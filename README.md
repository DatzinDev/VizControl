# VizControl

Observe what a Python program really does while it runs and explore it as an interactive, hierarchical graph.
The graph comes from runtime evidence, not from a model's guess about the code.

```
pip install vizcontrol
vizcontrol run app.py --some-flag     # writes .vizcontrol/runs/<time>-app.vizc
pytest --vizcontrol                   # or: one trace per test
vizcontrol serve                      # explore the runs in the browser
vizcontrol info .vizcontrol/runs/<file>.vizc
```

A failed run opens on its failure: the error path in red, the exception, the locals where it was raised and the
last observed changes to the objects involved.

```python
import vizcontrol

with vizcontrol.trace("checkout") as run:
    place_order(cart)
print(run.path)
```

Requires Python 3.12+ (uses `sys.monitoring`). See [docs/](docs/README.md) for requirements and architecture.

## Development

```
uv sync                                   # Python package (editable) + pytest
uv run pytest
cd frontend && npm ci && npm run build    # viewer -> src/vizcontrol/viewer/static
uv run python benchmarks/overhead.py
```
