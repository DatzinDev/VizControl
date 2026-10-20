"""The sys.monitoring tracer: turns interpreter events into call/binding/exception rows.

See docs/components/capture.md. Every callback is written so that it never raises into user code
(except KeyboardInterrupt and friends, which must keep working) and never keeps frames alive.
The hot paths (start/return/call) are deliberately inlined: each event costs a few hundred nanoseconds.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import site
import sys
import sysconfig
import threading
import time
import types
from contextvars import ContextVar
from types import BuiltinFunctionType, CodeType, FunctionType, MethodType

from vizcontrol.config import Config
from vizcontrol.serialize import Encoder, type_name
from vizcontrol.store import Writer

M = sys.monitoring
E = M.events
DISABLE = M.DISABLE
_now = time.perf_counter_ns
_get_ident = threading.get_ident
_getframe = sys._getframe
_MISSING = object()
_PKG_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep
_C_DESCRIPTORS = (types.MethodDescriptorType, types.WrapperDescriptorType, types.MethodWrapperType,
                  types.ClassMethodDescriptorType)
# The innermost traced call of the running context, as (call id, depth, tracer). asyncio copies the context into
# every task it creates, and executors into their jobs, so the first call of a task finds who created it: its
# logical caller. The tracer tells a link of this run from a stale one left by an earlier run.
CALLER: ContextVar[tuple | None] = ContextVar("vizcontrol_caller", default=None)
# The run a scoped trace() belongs to (one request among concurrent ones): set in that context only.
SCOPE: ContextVar[object] = ContextVar("vizcontrol_scope", default=None)

# How generators and coroutines end and hand back results: `await` catches a StopIteration in C, with no
# EXCEPTION_HANDLED event, so it would look like an exception that escaped. They are control flow, not failures.
_CONTROL_FLOW = (StopIteration, StopAsyncIteration, GeneratorExit)

CO_OPTIMIZED, CO_VARARGS, CO_VARKEYWORDS, CO_GENERATOR, CO_COROUTINE, CO_ASYNC_GENERATOR = (
    0x01, 0x04, 0x08, 0x20, 0x80, 0x200)


class Rec:
    """One open call on a thread's shadow stack. `frame` is id() of the frame: the callee's frame for Python
    calls, the caller's frame for opaque calls. Records with id None are 'skipped' (beyond max_depth).
    The row is written once, when the call ends, unless a flush happens while it is open (`written`)."""

    __slots__ = ("id", "frame", "depth", "opaque", "parent_id", "tid", "code_id", "line", "seq", "start",
                 "children", "written", "callable", "expect", "valued", "yields")

    def __init__(self, id, frame, depth, opaque, parent_id, tid, code_id, line, seq, start):
        self.id, self.frame, self.depth, self.opaque, self.parent_id = id, frame, depth, opaque, parent_id
        self.tid, self.code_id, self.line, self.seq, self.start = tid, code_id, line, seq, start
        self.children = 0
        self.written = False
        self.callable = None
        self.expect = ()
        self.valued = True  # its values are recorded (see Values.max_per_function)
        self.yields = 0


class ExcEntry:
    __slots__ = ("obj", "id", "call_id", "line", "value_id", "handler", "done", "light")

    def __init__(self, obj, id, call_id, line):
        self.obj, self.id, self.call_id, self.line = obj, id, call_id, line
        self.value_id = self.handler = None
        self.done = False  # caught; becomes final unless the same object is re-raised (with-blocks, bare raise)
        self.light = False  # past its function's first exceptions: no value, and not written if caught where raised


def _code_kind(code: CodeType) -> str:
    f = code.co_flags
    if f & CO_ASYNC_GENERATOR or f & CO_COROUTINE:
        return "coroutine"
    if f & CO_GENERATOR:
        return "comprehension" if code.co_name == "<genexpr>" else "generator"
    if code.co_name == "<module>":
        return "module"
    if not f & CO_OPTIMIZED:  # the only other unoptimized code objects are class bodies
        return "class"
    if code.co_name == "<lambda>":
        return "lambda"
    if code.co_name.startswith("<"):
        return "comprehension"
    if code.co_varnames[:1] in (("self",), ("cls",)) and code.co_argcount:
        return "method"
    return "function"


def _glob(pattern: str, path: str, module: str | None) -> bool:
    if "/" in pattern or os.sep in pattern or pattern.endswith(".py"):
        return fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(os.path.relpath(path), pattern)
    if module is None:
        return False
    return fnmatch.fnmatch(module, pattern) or (pattern.endswith(".*") and module == pattern[:-2])


def _excluded_prefixes() -> tuple[str, ...]:
    paths = {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix, *site.getsitepackages(),
             site.getusersitepackages()}
    paths.update(sysconfig.get_paths()[k] for k in ("stdlib", "platstdlib", "purelib", "platlib"))
    return tuple(os.path.abspath(p) + os.sep for p in paths if p) + (_PKG_DIR,)


def _message(exc: BaseException, library_str: bool) -> str:
    """str(exc) without running the project's own __str__ methods (on the exception or on its args). A library's
    exception (`library_str`: its __str__ is not project code) gets its own __str__, which is where pydantic and
    similar put the message."""
    args = BaseException.args.__get__(exc)
    simple = all(type(a) in (str, int, float, bool, type(None), bytes) for a in args)
    if library_str and (not simple or not args) and type(exc).__str__ is not BaseException.__str__:
        try:
            return str(exc)
        except Exception:
            pass
    if simple:
        return BaseException.__str__(exc)
    return ", ".join(type_name(type(a)) for a in args)


class Tracer:
    def __init__(self, config: Config, writer: Writer, encoder: Encoder, roots: list[str], flush_every: int = 20_000):
        self.cfg = config
        self.w = writer
        self.enc = encoder
        self.values = config.level != "calls"
        self.state = config.level == "state"
        self.max_depth = config.limits.max_depth
        self.max_calls = config.limits.max_calls
        self.max_items = config.values.max_items
        self.max_per_function = config.values.max_per_function
        self._per_function: dict[int, int] = {}  # code id -> calls so far
        self._raised_in: dict[int, int] = {}  # code id -> exceptions raised in it so far
        self._stored: dict[tuple, int] = {}  # encoded value -> its row: a value seen again is not stored again
        self._stored_bytes = 0
        self.flush_every = flush_every
        self._roots = tuple(os.path.abspath(r) + os.sep for r in roots)
        self._excluded = _excluded_prefixes()
        # id(code) -> code row id, or None if excluded. Not the code itself: equal code objects in two files (the
        # same small function written twice) compare and hash alike. `_alive` keeps the ids from being reused.
        self._codes: dict[int, int | None] = {}
        self._alive: list[CodeType] = []
        self._opaque_codes: dict[tuple, int] = {}
        self._files: dict[str, int] = {}
        self._stacks: dict[int, list[Rec]] = {}
        self._suspended: dict[int, Rec] = {}  # frame id of a suspended generator/coroutine -> its record
        self._exc: dict[int, ExcEntry] = {}  # in-flight exceptions by id(); they don't support weakrefs
        self._caught: list[ExcEntry] = []  # caught but maybe re-raised; finalized lazily
        self._resolved: dict[int, tuple[int, type]] = {}  # id(exc) -> (row id, type), for __cause__ links
        self._seq = iter(range(1, 1 << 62)).__next__
        self._new_call_id = lambda: writer.new_id("call")
        self._main = _get_ident()
        self.calls = 0
        self.value_bytes = 0
        self.truncated: list[str] = []
        self.tool: int | None = None
        self._failed = False
        self.scoped = False  # record only code running in this run's context (SCOPE), not the rest of the process

    # -- lifecycle --------------------------------------------------------------------------------------------
    def _callbacks(self) -> dict[int, object]:
        cbs = {E.PY_START: self._on_start, E.PY_RESUME: self._on_resume, E.PY_THROW: self._on_throw,
               E.PY_RETURN: self._on_return, E.PY_YIELD: self._on_yield, E.PY_UNWIND: self._on_unwind,
               E.RAISE: self._on_raise, E.RERAISE: self._on_raise, E.EXCEPTION_HANDLED: self._on_handled}
        if self.cfg.opaque_calls:
            cbs.update({E.CALL: self._on_call, E.C_RETURN: self._on_c_return, E.C_RAISE: self._on_c_raise})
        return cbs

    def start(self) -> None:
        for tool in (M.PROFILER_ID, 3, 4):
            try:
                M.use_tool_id(tool, "vizcontrol")
                break
            except ValueError:
                continue
        else:
            raise RuntimeError("no free sys.monitoring tool id (another profiler/debugger is using them)")
        self.tool = tool
        mask = 0
        for event, cb in self._callbacks().items():
            M.register_callback(tool, event, cb)
            mask |= event
        M.restart_events()  # forget DISABLEs from a previous run with a different config
        M.set_events(tool, mask)

    def stop(self) -> None:
        if self.tool is None:
            return
        tool, self.tool = self.tool, None
        M.set_events(tool, 0)
        for event in self._callbacks():
            M.register_callback(tool, event, None)
        M.free_tool_id(tool)
        # calls still open (killed thread, suspended generator, the program ended inside them) stay 'unfinished'
        for rec in [r for s in self._stacks.values() for r in s] + list(self._suspended.values()):
            if rec.id is not None:
                self._end(rec, None, None, "unfinished", None, 1)
        for entry in list(self._exc.values()):  # caught, or escaped the traced region (handler None)
            self._write_exc(entry, entry.handler if entry.done else None)
        self._exc.clear()

    def _disable(self) -> None:
        if self.tool is not None:
            M.set_events(self.tool, 0)

    def _fail(self, error: BaseException) -> None:
        if self._failed:
            return
        self._failed = True
        self._disable()
        self._gap(None, "tracer_error", f"{type(error).__name__}: {error}")
        print(f"vizcontrol: internal error, tracing stopped ({type(error).__name__}: {error})", file=sys.stderr)

    def mark(self, name: str) -> list:
        """A nested trace() starts: where (the innermost traced call of this thread) and when. Written at unmark.
        ponytail: a region still open when the process dies is not recorded."""
        stack = self._stacks.get(_get_ident()) or []
        call_id = next((r.id for r in reversed(stack) if r.id is not None), None)
        return [self.w.new_id("region"), name, call_id, self._seq()]

    def unmark(self, region: list) -> None:
        self.w.add("region", (*region, self._seq()))

    def _gap(self, call_id, kind: str, detail: str | None = None) -> None:
        if kind not in self.truncated:
            self.truncated.append(kind)
        self.w.add("gap", (self.w.new_id("gap"), call_id, self._seq(), kind, detail))

    # -- registration -----------------------------------------------------------------------------------------
    def _included(self, filename: str, module: str | None) -> bool:
        if filename.startswith("<"):
            return False
        path = os.path.abspath(filename)
        if path.startswith(self._excluded):
            return False
        if any(_glob(p, path, module) for p in self.cfg.exclude):
            return False
        if self.cfg.include:
            return any(_glob(p, path, module) for p in self.cfg.include)
        return path.startswith(self._roots)

    def _code_id(self, code: CodeType, module: str | None) -> int | None:
        cid = self._codes.get(id(code), _MISSING)
        if cid is not _MISSING:
            return cid
        cid = None
        if self._included(code.co_filename, module):
            if not isinstance(module, str):  # templates compiled to Python (Jinja): named after their path
                path = os.path.abspath(code.co_filename)
                root = next((r for r in self._roots if path.startswith(r)), None)
                module = os.path.splitext(path[len(root):])[0].replace(os.sep, ".") if root else None
            cid = self.w.new_id("code")
            self.w.add("code", (cid, self._file_id(code.co_filename), module, code.co_qualname,
                                code.co_firstlineno, _code_kind(code), 1))
        self._codes[id(code)] = cid
        self._alive.append(code)  # ponytail: keeps code objects alive; fine unless a program exec()s endlessly
        return cid

    def _file_id(self, filename: str) -> int:
        path = os.path.abspath(filename)
        fid = self._files.get(path)
        if fid is None:
            fid = self._files[path] = self.w.new_id("file")
            source = digest = None
            try:
                with open(path, "rb") as f:
                    raw = f.read()
                digest = hashlib.sha256(raw).hexdigest()
                if self.cfg.store_source:
                    source = raw.decode("utf-8", "replace")
            except OSError:
                pass
            rel = os.path.relpath(path)
            self.w.add("file", (fid, path, None if rel.startswith("..") else rel, digest, source))
        return fid

    def _opaque_code_id(self, key: tuple[str | None, str]) -> int:
        cid = self._opaque_codes.get(key)
        if cid is None:
            cid = self._opaque_codes[key] = self.w.new_id("code")
            self.w.add("code", (cid, None, key[0], key[1], None, "external", 0))
        return cid

    def _callee(self, c) -> tuple[tuple, tuple[str, ...]] | None:
        """Classify the target of a CALL: None if it is traced itself or a C function/method (len, re.match,
        list.append: per-element noise whose time stays in the caller's self time), else (code key, names of a
        Python callee that would make the opaque node redundant)."""
        t = type(c)
        if t is MethodType:
            c = c.__func__
            t = type(c)
        if t is FunctionType:
            code = c.__code__
            cid = self._codes.get(id(code), _MISSING)
            if cid is _MISSING:
                cid = self._code_id(code, c.__module__)
            if cid is not None or code.co_filename.startswith(_PKG_DIR):  # traced itself, or our own machinery
                return None
            return (c.__module__, c.__qualname__), ()
        if t is BuiltinFunctionType or t in _C_DESCRIPTORS:
            return None
        if isinstance(c, type):
            return None if c.__module__ == "builtins" else ((c.__module__, c.__qualname__), ("__init__", "__new__"))
        return (t.__module__, f"{t.__qualname__}.__call__"), ("__call__",)

    # -- records ----------------------------------------------------------------------------------------------
    def _new_stack(self) -> list[Rec] | None:
        tid = _get_ident()
        if not self.cfg.threads and tid != self._main:
            return None
        stack = self._stacks[tid] = []
        return stack

    def _write_open(self, rec: Rec) -> None:
        rec.written = True
        self.w.add("call", (rec.id, rec.parent_id, rec.tid, rec.code_id, rec.line, rec.depth, rec.seq, None,
                            rec.start, None, "unfinished", None, 1, rec.children))

    def _end(self, rec: Rec, seq, now, outcome: str, exc_id, exact: int) -> None:
        if rec.written:
            self.w.close_call(rec.id, seq, now, outcome, exc_id, exact, rec.children)
        else:
            self.w.add("call", (rec.id, rec.parent_id, rec.tid, rec.code_id, rec.line, rec.depth, rec.seq, seq,
                                rec.start, now, outcome, exc_id, exact, rec.children))

    def _flush(self, stack: list[Rec]) -> None:
        # Open calls of this thread are written now, so a killed process still has its ancestors on disk.
        # ponytail: other threads' open calls are written on their own flushes or at stop
        for rec in stack:
            if rec.id is not None and not rec.written:
                self._write_open(rec)
        self.w.flush()

    def _settle(self, stack: list[Rec], fid: int, outcome: str = "ok") -> None:
        """An event in frame `fid` means opaque calls it made earlier have returned (end time is approximate)."""
        while stack and stack[-1].opaque and stack[-1].frame == fid:
            self._end(stack.pop(), self._seq(), _now(), outcome, None, 0)

    def _pop(self, stack: list[Rec], fid: int, outcome: str = "ok") -> Rec | None:
        self._settle(stack, fid, outcome)
        if stack and stack[-1].frame == fid and not stack[-1].opaque:
            return stack.pop()
        for i in range(len(stack) - 2, -1, -1):  # records above it missed their end event
            rec = stack[i]
            if rec.frame == fid and not rec.opaque:
                for lost in stack[i + 1:]:
                    if lost.id is not None:
                        self._end(lost, None, None, "unfinished", None, 0)
                del stack[i:]
                return rec
        return None

    def _bind(self, call_id: int, seq: int, kind: str, name: str | None, line: int | None, obj) -> None:
        if not self.values:
            return
        e = self.enc.encode(obj, name)
        key = (e.object_id, e.type, e.data, e.truncated, e.redacted)
        vid = self._stored.get(key)
        if vid is None:
            self.value_bytes += len(e.data)
            if self.value_bytes > self.cfg.limits.max_values_bytes:
                self.values = self.state = False  # degrade to 'calls' level
                self._gap(call_id, "limit_values", f"{self.value_bytes} bytes")
            vid = self._stored[key] = self.w.new_id("value")
            self.w.add("value", (vid, e.object_id, e.type, e.data, int(e.truncated), int(e.redacted)))
            self._stored_bytes += len(e.data)
            # ponytail: forget all at once; an LRU if it ever matters
            if self._stored_bytes > 32 * 1024 * 1024 or len(self._stored) > 100_000:
                self._stored.clear()
                self._stored_bytes = 0
        self.w.add("binding", (call_id, seq, kind, name, line, vid))

    def _bind_locals(self, call_id: int, frame, seq: int, kind: str) -> None:
        for i, (name, value) in enumerate(frame.f_locals.items()):
            if i >= self.max_items:
                break
            self._bind(call_id, seq, kind, name, frame.f_lineno, value)

    # -- callbacks --------------------------------------------------------------------------------------------
    def _on_start(self, code, offset):
        try:
            frame = _getframe(1)
            cid = self._codes.get(id(code), _MISSING)
            if cid is _MISSING:
                cid = self._code_id(code, frame.f_globals.get("__name__"))
            if cid is None:
                return DISABLE
            stack = self._stacks.get(_get_ident())
            if stack is None:
                stack = self._new_stack()
                if stack is None:
                    return
            bid = id(frame.f_back)
            parent = stack[-1] if stack else None
            # Class() -> __init__, obj() -> __call__: the Python callee replaces the pending opaque node
            if parent is not None and parent.opaque and parent.frame == bid and not parent.written \
                    and code.co_name in parent.expect:
                stack.pop()
                parent = stack[-1] if stack else None
                if parent is not None:
                    parent.children -= 1
            link = CALLER.get()
            if parent is None or parent.opaque or parent.id is None or link is None or link[0] != parent.id:
                # Off the fast path (the stack's top call is the context's caller): check the context.
                if link is not None and link[2] is not self:
                    link = None  # left by an earlier run
                caller = next((r.id for r in reversed(stack) if not r.opaque and r.id is not None), None)
                if (link[0] if link else None) != caller:
                    # The running context is not the one this thread's stack was built in: a task or an executor
                    # job. Its context says which call started it (asyncio.run's own frame may still be on the stack).
                    parent = None
                if (caller is None or parent is None) and self.scoped and SCOPE.get() is not self:
                    return  # another request or job running beside this run's: not part of it
            if parent is None:
                depth, parent_id, line = (link[1] + 1, link[0], None) if link else (0, None, None)
            else:
                if parent.id is None or parent.depth >= self.max_depth:
                    if parent.id is not None and "limit_depth" not in self.truncated:
                        self._gap(parent.id, "limit_depth")
                    stack.append(Rec(None, id(frame), parent.depth + 1, False, None, 0, 0, None, 0, 0))
                    return
                if parent.opaque and not parent.written:
                    self._write_open(parent)  # it has children: it must exist before them on disk
                parent.children += 1
                depth, parent_id = parent.depth + 1, parent.id
                line = frame.f_back.f_lineno if parent.frame == bid else None
            if self.calls >= self.max_calls:
                self._gap(parent_id, "limit_calls")
                self._disable()
                return
            self.calls += 1
            rec = Rec(self._new_call_id(), id(frame), depth, False, parent_id, _get_ident(), cid, line,
                      self._seq(), _now())
            stack.append(rec)
            CALLER.set((rec.id, depth, self))
            if self.values:
                seen = self._per_function[cid] = self._per_function.get(cid, 0) + 1
                if seen > self.max_per_function:  # a hot function: its first calls show what its values look like
                    rec.valued = False
                    if seen == self.max_per_function + 1:
                        self._gap(rec.id, "limit_values_per_function", code.co_qualname)
            if self.values and rec.valued:
                n = code.co_argcount + code.co_kwonlyargcount
                n += bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
                if n:
                    local = frame.f_locals
                    for name in code.co_varnames[:n]:
                        if name in local:
                            self._bind(rec.id, rec.seq, "arg", name, None, local[name])
        except Exception as e:
            self._fail(e)

    def _on_return(self, code, offset, retval):
        try:
            cid = self._codes.get(id(code), _MISSING)
            if cid is None:
                return DISABLE
            if cid is _MISSING:
                return
            stack = self._stacks.get(_get_ident())
            if not stack:
                return
            frame = _getframe(1)
            fid = id(frame)
            rec = stack[-1]
            if rec.frame == fid and not rec.opaque:
                stack.pop()
            else:
                rec = self._pop(stack, fid)
                if rec is None:
                    return
            if rec.id is None:
                return
            CALLER.set((rec.parent_id, rec.depth - 1, self) if rec.parent_id is not None else None)
            now, seq = _now(), self._seq()
            if self.values and rec.valued:
                self._bind(rec.id, seq, "return", None, frame.f_lineno, retval)
                if self.state:
                    self._bind_locals(rec.id, frame, seq, "local")
                elif code.co_argcount and code.co_varnames[0] == "self":
                    # the receiver after a method ran: this is what makes `self.x = ...` changes visible
                    local = frame.f_locals
                    if "self" in local:
                        self._bind(rec.id, seq, "self", "self", frame.f_lineno, local["self"])
            if rec.written:
                self.w.close_call(rec.id, seq, now, "ok", None, 1, rec.children)
            else:
                self.w.add("call", (rec.id, rec.parent_id, rec.tid, rec.code_id, rec.line, rec.depth, rec.seq, seq,
                                    rec.start, now, "ok", None, 1, rec.children))
            if self.w.pending >= self.flush_every:
                self._flush(stack)
        except Exception as e:
            self._fail(e)

    def _on_call(self, code, offset, callable_, arg0):
        try:
            cid = self._codes.get(id(code), _MISSING)
            frame = _getframe(1)
            if cid is _MISSING:
                cid = self._code_id(code, frame.f_globals.get("__name__"))
            if cid is None:
                return DISABLE
            stack = self._stacks.get(_get_ident())
            if stack is None:
                stack = self._new_stack()
                if stack is None:
                    return
            if self.scoped and SCOPE.get() is not self:
                return  # made by another request or job running beside this run's
            fid = id(frame)
            if stack and stack[-1].opaque and stack[-1].frame == fid:
                self._settle(stack, fid)
            t = type(callable_)
            if t is FunctionType and self._codes.get(id(callable_.__code__)):
                return  # fast path: a traced Python function reports itself
            if t is BuiltinFunctionType or t in _C_DESCRIPTORS:
                # ponytail: a site that calls C once (re.match, list.append) is assumed to always do so
                return DISABLE
            target = self._callee(callable_)
            if target is None:
                return
            parent = stack[-1] if stack else None
            if parent is not None and parent.id is None:
                return
            rec = Rec(self._new_call_id(), fid, parent.depth + 1 if parent else 0, True,
                      parent.id if parent else None, _get_ident(), self._opaque_code_id(target[0]), frame.f_lineno,
                      self._seq(), _now())
            rec.callable, rec.expect = id(callable_), target[1]
            if parent is not None:
                parent.children += 1
            stack.append(rec)
        except Exception as e:
            self._fail(e)

    def _on_c_return(self, code, offset, callable_, arg0):
        self._c_end(callable_, "ok")

    def _on_c_raise(self, code, offset, callable_, arg0):
        self._c_end(callable_, "raised")

    def _c_end(self, callable_, outcome: str) -> None:
        try:
            stack = self._stacks.get(_get_ident())
            if stack and stack[-1].opaque and stack[-1].callable == id(callable_) \
                    and stack[-1].frame == id(_getframe(2)):
                self._end(stack.pop(), self._seq(), _now(), outcome, None, 1)
        except Exception as e:
            self._fail(e)

    def _on_resume(self, code, offset):
        try:
            if self._codes.get(id(code)) is None:
                return DISABLE if id(code) in self._codes else None
            self._resume(id(_getframe(1)))
        except Exception as e:
            self._fail(e)

    def _on_throw(self, code, offset, exc):
        try:
            if self._codes.get(id(code)) is not None:
                self._resume(id(_getframe(1)))
        except Exception as e:
            self._fail(e)

    def _resume(self, fid: int) -> None:
        rec = self._suspended.pop(fid, None)
        if rec is None:
            return
        stack = self._stacks.get(_get_ident())
        if stack is None:
            stack = self._new_stack()
        if stack is not None:
            stack.append(rec)
            if rec.id is not None:
                CALLER.set((rec.id, rec.depth, self))

    def _on_yield(self, code, offset, value):
        try:
            cid = self._codes.get(id(code), _MISSING)
            if cid is None:
                return DISABLE
            if cid is _MISSING:
                return
            stack = self._stacks.get(_get_ident())
            if not stack:
                return
            frame = _getframe(1)
            fid = id(frame)
            rec = stack[-1]
            if rec.frame == fid and not rec.opaque:
                stack.pop()
            else:
                rec = self._pop(stack, fid)
                if rec is None:
                    return
            gone = self._suspended.get(fid)
            if gone is not None and gone is not rec and gone.id is not None:
                # a generator dropped while suspended outside any try block is closed with no event (3.12+): its
                # frame's id now belongs to this one, so its record is written now, or never
                self._end(gone, None, None, "unfinished", None, 1)
            self._suspended[fid] = rec  # stays open; PY_RESUME pushes it back
            top = stack[-1] if stack else None  # back in the code that consumed it (or the event loop)
            CALLER.set((top.id, top.depth, self) if top is not None and top.id is not None else None)
            if rec.id is not None and self.values and rec.valued:
                rec.yields += 1
                if rec.yields <= self.max_per_function:  # a generator of a million items shows its first ones
                    self._bind(rec.id, self._seq(), "yield", None, frame.f_lineno, value)
        except Exception as e:
            self._fail(e)

    def _on_unwind(self, code, offset, exc):
        try:
            if not self._codes.get(id(code)):
                return
            stack = self._stacks.get(_get_ident())
            if not stack:
                return
            frame = _getframe(1)
            rec = self._pop(stack, id(frame), "raised")
            if rec is None or rec.id is None:
                return
            CALLER.set((rec.parent_id, rec.depth - 1, self) if rec.parent_id is not None else None)
            now, seq = _now(), self._seq()
            if self.state and rec.valued:
                self._bind_locals(rec.id, frame, seq, "local")
            entry = self._exc.get(id(exc))
            self._end(rec, seq, now, "raised", entry.id if entry and entry.obj is exc else None, 1)
        except Exception as e:
            self._fail(e)

    def _on_raise(self, code, offset, exc):
        try:
            if not self._codes.get(id(code)) or isinstance(exc, _CONTROL_FLOW):
                return
            stack = self._stacks.get(_get_ident())
            if stack is None:
                return
            frame = _getframe(1)
            fid = id(frame)
            self._settle(stack, fid, "raised")
            entry = self._exc.get(id(exc))
            if entry is not None and entry.obj is exc:
                entry.done = False  # propagating through this frame or re-raised: recorded at its origin
                return
            rec = stack[-1] if stack and stack[-1].frame == fid and not stack[-1].opaque else None
            call_id = rec.id if rec else None
            entry = self._exc[id(exc)] = ExcEntry(exc, self.w.new_id("exception"), call_id, frame.f_lineno)
            cid = self._codes[id(code)]
            raised = self._raised_in[cid] = self._raised_in.get(cid, 0) + 1
            if raised > self.max_per_function:  # exceptions as control flow (list.index in a loop)
                entry.light = True
                if raised == self.max_per_function + 1:
                    self._gap(call_id, "limit_exceptions_per_function", code.co_qualname)
            elif self.values:
                seq = self._seq()
                e = self.enc.encode(exc)
                entry.value_id = self.w.new_id("value")
                self.w.add("value", (entry.value_id, e.object_id, e.type, e.data, int(e.truncated), int(e.redacted)))
                if call_id is not None:  # locals at the origin: the most useful snapshot for debugging
                    self._bind_locals(call_id, frame, seq, "raise")
        except Exception as e:
            self._fail(e)

    def _on_handled(self, code, offset, exc):
        try:
            entry = self._exc.get(id(exc))
            if entry is None or entry.obj is not exc:
                return
            stack = self._stacks.get(_get_ident()) or []
            handler = None
            if self._codes.get(id(code)):
                fid = id(_getframe(1))
                self._settle(stack, fid, "raised")
                handler = next((r.id for r in reversed(stack) if r.frame == fid and not r.opaque), None)
            elif stack:  # caught inside a library called from traced code
                handler = stack[-1].id
            entry.handler, entry.done = handler, True
            self._caught.append(entry)
            if len(self._caught) > 64:  # ponytail: a re-raise after 64 other catches is recorded as a new exception
                self._finalize(self._caught[:32])
                del self._caught[:32]
        except Exception as e:
            self._fail(e)

    def _finalize(self, entries: list[ExcEntry]) -> None:
        for entry in entries:
            if entry.done and entry.obj is not None and self._exc.get(id(entry.obj)) is entry:
                del self._exc[id(entry.obj)]
                self._write_exc(entry, entry.handler)

    def _write_exc(self, entry: ExcEntry, handled_call_id: int | None) -> None:
        exc = entry.obj
        if entry.light and handled_call_id is not None and handled_call_id == entry.call_id:
            entry.obj = None  # raised and caught in one call, past its function's first: nothing refers to it
            return
        tb, frames = exc.__traceback__, []
        while tb is not None:
            co = tb.tb_frame.f_code
            # start at the first traced frame: the runner/pytest/pluggy frames above it are not the user's story
            if (frames or self._codes.get(id(co))) and not co.co_filename.startswith(_PKG_DIR):
                frames.append([co.co_filename, tb.tb_lineno, co.co_qualname])
            tb = tb.tb_next
        cause = exc.__cause__ or exc.__context__
        hit = self._resolved.get(id(cause)) if cause is not None else None
        cause_id = hit[0] if hit and hit[1] is type(cause) else None
        own = getattr(type(exc).__str__, "__code__", None)  # a Python __str__: is it the project's?
        library = own is None or not self._included(own.co_filename, type(exc).__module__)
        message, _ = self.enc.scrub(_message(exc, library)[: self.cfg.values.max_str])
        self.w.add("exception", (entry.id, type_name(type(exc)), message, entry.call_id, entry.line,
                                 handled_call_id, cause_id, json.dumps(frames), entry.value_id))
        self._resolved[id(exc)] = (entry.id, type(exc))
        entry.obj = None
