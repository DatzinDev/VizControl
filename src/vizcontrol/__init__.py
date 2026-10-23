"""VizControl: observe real Python executions and explore them as graphs."""

__version__ = "0.5.0"
FORMAT_VERSION = "1.1"

from vizcontrol.config import Config, load_config  # noqa: E402
from vizcontrol.runners import trace, traced  # noqa: E402
from vizcontrol.serialize import register  # noqa: E402

__all__ = ["Config", "load_config", "register", "trace", "traced", "__version__"]
