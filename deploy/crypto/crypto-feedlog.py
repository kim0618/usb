"""Append-only log of feed transitions for the crypto paper terminal.

The feed keeps its reconnects, book gaps and resyncs as in-memory counters and never writes them
anywhere. Counters answer "how many times" but not "when", and for a service that is meant to run
unattended for weeks "when" is the whole question -- an operator looking at a bad fill needs to
know whether the socket had just come back. This samples the counters and writes a line only when
something actually changed, so a quiet week costs a few hundred bytes.

It deliberately lives outside the terminal package: that code is owned by the research session and
this is an operations concern.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8100"
LOG = Path("/root/usb_runtime/crypto_paper/logs/feed.log")
CURSOR = Path("/root/usb_runtime/crypto_paper/logs/.feedlog-cursor.json")
STALE_SECONDS = 5

TRACKED = ("connected", "connects", "reconnects", "book_gaps", "book_resyncs",
           "malformed", "book_ready", "last_error")


def verdict(state: dict) -> str:
    feed = state.get("feed") or {}
    if not feed.get("connected") or state.get("quote") is None:
        return "DISCONNECTED"
    last, now = feed.get("last_message_ms"), state.get("server_time_ms")
    if last is None or now is None:
        return "DISCONNECTED"
    return "STALE" if (now - last) / 1000.0 > STALE_SECONDS else "LIVE"


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        with urllib.request.urlopen(BASE + "/api/crypto/state", timeout=10) as response:
            state = json.loads(response.read())
    except Exception as exc:
        sample = {"verdict": "UNREACHABLE", "error": f"{type(exc).__name__}: {exc}"}
    else:
        feed = state.get("feed") or {}
        account = state.get("account") or {}
        sample = {key: feed.get(key) for key in TRACKED}
        sample["verdict"] = verdict(state)
        sample["position"] = account.get("position_side")
        sample["ledger_events"] = state.get("ledger_event_count")

    previous = {}
    if CURSOR.exists():
        try:
            previous = json.loads(CURSOR.read_text())
        except json.JSONDecodeError:
            previous = {}

    # Position and ledger movement are carried for context but must not by themselves produce a
    # line, or every manual trade would masquerade as a feed event.
    signal = {k: v for k, v in sample.items() if k not in ("position", "ledger_events")}
    prior_signal = {k: v for k, v in previous.items() if k not in ("position", "ledger_events")}

    if signal != prior_signal:
        changed = sorted(k for k in signal if signal.get(k) != prior_signal.get(k))
        line = {"ts": stamp, "changed": changed, **sample}
        with LOG.open("a") as stream:
            stream.write(json.dumps(line, sort_keys=True) + "\n")
        print(json.dumps(line, sort_keys=True))

    CURSOR.write_text(json.dumps(sample, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
