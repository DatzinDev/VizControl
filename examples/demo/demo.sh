#!/usr/bin/env bash
# Trace the notes demo three ways, then open the viewer on all the runs.
# Needs `vizcontrol` and `pytest` on PATH: pip install vizcontrol pytest (or, in this repo, uv run examples/demo/demo.sh).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf .vizcontrol
vizcontrol run -m notes data/ok                     # a build that works
vizcontrol run -m notes data/broken || true         # a build that fails on a broken [[link]]
pytest -q -p no:cacheprovider --vizcontrol || true  # one run per test: the map of the whole project
vizcontrol serve "$@"
