# Manual Trade Snapshot Store V1: report

Verdict: **C. READY_FOR_PRODUCTION_READ_ONLY_RECORDER. Not deployed: user approval needed.**

**Commits**
- Contract: `CONTRACT_MANUAL_TRADE_SNAPSHOT_V1.md`, sha256 `2a48bf35…`, commit d6349e7.
- Implementation: 7072933. Release staged from it: 15 files, tgz sha256 `dcd31af7…`, MANIFEST
  verified, imports resolve from the release tree alone.

Evidence (local, not in git): `~/usb-manual-snapshot-v1-evidence/`
- `prod_copy_20261011-2231/`: read-only copies of S1, S2, `exit_guard.json` and `c1/state.json`,
  plus SHA256SUMS;
- `e2e_work/`.

## A. Source selection

Audit of the production copies on 2026-10-11:

| source | timestamp | symbol / side / qty / price | order id | reduceOnly | origin | partial fills | verdict |
|---|---|---|---|---|---|---|---|
| S1 `live/manual_performance.json` (terminal cache of Binance `userTrades`) | fill time | yes | order id + unique trade id | via side and position | client order id, `manual` flag | yes, n fills per order | **primary** |
| S2 mirror `LIVE_ORDER_SENT` | send time | params | client id | yes | `permission` (AUTO_EXIT) | no | **origin, decision time** |
| S2 `LIVE_ORDER_INTENT` (stage PLAN) | button time | side, intent | none | none | none | none | decision time |
| S2 `LIVE_ORDER_RESULT` | response | response | yes | | | not a fill list | rejected: 41 results for 54 sends |
| S2 `ORDER_TRADE_UPDATE` / `TRADE_LITE` | frame time | **event type only, no payload** | | | | | rejected |
| paper ledger | | | | | | | wrong account |

- S1 holds 58 BTC fills since 2026-09-29 and 33 funding rows; `classification_complete` is true.
- S2 holds 15 847 lines since 2026-09-30, of which 54 are sends and 0 carry AUTO_EXIT.

**Key finding: ExitGuard is invisible to the prefix.** ExitGuard closes through the same router
plan, so its orders get the same `usbm-` client id as manual orders. S1 alone therefore counts an
ExitGuard close as manual. V1 separates them with S2's `permission = AUTO_EXIT_CLOSE_ONLY`.

S1 refreshes only when the terminal's `GET /api/crypto/binance/performance` is called. The UI does
this; the recorder can too, at most once per 5 minutes. Latency never changes what is recorded,
because every value is read as of the fill time (§E).

## B. Manual / AUTO classification

| origin | rule | events on record |
|---|---|---|
| `MANUAL_TERMINAL` | `usbm-` and an S2 send without AUTO_EXIT | 41 |
| `EXITGUARD` | `usbm-` and an S2 send with AUTO_EXIT | 0 (shown in E2E F) |
| `USBM_UNVERIFIED` | `usbm-` before S2 starts (2026-09-29 fills) | 6 |
| `EXTERNAL` | any other client id | 0 (shown in unit test B/F) |

C1 AUTO trades only on paper and never appears in S1.

An order S1 has not yet classified (`client_order_id` null, retried by the terminal) **stops
derivation** at that order. If it were written as UNKNOWN, a later classification would conflict
with the store forever. If it were skipped, every later order's position arithmetic would break.
It waits and is listed in `cursor.waiting_for_s1_classification`.

## C. Schema

`manual-snapshot.v1`, as frozen in contract §4.
- **Event rows:** fills grouped by order id, VWAP, Binance realized PnL and commission, position
  before / after, VWAP entry, intent / send time, leverage.
- **Snapshot rows:** `market`, `timeframes`, `collector`, `position`, `liquidation`, `c1`,
  `layer_status`, `source_sessions`.
- **Measured size:** 2.19 KB per snapshot.

## D. Episode model

- An episode runs flat → flat over all origins. `manual_only` is true only if every event is
  `MANUAL_TERMINAL`.
- REVERSE (only possible externally) is split into CLOSE plus a new OPEN.
- Summary fields: direction, first entry, VWAP entry and exit, max size, hold, event counts;
  gross (Binance realized), fees, funding from S1 rows inside the hold, net; exit return; MFE, MAE
  and give-back from Binance 1m bars, with the end minutes flagged.

On record: 20 episodes, 19 closed and 1 open (LONG 0.108 since 10-07). 16 of the closed are
`manual_only`, so the later-analysis gate is **INSUFFICIENT** (< 20). No outcome is interpreted.

## E. Entry snapshot

One per order event, as of the last fill time. Every source is read as of then:
- Binance klines and mark / index from closed bars;
- OI only once its 5-minute statistic is complete;
- the collector journal row with the newest `receive_ms` ≤ as_of.

Coverage on the backfill:
- collector layer OK for events after 2026-10-10 21:47 KST, NOT_AVAILABLE before;
- 15s NOT_AVAILABLE for all 47 backfilled events (Binance serves aggTrades only for 2 days; error
  -4166 is recorded verbatim);
- 15s OK for a recent event (E2E D).

Liquidation context and C1 are `NOT_COLLECTED`: no as-of source exists in V1.

## F. Hold snapshot

5-minute UTC grid while a position is open, plus every event. The cadence was set before any
data, from storage:
- 1 min ≈ 2.9 MB/day per open position;
- 5 min ≈ 0.6 MB/day.

Fields: position age, unrealized PnL, MFE/MAE since open, market, collector, timeframes (15s not
collected).

## G. Exit snapshot

REDUCE and CLOSE events are snapshots like any event. The episode summary is written once, when
the episode closes. Partial reduce followed by close is handled (E2E D: REDUCE 0.050 then CLOSE
0.058 of the real 0.108 LONG):
- events `{OPEN_LONG, REDUCE, CLOSE}`;
- VWAP exit 83 253.70;
- net = gross −71.2148 − fees 9.0313 + funding.

## H. Outcome tracking

FOLLOWUP snapshots at +1, +5, +15, +30 and +60 min after each event. Each is written once the time
has passed and its 1m bar has closed (2-minute lag). Fields:
- signed return vs the event VWAP;
- MFE / MAE over the interval;
- market and collector at that time.

## I. Crash / restart safety

| case | mechanism | proof |
|---|---|---|
| restart, cursor loss | each run re-derives everything from S1 and appends only missing keys | E2E G: chunked runs (400 per run), torn line, cursor and S2 index deleted → identical to one run |
| duplicate replay, duplicate fills | trade id dedup; keys `event_id`, `ev:` / `fu:` / `hold:`, `episode_id` | E2E H: 0 rows written. Unit H: duplicated and reordered S1 |
| partial line | store cuts back to the last newline before appending; the S2 reader leaves a torn tail for the next run | unit G, S2 test |
| S2 rotation / rewrite | index keyed by inode, size and first-line hash; any change → full re-read | unit test |
| S1 losing or changing a recorded fill | **halts, writes nothing**, status `HALTED_S1_DISAGREES_WITH_STORE` | unit H. Found in testing: a lost close fill re-derived the episode as open and would have written 31 HOLD rows for a position that no longer existed |
| concurrent runs | flock on `runtime/lock` with inode re-check | unit |

A second defect was found by the restart test and fixed: hold-snapshot MFE/MAE read only the
current chunk's bars, not the whole episode. There is a regression test.

## J. Backfill

All 58 S1 fills, which make 47 order events, are recorded with `backfilled = true`. Only layers
that exist as of each fill are filled in:
- **public market:** all OK;
- **collector:** OK for 288 snapshots, STALE for 3, NOT_AVAILABLE for 3 226 (before the collector
  existed);
- **15s:** NOT_AVAILABLE for every backfilled event.

Nothing is reconstructed or estimated. Store after the backfill: 3 517 snapshots, 7.4 MB.

## K. Resource usage (local, Python 3.12 as on the host)

| run | RSS | wall | public requests |
|---|---|---|---|
| steady state, nothing due | 38 MB | 0.17 s | 0 |
| steady state, one hold snapshot (5-day-old position) | 38 MB | 0.8-1.9 s | 5-6 small tail reads |
| backfill, 500 per run | 28 MB | 0.2-1.8 s | ≤ 12 |
| backfill, everything in one run | 60 MB | 41 s | (unbudgeted; production uses `--max-snapshots 500`) |

- Unit limits: `MemoryHigh=64M`, `MemoryMax=96M`, `CPUQuota=25%`, `Nice=10`.
- Standard library only: no numpy, no requests.

**Rate safety.** A local test loop drew an HTTP 429 from Binance. The production host shares its IP
with the terminal's Binance reads, so the market reader was rebuilt:
- chunked cache, where an open chunk is extended by its tail only;
- ≤ 12 requests per run with 0.25 s spacing;
- 429/418 persisted as `backoff_until_ms` and respected by every later run;
- a budget or backoff defers snapshots instead of writing them as absent.

All four are unit-tested.

## L. Tests

`backend/tests/crypto/test_manual_snapshot_v1.py`: **24 passed**.
- Scenarios A-H on synthetic data.
- Manual / ExitGuard / external separation.
- Duplicates, partial fills, add / reduce / close / reverse.
- Restart and chunking parity.
- Torn lines, S2 rotation, missing collector and venue outage.
- **No future leak:** data after as_of is perturbed and the snapshot is unchanged.
- MFE / MAE / ATR / PnL hand checks.
- BTC-only, single-writer lock.
- Unclassified-order wait.
- Budget convergence, backoff persistence, tail-only fetch.
- **No import** of any live / terminal / paper / collector / Q123 / HOT / C1 module (AST).
- Writes only in the store, the market cache and the S2 index.
- GET only.

Offline E2E on production copies (`docs/crypto/manual_trade_snapshot_v1/e2e/e2e_prod_copy.py`):
- **A, B, C, E (real history):** 47 events, 7 multi-fill orders, Σ episode gross = Σ S1
  realized exactly.
- **D:** reduce then close.
- **F:** ExitGuard mixed in → that event is EXITGUARD and its episode is not manual-only; others
  are unchanged.
- **G:** restart parity.
- **H:** replay writes 0.

**All pass.**

## M. Changed files

- `backend/app/crypto/manual_snapshot_v1/{__init__,sources,episodes,market,collector,features,store,recorder}.py`
- `backend/tests/crypto/test_manual_snapshot_v1.py`
- `deploy/manual_trade_research/{RELEASE_FILES.txt, manual_trade_research.env, stage_release.sh, usb-crypto-manual-research.service, usb-crypto-manual-research.timer}`
- `docs/crypto/manual_trade_snapshot_v1/{CONTRACT_MANUAL_TRADE_SNAPSHOT_V1.md, e2e/e2e_prod_copy.py, MANUAL_TRADE_SNAPSHOT_V1_REPORT.md}`

## N. Commits

- d6349e7 `research(crypto): freeze manual trade snapshot store v1`
- 7072933 `feat(crypto): record manual trade research snapshots`
- the docs commit carrying this report

None are pushed (no HTTPS credential on this host).

## O. Production deploy plan (for approval; nothing done)

1. **Preflight (read only).**
   - available memory, PSI, crypto-paper MemoryCurrent and `memory.events max`;
   - the timer name is free;
   - `/root/usb_runtime/manual_trade_research` is absent;
   - confirm the S2 fingerprint directory (`487ea08e`).
2. **Release.** `deploy/manual_trade_research/stage_release.sh <commit>`, scp, and verify the
   MANIFEST on the host. The layout is `/root/usb_runtime/manual_trade_research/{src,config(600),store,logs}`
   with `store` at mode 700.
3. **Install.** Install the unit and the timer. The env file has no secret. The trigger URL is
   **empty (OFF)** in the shipped env file (predeploy finalization, 2026-10-12): a non-empty value
   makes the terminal send signed Binance reads every 5 minutes on the recorder's behalf. S1 is
   refreshed only by the UI's own requests. Before install, confirm on the host that the installed
   env reads `MTR_TRIGGER_URL=` and that the first run prints `"trigger": null`.
4. **Manual first run** of the oneshot. It does the backfill in 500-snapshot chunks over a few runs.
   Check:
   - events 47 and episodes 20, as locally;
   - `market_status`;
   - RSS from `memory.peak` sampled during the run.
5. **Timer.** Enable the timer (`*:*:20`) and watch two runs. The steady state should be ≤ 2 s, ≤ 40
   MB and ≤ 6 public requests per 5 minutes.
6. **Isolation proof.** These must be unchanged before and after:
   - PIDs and NRestarts of crypto-paper, context, q123, frontend, backend;
   - the S1 / S2 sha (S1 may change only through the terminal's own refresh);
   - BTC LIVE position identity, armed state, exit guard, trade_requests 0.
7. **Rollback.** `systemctl disable --now usb-crypto-manual-research.timer`. Nothing else depends on
   the recorder, and the store can stay.

## P. Final verdict

**C. READY_FOR_PRODUCTION_READ_ONLY_RECORDER.** All tests and offline E2E scenarios pass, steady
RSS is 38 MB (≤ 50), and the release, unit, timer and plan exist. **Not deployed; waiting for the
user's approval.** No edge conclusion is drawn: 16 manual-only closed episodes are below the 20
gate.
