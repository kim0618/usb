"""Production Context Collector V1: the LIQUIDITY and FLOW layers of Market Context, without the
research journal.

The Market Structure V0 collector is a research instrument. It keeps every depth frame, every
trade and every wall candidate transition so that any later question can be replayed, and that
is why it writes about 4.5 GB a day. The Manual Market Context panel needs none of that history:
it reads the collector's current state once a second. This package runs the **same** V0 state
machine - the same book, the same flow windows, the same wall tracker - and changes only what is
persisted:

* the raw replay material (`raw_depth`, `raw_trade`, `trade`, `snapshot`, `checkpoint`) and the
  per-second duplicates (`derived`, V0 `wall`) are never written;
* the V0 `session`, `telemetry` and `storage_stats` streams and the compact state file are kept
  unchanged, so the frozen Liquidity Map viewer and the Market Context V1 panel read this root
  exactly as they read a research root;
* one `context` record per second carries the Market Context fields, computed in memory by the
  frozen viewer functions and the Market Context V1 rules;
* one `wall_v2` record per `lm-wall.v2` bin transition carries the wall presence a forward study
  needs, so 28.9 walls a second do not have to be rewritten every second.

BTCUSDT only. No order, account or credential path is reachable from here; the package imports
nothing from `app.crypto.paper`, `app.crypto.live`, `app.crypto.terminal` or `app.crypto.c1`.

    python -m app.crypto.context_collector_v1 collect --root /path --duration 0
    python -m app.crypto.context_collector_v1 serve --root /path --port 8013
    python -m app.crypto.context_collector_v1 prune --root /path [--apply]
"""

#: Identity of this collector's own output: the `context` and `wall_v2` record shapes.
VERSION = "ctx-collector.v1.1"
COLLECTOR_VERSION = "ctx-collector.code.1.1"
#: The frozen contract this code implements, and its hash.
CONTRACT_RELATIVE_PATH = "docs/crypto/context_collector_v1/CONTRACT_CTX_V1_1.md"
CONTRACT_SHA256 = "a9f8becfd18aaf920bc2cc2ad96b4d465c5a1faa3a9e8652d261fdded03297fc"

__all__ = ["VERSION", "COLLECTOR_VERSION", "CONTRACT_RELATIVE_PATH", "CONTRACT_SHA256"]
