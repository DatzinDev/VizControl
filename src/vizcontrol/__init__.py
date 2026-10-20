"""VizControl: observe real Python executions and explore them as graphs."""

__version__ = "0.1.0"
FORMAT_VERSION = "1.0"

from vizcontrol.config import Config, load_config  # noqa: E402

__all__ = ["Config", "load_config", "__version__"]
