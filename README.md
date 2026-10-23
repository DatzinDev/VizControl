# VizControl

**See what your Python program actually did.** VizControl records a run and plays it back in your browser, step by
step, like a debugger after the fact: who called whom, with what, what came back, and where it failed.

[![PyPI](https://img.shields.io/pypi/v/vizcontrol)](https://pypi.org/project/vizcontrol/)
[![Python](https://img.shields.io/pypi/pyversions/vizcontrol)](https://pypi.org/project/vizcontrol/)
[![CI](https://github.com/DatzinDev/VizControl/actions/workflows/ci.yml/badge.svg)](https://github.com/DatzinDev/VizControl/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue)](https://github.com/DatzinDev/VizControl/blob/main/LICENSE)

![A failing ETL run played back: the call graph lights up module by module, the transcript says who called whom, and the run stops on the KeyError that broke it](https://raw.githubusercontent.com/DatzinDev/VizControl/main/docs/img/demo.gif)

> We don't want an AI to tell us how our software works. We want to observe how it really works.

## Why

To understand a project you didn't write (one you inherited, an open-source library, code an AI generated), you
usually read the source and guess. The guessing gets hard exactly where it matters: pipelines that run steps they
were handed, event buses, plugin registries, `map()` and `sorted(key=...)`. The call that matters is never written
where you can grep for it.

VizControl doesn't guess. It runs the code and records what happened: every call, its arguments, its result, every
exception. Everything on screen comes from that recording, and the one thing it infers (how a callback got called)
is labelled as inferred.

## Try it

```
pip install vizcontrol
vizcontrol run --serve your_script.py --its --args
```

The browser opens on the recording. Or try the example in this repo, a small layered ETL that fails on purpose:

```
git clone https://github.com/DatzinDev/VizControl && cd VizControl/examples/etl
vizcontrol run --serve -m etl data/orders_bad.csv     # KeyError: 'GBP'
```

It opens on the step that raised, with the path that led there in red.
[Understanding a project you didn't write](https://github.com/DatzinDev/VizControl/blob/main/docs/guide.md) walks
through reading a run.

## What you get

- **A call graph that plays.** One box per module, one node per function, one arrow per "A called B". As the run
  plays, the functions light up, the call stack is highlighted and the path to the current call is drawn on top.
- **A transcript.** One line per step: `→ clean_rows(rows=[…])`, `← returned […]`, `✗ RateTable.rate raised KeyError`.
- **Callbacks explained.** When a function ran without being called by name, the step says how it got there
  (`↩ callback · via self.steps[1]`) and where that function came from.
- **Failures first.** A failed run opens on the step where it raised, with the arguments that got it there.
- **Watch and inspect.** Follow a variable by name across the run, or open any value into its full tree and the
  object's history.
- **Share it.** Export the graph as a PNG, or the whole replay as an animated GIF.
- **Compare runs.** `vizcontrol diff a.vizc b.vizc` shows where two runs split and which results differ.
  `vizcontrol diagram run.vizc` prints the run's stages as a Mermaid flowchart.

## Trace what you already run

```
vizcontrol run app.py --flag         # a script (add --serve to open it right away)
vizcontrol run -m package.cli serve  # a module
pytest --vizcontrol                  # one run per test
vizcontrol serve                     # browse every run in .vizcontrol/runs
```

Around one unit of work in your own code:

```python
import vizcontrol

with vizcontrol.trace("checkout") as run:
    place_order(cart)
print(run.path)
```

For a web app, one run per request (FastAPI shown; `WSGIMiddleware` for Flask and Django):

```python
from vizcontrol.web import ASGIMiddleware

app.add_middleware(ASGIMiddleware, every=1)   # or only requests with an X-VizControl header
```

## Safe and light by default

- **Local.** By default the viewer is served on `127.0.0.1` only, with a strict CSP, and requests with a foreign
  `Host` header are rejected (no DNS rebinding).
- **Secrets stay out.** Values named like `password`, `token`, `api_key`, `secret`, `cookie` or `authorization`
  are redacted before anything is written. Trace files are readable by their owner only.
- **No dependencies.** The package uses only the standard library, and the viewer ships prebuilt in the wheel.
- **Low overhead.** It's built on `sys.monitoring` (PEP 669), so code you don't trace runs at native speed. An
  app-like workload runs at 1.1–1.2× ([`benchmarks/overhead.py`](https://github.com/DatzinDev/VizControl/blob/main/benchmarks/overhead.py)).
  Each traced call costs about 5 µs, so millions of tiny calls do add up.

## How it works

`sys.monitoring` capture → a `.vizc` file (SQLite) → a local JSON server → a React viewer. The
[docs](https://github.com/DatzinDev/VizControl/tree/main/docs) cover the requirements, architecture, trace format
and each component, and the [releases](https://github.com/DatzinDev/VizControl/releases) list what changed in each
version. Requires Python 3.12+.

## Contributing

Issues and pull requests are welcome. If it got something wrong on your code, a minimal script that reproduces it
is the most useful report you can send. To report a vulnerability, see [SECURITY.md](https://github.com/DatzinDev/VizControl/blob/main/SECURITY.md).

```
uv sync                                   # Python package (editable) + pytest
uv run pytest
cd frontend && npm ci && npm run build    # viewer -> src/vizcontrol/viewer/static
cd frontend && npm run e2e                # replay the ETL example in headless Chrome
uv run python benchmarks/overhead.py
```

## License

VizControl is free software, licensed under the [GNU Affero General Public License v3.0 or later](https://github.com/DatzinDev/VizControl/blob/main/LICENSE).
© Datzin · contacto@datzin.com.mx
