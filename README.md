# VizControl

Observe what a Python program really does while it runs and explore it as an interactive, hierarchical graph.
The graph comes from runtime evidence, not from a model's guess about the code.

```
pip install vizcontrol
vizcontrol run app.py --some-flag     # writes .vizcontrol/runs/<time>-app.vizc
pytest --vizcontrol                   # or: one trace per test
vizcontrol serve                      # explore the runs in the browser
vizcontrol diagram <run>.vizc         # the run's stages as a Mermaid flowchart
vizcontrol diff <a>.vizc <b>.vizc     # where two runs split and which results differ
```

Start with [Understanding a project you didn't write](https://github.com/DatzinDev/VizControl/blob/main/docs/guide.md),
or try `vizcontrol run examples/pipeline.py --bad` and `vizcontrol serve`.

The viewer goes from the whole project (a map of every run's route) down to stages, functions, calls and values;
follows an object through the calls that touched it; and opens a failed run on its failure, with what a passing
run reached and this one didn't.

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

Requires Python 3.12+ (uses `sys.monitoring`). See [docs/](https://github.com/DatzinDev/VizControl/tree/main/docs) for requirements and architecture.

## Development

```
uv sync                                   # Python package (editable) + pytest
uv run pytest
cd frontend && npm ci && npm run build    # viewer -> src/vizcontrol/viewer/static
cd frontend && npm run e2e               # drive the built viewer in headless Chrome
uv run python benchmarks/overhead.py
```

## License

VizControl is free software, licensed under the [GNU Affero General Public License v3.0 or later](https://github.com/DatzinDev/VizControl/blob/main/LICENSE).
© Datzin · contacto@datzin.com.mx
