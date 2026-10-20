"""The sys.monitoring tracer: turns interpreter events into call/binding/exception rows.

See docs/components/capture.md. Every callback is written so that it never raises into user code
(except KeyboardInterrupt and friends, which must keep working) and never keeps frames alive.
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
from types import BuiltinFunctionType, CodeType, FunctionType, MethodType, ModuleType

from vizcontrol.config import Config
from vizcontrol.serialize import Encoder, type_name
from vizcontrol.store import Writer

M = sys.monitoring
E = M.events
DISABLE = M.DISABLE
_now = time.perf_counter_ns
_get_ident = threading.get_ident
_MISSING = object()
_PKG_DIR = os.path.dirname(os.path.abspath(__file__)) + os.sep
_C_DESCRIPTORS = (types.MethodDescriptorType, types.WrapperDescriptorType, types.MethodWrapperType,
                  types.ClassMethodDescriptorType)
CO_OPTIMIZED, CO_VARARGS, CO_VARKEYWORDS, CO_GENERATOR, CO_COROUTINE, CO_ASYNC_GENERATOR = 0x01, 0x04, 0x08, 0x20, 0x80, 0x200


class Rec:
    """One open call on a thread's shadow stack. `frame` is id() of the frame: the callee's frame for Python
    calls, the caller's frame for opaque calls. Records with id None are 'skipped' (beyond max_depth)."""

    __slots__ = ("id", "frame", "depth", "opaque", "callable", "expect", "code_id", "written", "parent_id",
                 "tid", "line", "seq", "start")

    def __init__(self, id, frame, depth, opaque=False):
        self.id, self.frame, self.depth, self.opaque = id, frame, depth, opaque


class ExcEntry:
    __slots__ = ("obj", "id", "call_id", "line", "value_id", "handler", "done")

    def __init__(self, obj, id, call_id, line):
        self.obj, self.id, self.call_id, self.line = obj, id, call_id, line
        self.value_id = self.handler = None
        self.done = False  # caught; becomes final unless the same object is re-raised (with-blocks, bare raise)


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


class Tracer:
    def __init__(self, config: Config, writer: Writer, encoder: Encoder, roots: list[str]):
        self.cfg = config
        self.w = writer
        self.enc = encoder
        self.values = config.level != "calls"
        self.state = config.level == "state"
        self.max_depth = config.limits.max_depth
        self.max_calls = config.limits.max_calls
        self.max_items = config.values.max_items
        self._roots = tuple(os.path.abspath(r) + os.sep for r in roots)
        self._excluded = _excluded_prefixes()
        self._codes: dict[CodeType, int | None] = {}
        self._opaque_codes: dict[tuple, int] = {}
        self._files: dict[str, int] = {}
        self._stacks: dict[int, list[Rec]] = {}
        self._suspended: dict[int, Rec] = {}  # frame id of a suspended generator/coroutine -> its record
        self._exc: dict[int, ExcEntry] = {}  # in-flight exceptions by id(); they don't support weakrefs
        self._resolved: dict[int, tuple[int, type]] = {}  # id(exc) -> (row id, type), for __cause__ links
        self._caught: list[ExcEntry] = []  # caught but maybe re-raised; finalized lazily
        self._seq = iter(range(1, 1 << 62)).__next__
        self._main = _get_ident()
        self.calls = 0
        self.value_bytes = 0
        self.truncated: list[str] = []
        self.tool: int | None = None
        self._failed = False

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
        for stack in self._stacks.values():  # still open: left 'unfinished' (opaque ones must be written)
            for rec in stack:
                if rec.opaque and not rec.written:
                    self._write_opaque(rec)
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
        cid = self._codes.get(code, _MISSING)
        if cid is not _MISSING:
            return cid
        cid = None
        if self._included(code.co_filename, module):
            cid = self.w.new_id("code")
            self.w.add("code", (cid, self._file_id(code.co_filename), module, code.co_qualname,
                                code.co_firstlineno, _code_kind(code), 1))
        self._codes[code] = cid  # ponytail: keeps code objects alive; fine unless a program exec()s endlessly
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
        """Classify the target of a CALL: None if it is traced itself or builtin noise (len, list.append, ...),
        else (code key, names of a Python callee that would make the opaque node redundant)."""
        t = type(c)
        if t is MethodType:
            c = c.__func__
            t = type(c)
        if t is FunctionType:
            code = c.__code__
            cid = self._codes.get(code, _MISSING)
            if cid is _MISSING:
                cid = self._code_id(code, c.__module__)
            return None if cid is not None else ((c.__module__, c.__qualname__), ())
        if t is BuiltinFunctionType:
            owner = c.__self__
            if owner is None or type(owner) is ModuleType:
                module = c.__module__
                return None if module in (None, "builtins") else ((module, c.__qualname__), ())
            module = type(owner).__module__
            return None if module == "builtins" else ((module, c.__qualname__), ())
        if t in _C_DESCRIPTORS:
            owner = getattr(c, "__objclass__", None)
            return None if owner is None or owner.__module__ == "builtins" else ((owner.__module__, c.__qualname__), ())
        if isinstance(c, type):
            return None if c.__module__ == "builtins" else ((c.__module__, c.__qualname__), ("__init__", "__new__"))
        return (t.__module__, f"{t.__qualname__}.__call__"), ("__call__",)

    # -- stack helpers ----------------------------------------------------------------------------------------
    def _stack(self) -> list[Rec] | None:
        tid = _get_ident()
        stack = self._stacks.get(tid)
        if stack is None:
            if not self.cfg.threads and tid != self._main:
                return None
            stack = self._stacks[tid] = []
        return stack

    def _write_opaque(self, rec: Rec) -> None:
        rec.written = True
        self.w.add("call", (rec.id, rec.parent_id, rec.tid, rec.code_id, rec.line, rec.depth, rec.seq, None,
                            rec.start, None, "unfinished", None, 1))

    def _close_opaque(self, rec: Rec, outcome: str, exact: bool) -> None:
        seq, now = self._seq(), _now()
        if rec.written:
            self.w.close_call(rec.id, seq, now, outcome, None, int(exact))
        else:
            self.w.add("call", (rec.id, rec.parent_id, rec.tid, rec.code_id, rec.line, rec.depth, rec.seq, seq,
                                rec.start, now, outcome, None, int(exact)))

    def _settle(self, stack: list[Rec], fid: int, outcome: str = "ok") -> None:
        """An event in frame `fid` means opaque calls it made earlier have returned (end time is approximate)."""
        while stack and stack[-1].opaque and stack[-1].frame == fid:
            self._close_opaque(stack.pop(), outcome, False)

    def _pop(self, stack: list[Rec], fid: int, outcome: str = "ok") -> Rec | None:
        self._settle(stack, fid, outcome)
        if stack and stack[-1].frame == fid and not stack[-1].opaque:
            return stack.pop()
        for i in range(len(stack) - 2, -1, -1):  # records above it missed their end event
            rec = stack[i]
            if rec.frame == fid and not rec.opaque:
                for lost in stack[i + 1:]:
                    if lost.opaque:
                        self._close_opaque(lost, "unfinished", False)
                del stack[i:]
                return rec
        return None

    def _bind(self, call_id: int, seq: int, kind: str, name: str | None, line: int | None, obj) -> None:
        if not self.values:
            return
        e = self.enc.encode(obj, name)
        self.value_bytes += len(e.data)
        if self.value_bytes > self.cfg.limits.max_values_bytes:
            self.values = self.state = False  # degrade to 'calls' level
            self._gap(call_id, "limit_values", f"{self.value_bytes} bytes")
        vid = self.w.new_id("value")
        self.w.add("value", (vid, e.object_id, e.type, e.data, int(e.truncated), int(e.redacted)))
        self.w.add("binding", (call_id, seq, kind, name, line, vid))

    def _bind_locals(self, call_id: int, frame, seq: int, kind: str) -> None:
        for i, (name, value) in enumerate(frame.f_locals.items()):
            if i >= self.max_items:
                break
            self._bind(call_id, seq, kind, name, frame.f_lineno, value)

    # -- callbacks --------------------------------------------------------------------------------------------
    def _on_start(self, code, offset):
        try:
            frame = sys._getframe(1)
            cid = self._codes.get(code, _MISSING)
            if cid is _MISSING:
                cid = self._code_id(code, frame.f_globals.get("__name__"))
            if cid is None:
                return DISABLE
            stack = self._stack()
            if stack is None:
                return
            back = frame.f_back
            bid = id(back) if back is not None else None
            if stack:  # Class() -> __init__ / obj() -> __call__: the Python callee replaces the opaque node
                top = stack[-1]
                if top.opaque and top.frame == bid and not top.written and code.co_name in top.expect:
                    stack.pop()
            parent = stack[-1] if stack else None
            fid = id(frame)
            if parent is not None:
                if parent.id is None or parent.depth + 1 > self.max_depth:
                    if parent.id is not None and "limit_depth" not in self.truncated:
                        self._gap(parent.id, "limit_depth")
                    stack.append(Rec(None, fid, parent.depth + 1))
                    return
                if parent.opaque and not parent.written:
                    self._write_opaque(parent)
            if self.calls >= self.max_calls:
                self._gap(parent.id if parent else None, "limit_calls")
                self._disable()
                return
            self.calls += 1
            rec = Rec(self.w.new_id("call"), fid, parent.depth + 1 if parent else 0)
            stack.append(rec)
            seq = self._seq()
            line = back.f_lineno if parent is not None and back is not None and parent.frame == bid else None
            self.w.add("call", (rec.id, parent.id if parent else None, _get_ident(), cid, line, rec.depth, seq,
                                None, _now(), None, "unfinished", None, 1))
            if self.values:
                n = code.co_argcount + code.co_kwonlyargcount
                n += bool(code.co_flags & CO_VARARGS) + bool(code.co_flags & CO_VARKEYWORDS)
                if n:
                    local = frame.f_locals
                    for name in code.co_varnames[:n]:
                        if name in local:
                            self._bind(rec.id, seq, "arg", name, None, local[name])
        except Exception as e:
            self._fail(e)

    def _on_resume(self, code, offset):
        try:
            if self._codes.get(code) is None:
                return DISABLE if code in self._codes else None
            self._resume(id(sys._getframe(1)))
        except Exception as e:
            self._fail(e)

    def _on_throw(self, code, offset, exc):
        try:
            if self._codes.get(code) is not None:
                self._resume(id(sys._getframe(1)))
        except Exception as e:
            self._fail(e)

    def _resume(self, fid: int) -> None:
        rec = self._suspended.pop(fid, None)
        stack = self._stack()
        if rec is not None and stack is not None:
            stack.append(rec)

    def _on_return(self, code, offset, retval):
        try:
            cid = self._codes.get(code, _MISSING)
            if cid is None:
                return DISABLE
            if cid is _MISSING:
                return
            stack = self._stack()
            if not stack:
                return
            frame = sys._getframe(1)
            rec = self._pop(stack, id(frame))
            if rec is None or rec.id is None:
                return
            seq = self._seq()
            if self.values:
                self._bind(rec.id, seq, "return", None, frame.f_lineno, retval)
                if self.state:
                    self._bind_locals(rec.id, frame, seq, "local")
            self.w.close_call(rec.id, seq, _now(), "ok")
        except Exception as e:
            self._fail(e)

    def _on_yield(self, code, offset, value):
        try:
            cid = self._codes.get(code, _MISSING)
            if cid is None:
                return DISABLE
            if cid is _MISSING:
                return
            stack = self._stack()
            if not stack:
                return
            frame = sys._getframe(1)
            fid = id(frame)
            rec = self._pop(stack, fid)
            if rec is None:
                return
            self._suspended[fid] = rec  # stays open; PY_RESUME pushes it back
            if rec.id is not None and self.values:
                self._bind(rec.id, self._seq(), "yield", None, frame.f_lineno, value)
        except Exception as e:
            self._fail(e)

    def _on_unwind(self, code, offset, exc):
        try:
            if not self._codes.get(code):
                return
            stack = self._stack()
            if not stack:
                return
            frame = sys._getframe(1)
            rec = self._pop(stack, id(frame), "raised")
            if rec is None or rec.id is None:
                return
            seq = self._seq()
            if self.state:
                self._bind_locals(rec.id, frame, seq, "local")
            entry = self._exc.get(id(exc))
            self.w.close_call(rec.id, seq, _now(), "raised", entry.id if entry and entry.obj is exc else None)
        except Exception as e:
            self._fail(e)

    def _on_raise(self, code, offset, exc):
        try:
            if not self._codes.get(code):
                return
            stack = self._stack()
            if stack is None:
                return
            frame = sys._getframe(1)
            fid = id(frame)
            self._settle(stack, fid, "raised")
            entry = self._exc.get(id(exc))
            if entry is not None and entry.obj is exc:
                entry.done = False  # propagating through this frame or re-raised: recorded at its origin
                return
            rec = stack[-1] if stack and stack[-1].frame == fid and not stack[-1].opaque else None
            call_id = rec.id if rec else None
            entry = self._exc[id(exc)] = ExcEntry(exc, self.w.new_id("exception"), call_id, frame.f_lineno)
            if self.values:
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
            stack = self._stack() or []
            handler = None
            if self._codes.get(code):
                fid = id(sys._getframe(1))
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
        tb, frames = exc.__traceback__, []
        while tb is not None:
            co = tb.tb_frame.f_code
            if not (co.co_filename.startswith(_PKG_DIR) or co.co_filename.startswith("<frozen runpy")):
                frames.append([co.co_filename, tb.tb_lineno, co.co_qualname])
            tb = tb.tb_next
        cause = exc.__cause__ or exc.__context__
        hit = self._resolved.get(id(cause)) if cause is not None else None
        cause_id = hit[0] if hit and hit[1] is type(cause) else None
        message, _ = self.enc.scrub(_message(exc)[: self.cfg.values.max_str])
        self.w.add("exception", (entry.id, type_name(type(exc)), message, entry.call_id, entry.line,
                                 handled_call_id, cause_id, json.dumps(frames), entry.value_id))
        self._resolved[id(exc)] = (entry.id, type(exc))
        entry.obj = None

    def _on_call(self, code, offset, callable_, arg0):
        try:
            cid = self._codes.get(code, _MISSING)
            frame = sys._getframe(1)
            if cid is _MISSING:
                cid = self._code_id(code, frame.f_globals.get("__name__"))
            if cid is None:
                return DISABLE
            stack = self._stack()
            if stack is None:
                return
            fid = id(frame)
            self._settle(stack, fid)
            target = self._callee(callable_)
            if target is None:
                return
            parent = stack[-1] if stack else None
            if parent is not None and parent.id is None:
                return
            rec = Rec(self.w.new_id("call"), fid, parent.depth + 1 if parent else 0, opaque=True)
            rec.callable, rec.expect, rec.code_id, rec.written = id(callable_), target[1], self._opaque_code_id(target[0]), False
            rec.parent_id, rec.tid, rec.line, rec.seq, rec.start = (
                parent.id if parent else None, _get_ident(), frame.f_lineno, self._seq(), _now())
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
                    and stack[-1].frame == id(sys._getframe(2)):
                self._close_opaque(stack.pop(), outcome, True)
        except Exception as e:
            self._fail(e)


def _message(exc: BaseException) -> str:
    """str(exc) without running user __str__ methods (on the exception or on its args)."""
    args = BaseException.args.__get__(exc)
    if all(type(a) in (str, int, float, bool, type(None), bytes) for a in args):
        return BaseException.__str__(exc)
    return ", ".join(type_name(type(a)) for a in args)
