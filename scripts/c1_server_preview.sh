#!/usr/bin/env bash
#
# C1 Signal V1 server preview. PREVIEW ONLY. Run by the operator, not by an agent.
#
# What it does NOT touch, by construction:
#   * usb-crypto-paper.service and the production port 8100
#   * /root/usb_runtime/crypto_paper/src       (the production source snapshot)
#   * usb-frontend and the production Next build
#   * nginx, including the /crypto-api/ route
#   * data/runtime/crypto/paper                (the live paper run and its ledger)
#   * any systemd unit (this is a foreground process you kill with Ctrl-C)
#
# What it creates: one uvicorn on 127.0.0.1:8112 with its own source copy, its own paper run
# directory and its own C1 state directory, no Binance key in its environment, and fixture
# signals. It is reached over an SSH tunnel; nothing is published.
#
# Usage, from the development machine:
#
#   1) build the fixture here (needs the research data, which the server does not have):
#        PYTHONPATH=backend .venv/bin/python scripts/c1_fixture.py /tmp/c1_fixture.json 6
#
#   2) copy this preview's source and fixture to the server, into a NEW directory:
#        ssh traderj 'mkdir -p /root/usb_preview_c1/src'
#        rsync -a --delete backend/app /root/... -> see PREVIEW_SRC below; copy backend/ only
#        scp /tmp/c1_fixture.json traderj:/root/usb_preview_c1/c1_fixture.json
#        scp scripts/c1_server_preview.sh traderj:/root/usb_preview_c1/
#
#   3) on the server:  bash /root/usb_preview_c1/c1_server_preview.sh
#
#   4) from the development machine, tunnel and open:
#        ssh -N -L 8112:127.0.0.1:8112 traderj
#      then run the preview frontend locally against it:
#        cd frontend && NEXT_PUBLIC_CRYPTO_API_BASE=http://127.0.0.1:8112 \
#          NEXT_DIST_DIR=.next-c1-preview npx next dev --port 3100
#      and open http://127.0.0.1:3100/crypto-paper
#
set -euo pipefail

PREVIEW_ROOT="${PREVIEW_ROOT:-/root/usb_preview_c1}"
PREVIEW_SRC="$PREVIEW_ROOT/src/backend"
PREVIEW_PORT="${PREVIEW_PORT:-8112}"
PRODUCTION_PORT=8100
PRODUCTION_SRC=/root/usb_runtime/crypto_paper/src

if [ "$PREVIEW_PORT" = "$PRODUCTION_PORT" ]; then
  echo "refusing: $PREVIEW_PORT is the production port" >&2; exit 1
fi
case "$PREVIEW_ROOT" in
  "$PRODUCTION_SRC"*|/root/usb|/root/usb/*)
    echo "refusing: the preview root overlaps the production source" >&2; exit 1;;
esac
if [ ! -d "$PREVIEW_SRC/app/crypto/c1" ]; then
  echo "the preview source is not in place: $PREVIEW_SRC/app/crypto/c1" >&2; exit 1
fi
if [ ! -f "$PREVIEW_ROOT/c1_fixture.json" ]; then
  echo "the fixture is not in place: $PREVIEW_ROOT/c1_fixture.json" >&2; exit 1
fi

mkdir -p "$PREVIEW_ROOT/state/paper" "$PREVIEW_ROOT/state/c1"
if [ ! -f "$PREVIEW_ROOT/state/paper/run_config.json" ]; then
  # Copy the production run config and rename the run, so the preview opens its own account in
  # its own directory and cannot append to the live ledger.
  python3 - "$PRODUCTION_SRC" "$PREVIEW_ROOT" <<'PY'
import json, sys, pathlib
src = pathlib.Path(sys.argv[1]).parent / "run_config.json"
if not src.exists():
    src = pathlib.Path("/root/usb/data/runtime/crypto/paper/run_config.json")
body = json.loads(src.read_text())
body["run_id"] = "c1-preview-001"
out = pathlib.Path(sys.argv[2]) / "state/paper/run_config.json"
out.write_text(json.dumps(body, indent=2, sort_keys=True))
print("preview run config:", out, "run_id", body["run_id"])
PY
fi

cd "$PREVIEW_SRC/.."
export PYTHONPATH=backend
export CRYPTO_PAPER_RUN_CONFIG="$PREVIEW_ROOT/state/paper/run_config.json"
export CRYPTO_PAPER_ROOT="$PREVIEW_ROOT/state/paper"
export CRYPTO_C1_SIGNAL=on
export CRYPTO_C1_ROOT="$PREVIEW_ROOT/state/c1"
export C1_FIXTURE="$PREVIEW_ROOT/c1_fixture.json"
export CRYPTO_TRADE_CANDLES=on
# No key reaches this process, so the LIVE panel reports unavailable and no order, leverage,
# arm or Auto Exit path can be entered at all.
unset BINANCE_API_KEY BINANCE_API_SECRET BINANCE_LIVE_TRADING_ENABLED

echo "C1 preview on 127.0.0.1:$PREVIEW_PORT  (production on $PRODUCTION_PORT is untouched)"
exec python3 -m uvicorn app.crypto.terminal.server:app \
  --host 127.0.0.1 --port "$PREVIEW_PORT" --workers 1
