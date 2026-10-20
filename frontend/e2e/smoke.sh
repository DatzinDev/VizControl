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
# a package of many modules: staged by package, opened on its modules
big="$(mktemp -d)"
mkdir -p "$big/src/big"
touch "$big/src/big/__init__.py"
for i in $(seq 0 12); do
  printf 'def a(x):\n    return b(x) + c(x)\n\n\ndef b(x):\n    return x\n\n\ndef c(x):\n    return d(x)\n\n\ndef d(x):\n    return -x\n' > "$big/src/big/m$i.py"
done
printf 'import big.m%s\n' $(seq 0 12) > "$big/src/main.py"
printf 'print(sum(m.a(1) for m in [%s]))\n' "$(printf 'big.m%s,' $(seq 0 12))" >> "$big/src/main.py"
(cd "$big/src" && uv run --project "$root" vizcontrol run -o "$big/runs/main.vizc" main.py >/dev/null)
uv run --project "$root" vizcontrol serve "$runs" --port "$port" --no-browser &
server=$!
uv run --project "$root" vizcontrol serve "$flat" --port "$((port + 1))" --no-browser &
server2=$!
uv run --project "$root" vizcontrol serve "$big/runs" --port "$((port + 2))" --no-browser &
server3=$!
trap 'kill $server $server2 $server3 2>/dev/null; rm -rf "$runs" "$flat" "$big"' EXIT
for p in "$port" "$((port + 1))" "$((port + 2))"; do
  for _ in $(seq 30); do curl -sf "http://127.0.0.1:$p/api/runs" >/dev/null && break; sleep 0.5; done
done
node "$root/frontend/e2e/smoke.mjs" "http://127.0.0.1:$port/" "http://127.0.0.1:$((port + 1))/" "http://127.0.0.1:$((port + 2))/"
