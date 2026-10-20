#!/usr/bin/env bash
# Trace the sample app, serve the runs and drive the built viewer. Needs: uv, node, Chrome.
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
runs="$(mktemp -d)"
port="${PORT:-7181}"
cd "$root/tests/samples/shop"
uv run --project "$root" vizcontrol run -o "$runs/basic.vizc" checkout.py basic >/dev/null
uv run --project "$root" vizcontrol run -o "$runs/vip.vizc" checkout.py vip >/dev/null 2>&1 || true
uv run --project "$root" vizcontrol serve "$runs" --port "$port" --no-browser &
server=$!
trap 'kill $server 2>/dev/null; rm -rf "$runs"' EXIT
for _ in $(seq 30); do curl -sf "http://127.0.0.1:$port/api/runs" >/dev/null && break; sleep 0.5; done
node "$root/frontend/e2e/smoke.mjs" "http://127.0.0.1:$port/"
