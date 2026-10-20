"""Build the viewer into the wheel, so `pip install vizcontrol` needs no Node."""

import shutil
import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class ViewerBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if version == "editable":  # dev installs use `npm run build` (or `npm run dev`) directly
            return
        root = Path(self.root)
        if (root / "src/vizcontrol/viewer/static/index.html").is_file():
            return
        frontend = root / "frontend"
        npm = shutil.which("npm")
        if not frontend.is_dir() or npm is None:
            raise RuntimeError(
                "the viewer is not built and npm is not available: run `npm ci && npm run build` in frontend/")
        # --ignore-scripts: third-party install scripts never run while building the package
        # fixed argument lists, no shell, npm resolved from PATH by the person building
        subprocess.run([npm, "ci", "--ignore-scripts", "--no-fund", "--no-audit"], cwd=frontend, check=True)  # noqa: S603
        subprocess.run([npm, "run", "build"], cwd=frontend, check=True)  # noqa: S603
