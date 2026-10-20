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

### pytest plugin (FR-RUN-4)

`src/vizcontrol/pytest_plugin.py`, registered through the `pytest11` entry point, inactive unless `--vizcontrol` is
passed:

```
pytest tests/test_orders.py --vizcontrol [--vizcontrol-level=state] [--vizcontrol-fixtures] [--vizcontrol-dir DIR]
```

- One run per test item (`kind = pytest`, `target` = node id). By default the tracer wraps `pytest_runtest_call`
  (test body only). `--vizcontrol-fixtures` wraps the whole `pytest_runtest_protocol`, so fixture setup/teardown
  are traced too and the outcome is the worst of the three phases.
- Outcome: passed → `ok`, `AssertionError` → `assertion_failed`, `pytest.skip` → `skipped`, other errors →
  `raised`. pytest catches the exception in untraced frames, so it is recorded as escaped and its `assert` line
  becomes the failure point in the viewer. This covers logical errors that raise nothing of their own (vision §6).
- Files go to `<out_dir>/pytest-<time>/NNNN-<node id>.vizc` (relative to pytest's rootdir), or `--vizcontrol-dir`.
- The terminal summary prints how many tests were traced and failed, and the `vizcontrol serve <dir>` command.
- Tracebacks start at the first traced frame, so pytest/pluggy frames don't show.

### HTTP requests (FR-RUN-5, Later)

An ASGI middleware that wraps each request in `trace()`, enabled by sampling or a request header. It needs async
causality (FR-CAP-12) to be meaningful, so it is roadmap.

## Limits and failure modes

- Code executed before the runner starts (interpreter startup, `sitecustomize`) is not observed.
- `os.exec*` replaces the process: the run is closed as `unfinished` by `atexit` only if Python gets the chance.
  This is documented.
- Multiprocessing children are not traced in v1.
