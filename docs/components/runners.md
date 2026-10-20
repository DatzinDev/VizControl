# Runners

`src/vizcontrol/runners.py`, `src/vizcontrol/pytest_plugin.py`

## Responsibility

Start the user's code under capture with unchanged semantics, define run boundaries (one run = one `.vizc`),
and record the outcome.

## Requirements covered

FR-RUN-1..6, FR-PER-2/4.

## Entry points

### Script and module (FR-RUN-1/2/6)

```
vizcontrol run app.py --flag value
vizcontrol run -m package.cli --flag value
```

Implemented with `runpy.run_path(path, run_name="__main__")` / `runpy.run_module(name, run_name="__main__",
alter_sys=True)`, the same mechanism as `python file.py` / `python -m`. Before running:

- `sys.argv` is set to `[target, *args]`, and `sys.path[0]` to the script's directory (script) or cwd (module),
  as Python does.
- The tracer starts immediately before `runpy` so VizControl's own startup is not in the trace.

Outcome mapping:

| Program ends with | `run.outcome` | Process exit code |
|---|---|---|
| Normal return | `ok` | 0 |
| `SystemExit(n)` | `ok` if n in (0, None), else `raised` | n |
| Uncaught exception | `raised` (exception linked, escaped) | 1, traceback printed as Python would |
| `KeyboardInterrupt` / SIGTERM | `unfinished` | 130 / 143 |

The traced program's exit code is always the process exit code, so `vizcontrol run` can be used in scripts and CI.

### Python API (FR-RUN-3)

```python
import vizcontrol

with vizcontrol.trace("checkout", level="state") as run:   # explicit block
    place_order(cart)
print(run.path)                                            # .vizcontrol/runs/...-checkout.vizc

@vizcontrol.traced(level="values")                          # each call of the function is one run
def handle(event): ...
```

- `trace()` accepts the same options as the config file (keyword arguments override config).
- `trace()` re-raises any exception from the block after recording it, so it never swallows errors.
- Nesting: a `trace()`/`traced` inside an active run does not start a new run. It adds a marker so the region can
  be found in the viewer.
- `traced` on an `async def` function is Later (FR-CAP-12).

### pytest plugin (FR-RUN-4, Should)

Registered through the `pytest11` entry point, inactive unless `--vizcontrol` is passed:

```
pytest tests/test_orders.py::test_checkout --vizcontrol [--vizcontrol-level=state]
```

- One run per test item. The tracer starts in `pytest_runtest_call` (test body only, not fixtures). An option
  `--vizcontrol-fixtures` includes setup/teardown.
- Outcome: passed → `ok`, `AssertionError` → `assertion_failed` with the assertion exception linked, other errors
  → `raised`. The assertion line becomes the failure point in the viewer, which covers logical errors that raise no
  exception of their own (vision §6).
- `run.target` is the test node id. Files go to `.vizcontrol/runs/pytest-<session>/`.
- At session end the plugin prints the run directory and the `vizcontrol serve` command.

### HTTP requests (FR-RUN-5, Later)

An ASGI middleware that wraps each request in `trace()`, enabled by sampling or a request header. It needs async
causality (FR-CAP-12) to be meaningful, so it is roadmap.

## Limits and failure modes

- Code executed before the runner starts (interpreter startup, `sitecustomize`) is not observed.
- `os.exec*` replaces the process: the run is closed as `unfinished` by `atexit` only if Python gets the chance.
  This is documented.
- Multiprocessing children are not traced in v1.
