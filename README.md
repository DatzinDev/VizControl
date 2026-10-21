# VizControl

Record what a Python program really does while it runs, then step through it like a debugger after the fact.
Everything shown comes from runtime evidence, not from a model's guess about the code.

![Replay of the ETL example: the call graph by module, a callback marked with where the function came from, the transcript and the call stack](https://raw.githubusercontent.com/DatzinDev/VizControl/main/docs/img/replay.png)

```
pip install vizcontrol
vizcontrol run --serve app.py --flag # trace app.py, then open the run in the browser
vizcontrol run app.py --flag          # or only write .vizcontrol/runs/<time>-app.vizc
pytest --vizcontrol                   # or: one trace per test
vizcontrol serve                      # explore the runs in the browser
vizcontrol diagram <run>.vizc         # the run's stages as a Mermaid flowchart
vizcontrol diff <a>.vizc <b>.vizc     # where two runs split and which results differ
```

A run opens as a recording you can step through, like a debugger after the fact: the call graph (one box per module)
lights up as the program goes, a transcript says who called whom with what and what came back, callbacks are marked
with where the function came from, and Watch follows the variables you pick. A failed run opens on the step where
it raised.

Try it on the [ETL example](https://github.com/DatzinDev/VizControl/tree/main/examples/etl), or read
[Understanding a project you didn't write](https://github.com/DatzinDev/VizControl/blob/main/docs/guide.md).

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

Requires Python 3.12+ (uses `sys.monitoring`). See [docs/](https://github.com/DatzinDev/VizControl/tree/main/docs) for requirements and architecture,
and the [releases](https://github.com/DatzinDev/VizControl/releases) for what changed in each version.

## Development

```
uv sync                                   # Python package (editable) + pytest
uv run pytest
cd frontend && npm ci && npm run build    # viewer -> src/vizcontrol/viewer/static
cd frontend && npm run e2e               # replay the ETL example in headless Chrome
uv run python benchmarks/overhead.py
```

## License

VizControl is free software, licensed under the [GNU Affero General Public License v3.0 or later](https://github.com/DatzinDev/VizControl/blob/main/LICENSE).
© Datzin · contacto@datzin.com.mx
