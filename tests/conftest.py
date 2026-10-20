import subprocess
import sys
from pathlib import Path

import pytest

SHOP = Path(__file__).parent / "samples" / "shop"


@pytest.fixture(scope="session")
def runs_dir(tmp_path_factory) -> Path:
    """Two real runs of the sample shop: basic (ok) and vip (raises)."""
    d = tmp_path_factory.mktemp("runs")
    for tier in ("basic", "vip"):
        subprocess.run([sys.executable, "-m", "vizcontrol", "run", "-o", str(d / f"{tier}.vizc"), "checkout.py", tier],
                       cwd=SHOP, capture_output=True, timeout=60)
    return d


pytest_plugins = ["pytester"]
