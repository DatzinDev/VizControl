# Capture engine

`src/vizcontrol/capture.py`

## Responsibility

Observe the running interpreter through `sys.monitoring` (PEP 669) and turn interpreter events into call, binding,
exception, line and gap records for the [storage writer](storage.md). It decides *what* is traced (filters,
level, limits). It does not decide *how values look* ([serialization](serialization.md)) or *how runs start*
([runners](runners.md)).

## Requirements covered

FR-CAP-1..11, FR-RUN-6 (no semantic change), NFR-PERF-1/2, NFR-SAFE-1, NFR-OBS.

## Interface

```python
class Tracer:
    def __init__(self, config: Config, writer: Writer, encoder: Encoder): ...
    def start(self) -> None   # claim a tool id, register callbacks, enable events
    def stop(self) -> None    # disable events, close open calls as 'unfinished', free tool id
```

`start()`/`stop()` are called by runners only. A process has at most one active `Tracer`. A second `start()` raises
`RuntimeError`.

## Internal design

### Tool id and events

- Claims `sys.monitoring.PROFILER_ID` and falls back to free ids 3 and 4. This coexists with debuggers
  (`DEBUGGER_ID`) and coverage (`COVERAGE_ID`). `restart_events()` at start drops `DISABLE`s left by a previous run.
- Global events: `PY_START`, `PY_RESUME`, `PY_RETURN`, `PY_YIELD`, `PY_UNWIND`, `PY_THROW`, `RAISE`, `RERAISE`,
  `EXCEPTION_HANDLED`, plus `CALL`, `C_RETURN` and `C_RAISE` when opaque calls are on.
- Per-line capture (`LINE` local events on selected functions, FR-CAP-5 Should) is not implemented yet.

### Filtering (FR-CAP-7, NFR-PERF-1)

The first event of a code object resolves `code.co_filename` (and the module from `f_globals["__name__"]`) against
the filters and caches the decision in a `dict[CodeType, int | None]` (code row id, or `None` if excluded).

- Excluded → the callback returns `sys.monitoring.DISABLE`, so the interpreter stops calling it for that code
  object.
- Events that cannot be disabled (`RAISE`, `PY_UNWIND`, `EXCEPTION_HANDLED`, `C_RETURN`, ...) do one dict lookup
  and return early for excluded code.

Default filters: include everything under the project root (cwd, plus the script's directory for script runs).
Exclude the stdlib, `sys.prefix`/`sys.base_prefix`/`exec_prefix`, site-packages, `<frozen ...>`/`<string>` code
and VizControl itself. Users add `include`/`exclude` globs on module names (`app.*` also matches `app`) or paths.
Excludes win over includes; a non-empty `include` replaces the project-root rule.

### Shadow stack and call tree (FR-CAP-1/2/3)

- One stack per thread (`threading.get_ident()` → list of open call records). The top of the stack is the parent
  of the next included call. This yields `parent_id`, `depth` and `call_line` (from the caller frame's
  `f_lineno`, only when the caller is the parent).
- Records are matched to their end events by `id(frame)`, which is unique while the frame runs. A record whose end
  event never came (the frame started before tracing, a missed event) is closed as `unfinished` when an
  ancestor ends, and an end event without a matching record is ignored.
- `PY_START`: push a record; `seq` from a global counter. `PY_RETURN`: pop, capture the return value, and write
  the **whole row once**, already closed. A row is written while still open only when a flush happens during the
  call (so a killed process keeps its ancestors); it is then closed with an `UPDATE`. `children` is counted in
  memory.
- `PY_UNWIND`: pop, `outcome = raised`, link the in-flight exception.
- Generators/coroutines: `PY_YIELD` pops but keeps the record open; `PY_RESUME`/`PY_THROW` push the same record
  again. One generator invocation is one call node; calls during any resume are its children.
- Async causality (FR-CAP-12): a `ContextVar` holds the innermost traced call of the running context, set on
  every push and pop. asyncio copies the context into each task it creates (`create_task`, `gather`), and so do
  `asyncio.to_thread` and anyio's worker threads, so a task's first call finds the call that created it, even after
  that call returned. When the context's caller is not the innermost traced call on the thread's stack, the code
  runs in another context (a task, a job), and the context wins: `gather`'s tasks hang under the coroutine that
  awaited them, not under `asyncio.run`. The common case (the stack's top is the context's caller) costs one
  `ContextVar.get`. `loop.run_in_executor` copies no context, so its jobs stay roots.
- Frames are obtained with `sys._getframe(1)` inside the callback. No frame reference is stored past the
  callback.

### Opaque calls (FR-CAP-8)

When included code calls something that is not traced, the `CALL` event (fired in the caller) records an opaque
call node with the callable's qualified name. This applies to excluded **Python** functions and methods (library
entry points such as `json.dumps` or `session.commit`), classes outside `builtins`, and callable instances.

- C functions and methods (`len`, `re.Pattern.match`, `list.append`, `time.sleep`) are **not** recorded. They are
  per-element noise and their time stays in the caller's self time. The `CALL` event is disabled at a call site the
  first time it calls C code. The assumption is that a site calling C always does; a site that later calls a
  Python library function there loses that opaque node.
- `Class()` and `obj()` push a pending opaque node. If the next event is the class's `__init__`/`__new__` (or the
  object's `__call__`) starting from the same caller frame, that traced callee replaces the opaque node.
- Calls to classes implemented in C close exactly on `C_RETURN`/`C_RAISE`.
- Excluded Python callables have no own events. The node closes at the next event observed in the caller's frame,
  its `end_ns` is an upper bound, and `end_exact = 0`.
- Included code called back from inside an opaque call (framework → user handler, `sorted(key=f)` excluded)
  becomes its child. This keeps "the library called my function" visible.

Opaque calls are on by default and can be turned off (`opaque_calls = false`) for lower overhead.

### Values and snapshots (FR-CAP-4/5, FR-CAP-9)

| Level | Records |
|---|---|
| `calls` | call tree, timing, outcome, exception type/message/traceback |
| `values` (default) | + arguments at `PY_START`, return value, `self` at a method's return (so `self.x = ...` changes are visible), yielded values, exception object, **locals at the frame where an exception originates** |
| `state` | + locals snapshot at return and at unwind |

Arguments are read from `frame.f_locals` using the first `co_argcount + co_kwonlyargcount (+ *args, **kwargs)`
names of `co_varnames`. Every value goes through the `Encoder` before being queued. Locals snapshots take at most
`values.max_items` names.

Real code repeats itself, and so do its values:
- An encoded value already stored (same object id, type and data) is not stored again: its binding points to the
  existing row. On real projects 90% or more of the bindings repeat a value. The lookup table is dropped after
  32 MiB of values or 100,000 of them.
- Only the first `values.max_per_function` calls (default 100) of each function record values. A hot function's
  later calls keep their structure, timing and outcome, but no arguments or return; the locals where an exception
  originates are still recorded. A generator call likewise records its first `max_per_function` yielded items. The first excess call of each function writes a `limit_values_per_function` gap
  with its qualname.

### Exceptions (FR-CAP-6)

`RAISE` fires in every frame an exception propagates through, so entries are keyed by `id(exc)` with a strong
reference while in flight (exceptions don't support weakrefs):

- `RAISE` of an unseen object creates the entry at the current call and line, with locals at that point.
  `RAISE`/`RERAISE` of a known object is propagation or re-raise and does nothing new.
- `PY_UNWIND` marks each unwound call `raised` with the same `exception_id`.
- `EXCEPTION_HANDLED` records the handler call, but the entry stays pending. `with` blocks and `except: ...; raise`
  catch and then re-raise the same object, and a `RERAISE` reopens the entry. Pending entries are written when
  more than 64 have accumulated or at stop.
- Caught in an untraced frame with nothing traced on the stack (the runner, the `trace()` block) = escaped,
  `handled_call_id = NULL`.
- Past the first `values.max_per_function` exceptions raised in a function (exceptions as control flow:
  `list.index` in a loop), the next ones get no value and no locals, and one raised and caught in the same call
  is not recorded at all. The first excess writes a `limit_exceptions_per_function` gap with the qualname.
- `StopIteration`, `StopAsyncIteration` and `GeneratorExit` are not recorded: they are how generators and
  coroutines end, and `await` catches the StopIteration that carries a result in C, with no `EXCEPTION_HANDLED`,
  so it would look escaped.
- The message never runs the project's own `__str__`: it is `BaseException.__str__` when all args are simple
  types. An exception whose `__str__` is a library's or builtin (pydantic's `ValidationError`, `subprocess`'s
  `CalledProcessError`) gets that `__str__` when its args are not simple or empty, since that is where the
  message is.
  `__cause__`/`__context__` link through `cause_id`. The traceback is stored as JSON without VizControl/runpy
  frames.

### Limits (FR-CAP-10)

`max_calls`, `max_depth`, `max_values_bytes`. When a limit is hit:

1. A `gap` row is written with the kind and the current `seq` (once per kind).
2. Capture degrades: past `max_values_bytes` it falls back to `calls` level. Past `max_depth` it pushes "skip"
   records so deeper calls are not written, while their returns still match. Past `max_calls` tracing stops.
3. `run.truncated` lists the limits hit.

Capture never silently drops data without a `gap` row.

### Threads (FR-CAP-11)

Callbacks fire on every thread, and each thread has its own stack. Calls carry `thread_id`. A thread's first
included call is a root (`parent_id = NULL`). `analysis.thread_starts` links it to the `Thread.start` call that
launched it (an opaque node in the spawning thread), inferred by time order: each root call outside the run's
first thread takes the earliest unmatched `Thread.start` that began before it. The key is the root call, not
`thread_id`: Python reuses a thread's ident once it ends. The call inspector shows it as "thread started by …", marked
inferred. `threads = false` traces the main thread only.

### Writes and flushing (NFR-PERF-2)

Rows are appended to the writer's in-memory batches. When more than `flush_every` (20,000) are pending, the
current thread's open calls are written and the batch is committed. Other threads' open calls are written on
their own flushes or at stop.

### Safety (NFR-SAFE-1)

- Every callback body runs under `try/except Exception`. On an internal error the tracer writes a `tracer_error`
  gap, disables all events, warns once on stderr and lets the program continue. `KeyboardInterrupt`/`SystemExit`
  are not caught, so they keep working.
- Callbacks are not re-entered: the interpreter does not fire events for code run inside a callback, and
  VizControl's own modules are excluded anyway.
- No frame references outlive a callback. In-flight exceptions are the only strong references kept, until
  resolved.

### Measured overhead

`benchmarks/overhead.py` on Python 3.13, x86-64 laptop:

| Workload | `calls` | `values` | `state` |
|---|---|---|---|
| app-like (traced code orchestrating json/re/sorting) | ≈1.0× | ≈1.0× | ≈1.1× |
| worst case (87k tiny traced functions) | 4.2 µs/call (≈60×) | 4.4 µs/call | 4.4 µs/call |

The worst case is pure per-call bookkeeping in Python: its few functions are called thousands of times, so past
their first 100 calls (`values.max_per_function`) they record no values. A C accelerator for the hot callbacks is
the upgrade path if it matters.

On real projects (Python 3.13): `black` formatting a 1,800-line file, about 1M calls, takes 15 s traced instead of
0.5 s and writes a 220 MB file; `rich` rendering a README, 110k calls, 4.5 s instead of 0.2 s and 37 MB; the Flask
tutorial's 24 tests, 1.1 s instead of 1.0 s; `more-itertools`' 765 tests, 127 s instead of 8 s and 675 MB of runs.
Generator-heavy code costs the most: every `yield` and resume of a traced generator runs a callback (about 1 µs),
even past its values cap, and one test there yields 8 million times.

At scale (a 250k-iteration loop of tiny functions, `values` level): 893k calls and 2.3M bindings in 17.7 s (the
program alone takes 0.3 s), 33 MB of memory, a 381 MB `.vizc` file.

## Config knobs

`level`, `include`, `exclude`, `opaque_calls`, `threads`, `limits.max_calls`, `limits.max_depth`,
`limits.max_values_bytes`. See [CLI](cli.md#configuration).

## Limits and failure modes

- Code that runs before the tracer starts (e.g. import-time side effects of the runner itself) is not observed.
- C extensions are opaque by nature. Callbacks from C into Python are observed as children of the opaque node.
- `sys.monitoring` is process-wide. Another tool that uses the same events with a different id is fine. If no
  tool id is free, `start()` fails with a clear error.
- Programs that `os.fork()` keep tracing only in the parent. Subprocesses are out of scope for v1.
- The `CALL` event fires for every call site in included code. It is the main overhead source and is measured in
  the benchmark suite.

## Open questions

- Should `max_depth` count only included frames or include opaque ones? Proposal: only included.
- Should per-line capture be selectable by line range rather than by whole function?
