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
  (`DEBUGGER_ID`) and coverage (`COVERAGE_ID`).
- Global events: `PY_START`, `PY_RESUME`, `PY_RETURN`, `PY_YIELD`, `PY_UNWIND`, `PY_THROW`, `RAISE`, `RERAISE`,
  `EXCEPTION_HANDLED`, plus `CALL`, `C_RETURN` and `C_RAISE` when opaque calls are on.
- Local events: `LINE`, only on code objects selected for per-line capture
  (`sys.monitoring.set_local_events`).

### Filtering (FR-CAP-7, NFR-PERF-1)

The first `PY_START` of a code object resolves `code.co_filename` against the filters and caches the decision
in a `dict[CodeType, int | None]` (code row id, or `None` if excluded).

- Excluded → the callback returns `sys.monitoring.DISABLE`, so the interpreter stops calling it for that code
  object.
- Events that cannot be disabled (`RAISE`, `PY_UNWIND`, `EXCEPTION_HANDLED`, `C_RETURN`, ...) do one dict lookup
  and return early for excluded code.

Default filters: include everything under the run's project root (cwd, or the target's directory). Exclude
`sys.prefix`/`sys.base_prefix` (stdlib), `site-packages`/`dist-packages`, frozen and `<string>` code, and
VizControl itself. Users add `include`/`exclude` globs on module names or paths. Excludes win over includes.

### Shadow stack and call tree (FR-CAP-1/2/3)

- One stack per thread (`threading.get_ident()` → list of open call records). The top of the stack is the parent
  of the next included call. This yields `parent_id`, `depth` and `call_line` (from the caller frame's
  `f_lineno`).
- `PY_START`: push and write `call` row (outcome `unfinished` until closed); `seq` from a global counter.
- `PY_RETURN`: pop, set `end_ns`, `seq_end`, `outcome = ok`, capture the return value.
- `PY_UNWIND`: pop, `outcome = raised`, link the in-flight exception.
- Generators/coroutines: `PY_YIELD` pops but keeps the record open; `PY_RESUME` pushes the same record again. One
  generator invocation is one call node; calls during any resume are its children. Async task causality is
  FR-CAP-12 (Later).
- Frames are obtained with `sys._getframe(1)` inside the callback. No frame reference is stored past the
  callback.

### Opaque calls (FR-CAP-8)

When included code calls something that is not traced (a C function, an excluded Python function), the `CALL`
event (fired in the caller) records an opaque call node with the callable's qualified name:

- C callables close exactly on `C_RETURN`/`C_RAISE`.
- Excluded Python callables have no own events. The node closes at the next event observed in the caller's frame.
  Its `end_ns` is then an upper bound, and the node is marked so the UI can show the duration as approximate.
- Included code called back from inside an opaque call (framework → user handler) becomes its child. This keeps
  "the library called my function" visible.

Opaque calls are on by default and can be turned off (`opaque_calls = false`) for lower overhead.

### Values and snapshots (FR-CAP-4/5, FR-CAP-9)

| Level | Records |
|---|---|
| `calls` | call tree, timing, outcome, exception type/message |
| `values` (default) | + arguments at `PY_START`, return value, exception object |
| `state` | + locals snapshot at return and at unwind; per-line locals for functions listed in `trace_lines` |

Arguments are read from `frame.f_locals` using the first `co_argcount + co_kwonlyargcount (+ *args, **kwargs)`
names of `co_varnames`. Every value goes through the `Encoder` before being queued. Snapshots skip names already
captured with an identical encoding in the same call (dedup by hash) to save space.

### Exceptions (FR-CAP-6)

- `RAISE`: if this exception object has not been seen, write an `exception` row for the current call and line.
  Store a weak mapping `id(exc) → exception_id`, cleared when the exception is handled or escapes.
- `RERAISE` and propagation via `PY_UNWIND` mark each unwound call `raised` with the same `exception_id`.
- `EXCEPTION_HANDLED` sets `handled_call_id`.
- At stop, an exception still in flight means it escaped the run.
- `__cause__`/`__context__` chains are linked through `cause_id`. The full traceback, including frames not
  observed, is stored as JSON.

### Limits (FR-CAP-10)

`max_calls`, `max_depth`, `max_values_bytes`, `max_snapshots_per_call`, `max_lines`. When a limit is hit:

1. A `gap` row is written with the kind and the current `seq`.
2. Capture degrades: for example, past `max_values_bytes` it falls back to `calls` level, and past `max_depth` it
   stops creating nodes below that depth but still records exceptions crossing it.
3. `run.truncated` lists the limits hit.

Capture never silently drops data without a `gap` row.

### Threads (FR-CAP-11)

Callbacks fire on every thread, and each thread has its own stack. Calls carry `thread_id`. A thread's first
included call becomes a root (`parent_id = NULL`) unless the thread was started from an included call. In that
case it is linked as a child of the call that ran `Thread.start()`, marked inferred.

### Safety (NFR-SAFE-1)

- Every callback body runs under `try/except BaseException`. On an internal error the tracer writes a
  `tracer_error` gap, disables all events, warns once on stderr and lets the program continue.
- A thread-local re-entrancy flag stops VizControl's own code (encoder, writer) from being traced.
- No strong references to user objects outlive a callback. Object ids use `id()` plus a `weakref.finalize` or
  generation counter (see [serialization](serialization.md)).

## Config knobs

`level`, `include`, `exclude`, `opaque_calls`, `trace_lines`, `threads`, `max_calls`, `max_depth`,
`max_values_bytes`, `max_snapshots_per_call`, `max_lines`. See [CLI](cli.md#configuration).

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
