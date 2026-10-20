# VizControl

Observe what a Python program really does while it runs and explore it as an interactive, hierarchical graph.
The graph comes from runtime evidence, not from a model's guess about the code.

```
pip install vizcontrol
vizcontrol run app.py --some-flag     # writes .vizcontrol/runs/<time>-app.vizc
vizcontrol info .vizcontrol/runs/<file>.vizc
```

```python
import vizcontrol

with vizcontrol.trace("checkout") as run:
    place_order(cart)
print(run.path)
```

Requires Python 3.12+ (uses `sys.monitoring`). See [docs/](docs/README.md) for requirements and architecture.
