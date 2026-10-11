# Manual Trade Snapshot Store: design (R0.1 §11)

Status: **design only. Nothing is implemented or deployed.**

Purpose: from now on, every real manual BTC trade gets an entry-time context snapshot and an outcome
row. A future edge study then has its labels, which MANUAL-R1 and this R0.1 both lacked. Every
manual entry on record so far has no context at all (see R0.1 results §P).

## 1. Principle

- **No hook in the order path.** The terminal's order code is not touched. A separate read-only
  follower tails what already exists:
  - the paper ledger (`run/<id>/ledger.jsonl`, append-only);
  - the Binance LIVE mirror and audit (`live_mirror` journal, user-data events).
- It writes only its own store, which lives outside the trading runtime and is read-only to
  everything else.
- **Never estimate a missing layer.** If a source is stale or absent at the entry instant, that
  layer is written as `null` with a reason. It is never interpolated or back-filled from later data.
- The follower is restartable and idempotent. Rows are keyed by (account, position open event id),
  so re-reading the ledger appends nothing twice. This is the Q123 recorder pattern.

## 2. Trigger

- A `POSITION_OPEN` (paper) or a LIVE position transition from flat to non-flat (mirror) creates an
  entry snapshot.
- `POSITION_INCREASE` appends an increase row. The entry snapshot is never rewritten.
- `POSITION_CLOSE` / `LIQUIDATION` (paper) or a transition from non-flat to flat (LIVE) creates an
  outcome row.
- The snapshot time is the **fill time** in the ledger. The context used is the newest context
  whose `receive_ms` ≤ fill time. A later sample is never used.

## 3. Entry snapshot schema (`manual-snapshot.v1`)

| field | source | note |
|---|---|---|
| `account` | PAPER_BTC_MANUAL / BINANCE_LIVE | |
| `symbol` | ledger / mirror | BTCUSDT only in v1 |
| `direction` | LONG / SHORT | |
| `entry_ms`, `entry_price`, `qty`, `leverage` | fill | |
| `origin` | ledger reason (MANUAL-R2 vocabulary) | legacy `MANUAL` maps to `UNKNOWN_LEGACY`, never to USER |
| `chart_timeframe` | not observable today | `null` + `NOT_RECORDED`. The UI does not report the timeframe on screen; recording it needs a UI change, which is a separate decision |
| `structure.{tf}` for 15s, 1m, 10m, 1h, 4h, 1d | R1 time-scaled run (R0.1 §2) on Bybit bars at the last close ≤ entry | nearest R/S price and distance in ATR(tf). The 1m value equals the production structure worker's |
| `walls` | Context Collector `context_latest` at ≤ entry | nearest ASK/BID V2 wall price, distance bp, persistence, `wall_state` |
| `flow` | collector 60 s window | buy/sell share, state |
| `derivatives` | MC V1 derivatives cache (OI 1h change, funding, basis) | as the strip shows them |
| `liquidation_context` | liquidation_forward last 15 min, if the file is readable | counts and notional by side, or `null` |
| `c1` | C1 engine state at ≤ entry (read-only GET) | state and last signal id, or `null` |
| `layer_status` | per layer: LIVE / STALE / ABSENT + reason | |

## 4. Outcome row

`exit_ms`, `exit_price`, `exit_reason` (ledger), `net_pnl` and its parts (gross, fee, funding,
from the ledger, never recomputed), `hold_ms`, `mfe_bp`, `mae_bp`, `max_hold_ms`.

MFE and MAE come from the same 1m Bybit bars as the chart, entry minute to exit minute inclusive.
They carry a flag when an extreme is in the entry or exit minute: intrabar order is unknown there.

## 5. Store, resources, retention

- Location: `/root/usb_runtime/manual_snapshots/`.
- Format: `entries.jsonl` and `outcomes.jsonl`, append-only, plus a checkpoint holding the ledger
  byte offset and the mirror sequence.
- Size: a few KB per trade, so under 1 MB a year at the current trade rate.
- Process: a oneshot timer, once a minute, the same shape as the BTC context structure worker. It
  has `ProtectSystem=strict`, its store is its only writable path, and it reads the ledger and the
  journal read-only. Expected RSS is about 50 MB with a structure checkpoint per timeframe.

## 6. Tests required before any deployment

- Replaying the existing ledger twice gives byte-identical stores.
- Truncating at every event gives a prefix of the full store.
- A context sample after the fill time never appears (perturbation test).
- `MANUAL` maps to `UNKNOWN_LEGACY`.
- A missing collector gives `layer_status = ABSENT`, not an error.
- No order or terminal module is imported (static test, as for btc_context_ui_v1).
