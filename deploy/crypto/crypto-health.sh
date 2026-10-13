#!/usr/bin/env bash
# US-B CRYPTO paper terminal health, composed for the operator.
#
# The terminal's own /health answers only "is the app up". Everything else an operator needs to
# decide whether the screen can be trusted -- feed connectivity, how old the last tick is, whether
# the account actually loaded, what the position is -- lives in /api/crypto/state. This script
# joins the two with systemd's view of the unit, so one command answers "can I trade on this".
#
# Exit code: 0 LIVE, 1 STALE, 2 DISCONNECTED or down.
set -uo pipefail

BASE="${CRYPTO_HEALTH_BASE:-http://127.0.0.1:8100}"
UNIT="${CRYPTO_HEALTH_UNIT:-usb-crypto-paper.service}"
STALE_SECONDS="${CRYPTO_HEALTH_STALE_SECONDS:-5}"
RUN_ROOT="${CRYPTO_PAPER_ROOT:-/root/usb_runtime/crypto_paper/run}"

active=$(systemctl is-active "$UNIT" 2>/dev/null || true)
uptime_since=$(systemctl show "$UNIT" --property=ActiveEnterTimestamp --value 2>/dev/null || true)
restarts=$(systemctl show "$UNIT" --property=NRestarts --value 2>/dev/null || true)

health=$(curl -fsS --max-time 5 "$BASE/health" 2>/dev/null || true)
state=$(curl -fsS --max-time 5 "$BASE/api/crypto/state" 2>/dev/null || true)

UNIT="$UNIT" ACTIVE="$active" SINCE="$uptime_since" RESTARTS="$restarts" \
HEALTH="$health" STATE="$state" STALE_SECONDS="$STALE_SECONDS" RUN_ROOT="$RUN_ROOT" \
python3 <<'PY'
import json, os, shutil, sys, datetime

active = os.environ["ACTIVE"]
health = json.loads(os.environ["HEALTH"]) if os.environ["HEALTH"].strip() else None
state = json.loads(os.environ["STATE"]) if os.environ["STATE"].strip() else None
stale_limit = float(os.environ["STALE_SECONDS"])

def line(label, value):
    print(f"{label:<26} {value}")

print("=== US-B CRYPTO Paper Terminal ===")
line("unit", f'{os.environ["UNIT"]} ({active})')
line("active since", os.environ["SINCE"] or "-")
line("restarts since boot", os.environ["RESTARTS"] or "-")

if active != "active" or health is None:
    line("verdict", "DISCONNECTED (service is not answering)")
    sys.exit(2)

line("backend alive", f'yes (engine {health.get("engine")})')
line("run configured", "yes" if health.get("configured") else f'NO: {health.get("error")}')

if state is None:
    line("verdict", "DISCONNECTED (state endpoint unavailable)")
    sys.exit(2)

feed = state.get("feed") or {}
account = state.get("account")
quote = state.get("quote")
server_ms = state.get("server_time_ms")
last_ms = feed.get("last_message_ms")

age = None
if last_ms is not None and server_ms is not None:
    age = (server_ms - last_ms) / 1000.0

line("bybit ws connected", "yes" if feed.get("connected") else "NO")
line("book ready", "yes" if feed.get("book_ready") else "NO")
line("last market tick (UTC)",
     datetime.datetime.fromtimestamp(last_ms / 1000, datetime.timezone.utc).isoformat()
     if last_ms else "-")
line("tick age (s)", f"{age:.2f}" if age is not None else "-")
line("reconnects / gaps / resyncs",
     f'{feed.get("reconnects")} / {feed.get("book_gaps")} / {feed.get("book_resyncs")}')
if feed.get("last_error"):
    line("last feed error", feed["last_error"])

line("paper account loaded", "yes" if account else "NO")
if account:
    side = account.get("position_side")
    line("position", "FLAT" if side is None
         else f'{side} {account.get("position_qty")} @ {account.get("avg_entry")}')
    line("equity (USDT)", account.get("equity"))
    line("realized pnl (USDT)", account.get("realized_pnl"))
    line("unrealized pnl (USDT)", account.get("unrealized_pnl"))
line("mode", (state.get("state") or {}).get("mode"))
line("new entry allowed", "yes" if (state.get("state") or {}).get("can_open_new_position") else "NO")
line("run id", state.get("run_id"))
line("tape records", state.get("input_record_count"))
line("ledger events", state.get("ledger_event_count"))

# The input tape is append-only and never rotated: recovery replays it in full, so truncating it
# would destroy the run. Disk is therefore a hard operating limit, not a housekeeping chore.
usage = shutil.disk_usage(os.environ["RUN_ROOT"])
free_mb = usage.free / 1024 / 1024
line("disk free (MB)", f"{free_mb:.0f}")
if state.get("input_record_count"):
    line("tape growth", "~37 MB/day at 1 Hz")
    line("days of disk left", f"{free_mb / 37:.0f}")

if not feed.get("connected") or quote is None:
    line("verdict", "DISCONNECTED (no live quote; new entries are refused)")
    sys.exit(2)
if age is None or age > stale_limit:
    line("verdict", f"STALE (last tick {age if age is None else round(age, 2)}s ago; do not enter)")
    sys.exit(1)
line("verdict", "LIVE")
sys.exit(0)
PY
