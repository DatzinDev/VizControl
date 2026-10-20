#!/usr/bin/env bash
# Trace the sample app, serve the runs and drive the built viewer. Needs: uv, node, Chrome.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
runs="$(mktemp -d)"
port="${PORT:-7181}"
cd "$root/tests/samples/shop"
uv run --project "$root" vizcontrol run -o "$runs/basic.vizc" checkout.py basic >/dev/null
uv run --project "$root" vizcontrol run -o "$runs/vip.vizc" checkout.py vip >/dev/null 2>&1 || true
# a flat one-file project, served on its own: a served directory is one project
flat="$(mktemp -d)"
cd "$root/examples"
uv run --project "$root" vizcontrol run -o "$flat/ok.vizc" pipeline.py >/dev/null
uv run --project "$root" vizcontrol run -o "$flat/bad.vizc" pipeline.py --bad >/dev/null 2>&1 || true
uv run --project "$root" vizcontrol serve "$runs" --port "$port" --no-browser &
server=$!
uv run --project "$root" vizcontrol serve "$flat" --port "$((port + 1))" --no-browser &
server2=$!
trap 'kill $server $server2 2>/dev/null; rm -rf "$runs" "$flat"' EXIT
for p in "$port" "$((port + 1))"; do
  for _ in $(seq 30); do curl -sf "http://127.0.0.1:$p/api/runs" >/dev/null && break; sleep 0.5; done
done
node "$root/frontend/e2e/smoke.mjs" "http://127.0.0.1:$port/" "http://127.0.0.1:$((port + 1))/"
