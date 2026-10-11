# CONTRACT: Manual Trade Snapshot Store V1 (BTCUSDT, Binance LIVE)

Status: **FROZEN before any snapshot is stored.** It is committed before the first store row is
written. A change after that makes V1.1, and rows written under V1 keep their `contract`
field.

Purpose: record context for the user's own BTC LIVE trades, so that a later study can ask in which
contexts the user trades well or badly. **Research data only.** There is no signal, no automation,
and no edge conclusion in V1.

Hard limits:
- The recorder never imports or calls order, leverage, AUTO, ExitGuard, HOT or collector code
  paths.
- It never writes outside its own store.
- It never edits a source file.
- It holds no Binance credential.
- A value it cannot read is written as absent with a reason. It is never estimated.

## 1. Sources (audit of 2026-10-11 on production copies, sha in the report)

| source | what it gives | used for |
|---|---|---|
| S1 `data/runtime/crypto/live/manual_performance.json` (crypto-paper's own durable cache of Binance `userTrades`, refreshed by the terminal) | every BTC fill: trade id, order id, client order id, side, qty, price, Binance `realizedPnl`, commission, time; plus funding income rows | **detection of fills (primary)**: unique trade id, partial fills grouped by order id, authoritative PnL and fees |
| S2 `data/runtime/crypto/live/<fp>/binance_live_events.jsonl` (LIVE audit mirror, append-only) | `LIVE_ORDER_INTENT` (button press time), `LIVE_ORDER_SENT` (client order id, params incl. reduceOnly, `permission`), `LIVE_ORDER_RESULT` | **origin classification and decision time**. Its `LIVE_STREAM_EVENT` lines carry the event type only (no fill payload), so it cannot be the fill source |
| S3 Context Collector journal (`/root/usb_runtime/crypto_context/journal/context/*`, 1 s, 7-day retention) | book, spread, liquidity / walls, flow windows, collector state, session id | collector layers, **as of the fill time** |
| S4 Binance public REST (no key): klines 1m/1h/4h/1d, aggTrades, markPriceKlines, indexPriceKlines, openInterestHist 5m, fundingRate | timeframe and derivatives context, outcome paths | historical by time range, so values are as of the fill time |

Rejected as primary:
- `TRADE_LITE` / `ORDER_TRADE_UPDATE` in the mirror carry no payload.
- `LIVE_ORDER_RESULT` is missing for 13 of 54 sends, and a market order's result is not its fill
  list.
- The paper ledger is not the LIVE account.

**Refresh trigger (S1).** S1 is refreshed only when something calls the terminal's
`GET /api/crypto/binance/performance?symbol=BTCUSDT`. The UI does this on the LIVE screen. The
recorder may issue that same GET at most once per 5 minutes (configurable, default on). It is a
read through the terminal's existing path, and the recorder holds no key. Detection latency
therefore never changes what is recorded: every context value is read as of the fill time from
S3/S4 history.

## 2. Classification

Fills are grouped into one **order event** per Binance `order_id`. Origin comes from the client
order id and S2:

| origin | rule |
|---|---|
| `MANUAL_TERMINAL` | client id starts `usbm-`, and S2 has no `LIVE_ORDER_SENT` with `permission = AUTO_EXIT_CLOSE_ONLY` for it |
| `EXITGUARD` | client id starts `usbm-` and S2 has that send with `permission = AUTO_EXIT_CLOSE_ONLY` (ExitGuard uses the same router plan and prefix, so the prefix alone would call it manual) |
| `USBM_UNVERIFIED` | client id starts `usbm-`, the fill is earlier than S2's first line, and no S2 line can confirm the origin |
| `EXTERNAL` | any other client id (Binance app / web / other API). The terminal did not send it |
| `UNKNOWN` | S1 has no client id for the fill (`manual = null`) |

C1 AUTO trades only on the paper account and never appears in S1.

**Event type**, from the signed position before and after the order, where the position is the
running sum of all S1 fills in time and id order:
- `OPEN_LONG` / `OPEN_SHORT`: from flat;
- `ADD`: same side, larger;
- `REDUCE`: same side, smaller, not flat;
- `CLOSE`: to flat;
- `REVERSE`: through zero. It is recorded as `CLOSE` plus `OPEN_*` at the same price; the terminal
  refuses reverses, so only an external order can do this.

**Scope: BTCUSDT only** (S1's `manual_performance.json`; the HOT and per-symbol files are out of V1).

**Order settlement.** An order event is written once the S1 file's `last_calculated_ms` is at
least the order's last fill time plus 60 s. A market order fills within milliseconds, so this
guarantees all of its fills are in S1 before it is grouped.

## 3. Episode

An episode runs from flat to flat on the account's BTC position. `episode_id = "ep-<first order
id>"`. It contains every order event, whatever its origin.

- `origin_mix` = the set of origins.
- The episode counts toward manual edge gates only if every event is `MANUAL_TERMINAL`
  (`manual_only = true`).
- Summary on close:
  - direction, first entry time and price, VWAP entry (opening and adding fills), final exit time
    and VWAP;
  - max size, hold ms, order counts by type;
  - gross = Σ Binance `realized_pnl`, fees = Σ commission, funding = Σ S1 funding income with
    time inside [open, close], net = gross − fees + funding (USDT);
  - MFE / MAE (bp vs VWAP entry, from Binance 1m high / low over [first fill minute, last fill
    minute]; flag `extreme_in_fill_minute` when the extreme is in either end minute);
  - give-back = MFE − exit return (bp);
  - the snapshot ids of the open event and the last event.
- An episode still open has no summary. It gets one when it closes.

## 4. Snapshot schema `manual-snapshot.v1` (one row per snapshot)

**Keys:** `snapshot_id`, `kind` (EVENT / HOLD / FOLLOWUP), `episode_id`, `event_id` (order id) or
`slot_ms` (hold), `as_of_ms`, `recorded_at_ms`, `backfilled` (bool), `contract`.

**Event fields:**
- `event_type`, `origin`, `direction`, `symbol`;
- fill: `first_fill_ms`, `last_fill_ms`, `vwap`, `qty`, `fills`, `commission`, `realized_pnl`;
- decision: `intent_ms` (S2 `LIVE_ORDER_INTENT` stage PLAN closest before the send, else absent),
  `sent_ms`;
- `leverage` and `notional`: leverage from S2's nearest leverage record ≤ fill, else absent;
  notional = qty × vwap.

**Position fields** (computed from S1 fills only):
- `qty_before`, `qty_after`, `vwap_entry_before`, `vwap_entry_after`;
- `unrealized_before_usdt` = (price_ref − vwap_entry_before) × signed qty_before, at the market
  price below;
- `episode_realized_before_usdt`.

**Market** (S4, as of `as_of_ms`):
- `last_1m_close` (last closed 1m bar with close time ≤ as_of);
- `mark` and `index` (last closed markPriceKlines / indexPriceKlines 1m);
- `basis_bp` = (mark − index) / index × 1e4;
- `funding_rate_last` (last funding time ≤ as_of), `next_funding_ms`;
- `oi_last` (last 5m statistic with time + 5 m ≤ as_of), `oi_change_1h_pct`.

**Timeframe features** for each of `15s` (aggTrades → 15 s bars), `1m`, `10m` (from 1m), `1h`,
`4h`, `1d`. Lookback N = 20 closed bars ending at or before as_of (R0.1's primary horizon). The
fields are:
- `ret_bp` = close_last / close_first − 1;
- `range_bp` = (max high − min low) / close_last;
- `atr14` (Wilder, as in Price Structure R0; absent if fewer than 15 bars);
- `volume`;
- `dist_high_bp`, `dist_low_bp` = distance from close_last to the N-bar high / low.

Structure levels are **not** stored as features: R0.1 found no edge in them.
`15s` is computed for EVENT snapshots only; HOLD and FOLLOWUP mark it `NOT_COLLECTED`.

**Collector** (S3: the newest context row with `receive_ms` ≤ as_of):
- `row_ms`, `age_ms`, `session_id`, collector state;
- book: best bid / ask, mid, spread bp, book state, generation;
- liquidity: state; per side `wall_state` and the nearest wall's price, distance bp, notional and
  persistence;
- flow: per window 5s / 15s / 60s, buy / sell USDT and state.

If `age_ms > 5 000` the layer is marked `STALE` with its values kept. If no row exists, it is
`NOT_AVAILABLE`.

**Not collected in V1:**
- liquidation context: the liquidation_forward store has no as-of reader;
- C1 state: there is no history source, and the state file holds only the present.

Both are written as `NOT_COLLECTED` with that reason.

**Layer status:** `layer_status = {market, timeframes, collector, derivatives, position}`, each one
of `OK` / `STALE` / `NOT_AVAILABLE` / `NOT_COLLECTED`, plus a reason, and `source_sessions`.

## 5. Snapshots taken

- **EVENT:** one per order event, as of its `last_fill_ms`.
- **FOLLOWUP:** for each EVENT, at +1, +5, +15, +30 and +60 min. Each is written once its time
  has passed and its data exists. Fields:
  - market and collector as of that time;
  - `ret_bp` signed in the episode direction from the event VWAP;
  - `mfe_bp` / `mae_bp` from the event to then (1m high / low, end minutes flagged);
  - `qty_now` (position at that time from S1).
- **HOLD:** while an episode is open, one snapshot every **5 minutes** on the UTC 5-minute grid,
  plus the EVENT snapshots. Fields:
  - position age, unrealized pnl at last_1m_close;
  - MFE / MAE since the open;
  - market, collector and timeframes (not 15s).

  Cadence is decided by storage before any row exists:

  | | per row | rows per day | per day |
  |---|---|---|---|
  | 1 min | ~2 KB | 1 440 | ~2.9 MB |
  | 5 min | ~2 KB | 288 | ~0.6 MB |

  Positions on record have lasted up to ~5 days, so 1 min would be about 14 MB for one episode.
  Five minutes plus every event is the V1 choice.

## 6. Store and idempotency

Root `/root/usb_runtime/manual_trade_research/` (local default `data/runtime/crypto/manual_trade_research/`):
- `events.jsonl`, `snapshots.jsonl`, `episodes.jsonl`;
- `cursor.json`;
- `runtime/` (lock, market data cache).

Writes:
- **Append only, one JSON per line, flush + fsync per line.** A torn last line is ignored and
  repaired on the next run by truncating to the last newline.
- Every row has a deterministic key:
  - events: `event_id = <order_id>`;
  - snapshots: `snapshot_id` = `ev:<order_id>`, `fu:<order_id>:<minutes>`, `hold:<episode_id>:<slot_ms>`;
  - episodes: `episode_id` (written once, when closed).

  A key that is already present is never written again.
- Each run re-derives events and episodes from the whole S1 file, which is small (58 fills today),
  and appends only missing keys. Cursor loss, a restart, a replayed or duplicated source, or an S1
  file rewrite therefore cannot create a duplicate. A fill that disappears from S1 after its event
  was written does not delete anything. It is counted in `cursor.json.anomalies`.
- One run at a time: an `flock` on `runtime/lock` (held fd; inode re-checked after acquiring).

## 7. Backfill

S1 fills before the recorder starts are recorded with `backfilled = true`. Only layers that exist
as of the fill time are filled in:
- S4 public history: klines and markPriceKlines for any date; openInterestHist only for the last
  30 days;
- S3 only within its 7-day retention and from the collector's start (2026-10-10 21:47 KST).

Everything else is `NOT_AVAILABLE`. Nothing is estimated.

## 8. Gates for later analysis (not applied in V1)

Count of closed `manual_only` episodes:

| n | gate |
|---|---|
| < 20 | INSUFFICIENT |
| 20-49 | EARLY_REVIEW |
| 50-99 | MANUAL_EDGE_REVIEW |
| ≥ 100 | STRONGER_EVIDENCE |

## 9. Readiness verdict (for this work order)

- **A. NOT_READY**: any §10 test fails.
- **B. READY_FOR_LOCAL_TRIAL**: all tests pass on synthetic data and production copies.
- **C. READY_FOR_PRODUCTION_READ_ONLY_RECORDER**: B, plus the offline E2E scenarios A-H pass on
  production copies, the RSS of a run is ≤ 50 MB, and a release, unit and deploy plan exist.
  Even at C, it is not deployed without the user's approval.

## 10. Tests required

- manual vs ExitGuard vs external separation;
- duplicate protection (replay, duplicated fills, cursor loss, torn line);
- partial fills;
- add / reduce / close / reverse;
- restart recovery;
- S1 rewrite / rotation;
- missing collector or market layer;
- no future leak (data after as_of perturbed → snapshot unchanged);
- MFE / MAE and net PnL hand checks;
- BTC-only gate;
- no import of any order / terminal / live-router module (static);
- no write outside the store.
