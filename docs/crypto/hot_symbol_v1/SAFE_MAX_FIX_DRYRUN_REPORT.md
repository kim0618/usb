# Dynamic HOT V1 · Safe MAX Fix + LIVE Read-Only / Dry-Run Validation

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state: Production HOT V1 PASS. Production code, `origin/main` and `origin/feat/dynamic-hot-v1` are all `fa67437`. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md).

Date: 2026-10-10 (fix, server re-validation) / 2026-10-11 (PAPER E2E, restart audit, lock fix)
Branch: `feat/dynamic-hot-v1` (isolated clone, push disabled)
Verdict: **B. SAFE_MAX_FIX_PASS** (C blocked by a PAPER restart defect, section V)

No LIVE order, CLOSE, leverage write, AUTO change or account change was made. No deploy, no
production restart.

## A. Root cause

One choke point priced every LIVE margin figure: `adapter → sizing → preview` took
`symbolConfig.leverage` (the account setting) as the leverage. Nothing capped it at what the
leverage bracket allows. GTCUSDT reads 20x configured against a 10x first band, so required margin
was halved and Safe MAX roughly doubled (fixture 100 USDT: 1,980 → correct 995). BTC (150x band)
never showed it.

| Input | Source before | Source after |
|---|---|---|
| configured leverage | `GET /fapi/v1/symbolConfig` | unchanged |
| leverage bracket | read for display only | `GET /fapi/v1/leverageBracket`, parsed and validated (`live/brackets.py`) |
| Safe MAX leverage | configured | `effective_leverage(configured, brackets, notional)` |
| order preview leverage | configured | same function, same snapshot |
| order builder | quantity only (no leverage on the wire) | unchanged; preflight uses the same effective leverage |

## B. Effective leverage contract

`effective = min(configured, allowed(bracket_notional))`. `bracket_notional` is the whole position
the order would leave on that side (held + order), valued at max(fill, mark).

## C. Bracket algorithm

The brief asked for a fixed-point iteration. The implementation does not need one, by design:
- Safe MAX already searches over candidate quantities. Each candidate's notional fixes its band, the band fixes the leverage, and the leverage fixes the margin.
- Bands are validated as contiguous, starting at 0, with leverage non-increasing as notional grows. So required margin is monotone in quantity. The search cannot oscillate at a boundary and terminates deterministically.

Fail closed:
- No table or a malformed table returns `LEVERAGE_BRACKET_UNKNOWN`. This covers a missing row, another symbol's row, a non-number, a gap, a non-zero first floor, or leverage rising with notional.
- A notional past the last band returns `NOTIONAL_ABOVE_MAXIMUM`.
- Neither falls back to the configured leverage. The pure functions take brackets with no default.

## D–G. Per-symbol results (production account, read-only, 2026-10-10 14:06–14:10 UTC)

| | tick / step | min notional | bracket max | configured | effective | commission m/t | position / open orders |
|---|---|---|---|---|---|---|---|
| BTCUSDT | 0.10 / 0.001 | 50 | 150x (12 bands) | 20x | 20x (unchanged) | 0.02% / 0.05% | LONG 0.108 / 0 |
| CFXUSDT (HOT #1) | 0.00001 / 1 | 5 | 75x (11) | 20x | 20x (unchanged) | 0.02% / 0.05% | flat / 0 |
| BATUSDT | 0.00001 / 0.1 | 5 | 50x (9) | 20x | 20x (unchanged) | 0.02% / 0.05% | flat / 0 |
| GTCUSDT (Binance-only) | 0.00001 / 0.1 | 5 | **10x** (6) | 20x | **10x** | 0.02% / 0.05% | flat / 0 |

Precision checks:
- BTC price precision 2, quantity precision 3; CFX 7/0; BAT 6/1; GTC 6/1.
- MARKET_LOT_SIZE was read separately (for example BTC market max 120).
- Real availableBalance is 0, because the BTC position uses all margin.

## H. Safe MAX (fixture balances, production metadata and depth)

| Symbol | balance | effective | max qty | notional | margin | fee | slippage |
|---|---|---|---|---|---|---|---|
| BTC LONG | 100 | 20x | 0.022 | 1,820.31 | 91.02 | 0.91 | 0 |
| CFX LONG | 100 | 20x | 35,236 | 1,980.12 | 99.01 | 0.99 | 1.27 |
| BAT LONG | 100 | 20x | 15,218.6 | 1,980.18 | 99.01 | 0.99 | 0.70 |
| GTC LONG | 100 | **10x** | 4,998.3 | **994.99** | 99.50 | 0.50 | 0.33 |
| GTC LONG | 500 / 1000 | 10x | 21,500.2 | 4,280.79 | 428.08 | 2.14 | 3.11 (book depth limit) |

- BTC, CFX and BAT match the legacy figure exactly. Only GTC changes: legacy was 1,980.17.
- Every quantity is on its step and above its min notional.
- No BTC value is reused for a HOT symbol.
- The bracket boundary cases (CFX 500 → band 2, 1000 → band 3) priced cleanly.

## I. Order preview parity

Preview, preflight and Safe MAX use the same effective leverage, in tests and on production. GTC shows: preview leverage 10, configured 20, notional 996.82, margin 99.68.

The displayed leverage, notional, quantity, margin, fee, slippage and break-even all come from that one leverage.

## J–L. Dry-run payloads (built, then stopped before the transport)

- **LONG and SHORT:** MARKET BUY and SELL with the symbol's own quantity. For GTC, SHORT preflight was feasible at 10x (notional 992.21). LONG was INSUFFICIENT_MARGIN at the moment of the run, because the price moved between sizing and preflight. That is correct refusal behaviour.
- **CLOSE:** reduceOnly MARKET, built from fixture positions.

| Fixture position | CLOSE payload |
|---|---|
| GTC LONG 100 | `SELL 100 reduceOnly=true` |
| CFX/BAT SHORT 1000 | `BUY 1000 reduceOnly=true` |
| real BTC LONG 0.108 | `SELL 0.108 reduceOnly=true` |

- A HOT CLOSE produced no BTC payload, and the reverse also held. Tests cover this.
- The read-only transport refused a control POST `/fapi/v1/order` before it reached the network. Dry-run writes attempted on the wire: 0.

## M. BTC/HOT isolation

- PAPER: BTC LONG 0.002 was unchanged through HOT LONG → CLOSE → rescan. Tabs never showed another symbol's figures.
- Production: BTC `positionAmt`, `entryPrice` and `updateTime` were identical before and after.

## N. Binance-only symbol (GTC)

All of these were built from the Binance path alone, with no Bybit metadata: filters, brackets, commission, positions and open orders, Safe MAX, preview, and dry-run LONG/SHORT/CLOSE. The PAPER margin table came from account brackets.

## O. HOT lock

Policy holds in tests and the live terminal:

| State | Scan |
|---|---|
| BTC position or BTC order | allowed |
| HOT position / open order / in flight | blocked |
| HOT state unknown | blocked |
| HOT flat | allowed |

A state change during a scan keeps the old HOT symbol.

**New defect found and fixed (V1 code):**
- `PaperExposure` read a missing paper session as flat.
- After a restart where the HOT run failed to recover (section V), the lock said `HOT_SYMBOL_FLAT` while the HOT run held a LONG. `POST /hot/scan` returned 200, so a different #1 would have replaced the HOT symbol and hidden the position. This was reproduced in the local terminal with RLCUSDT LONG 20.4.
- Fix: a missing session with a run directory on disk raises, so the lock reads `HOT_STATE_UNKNOWN`. A symbol that never traded is still flat.
- After the fix, the same on-disk state gives `HOT_STATE_UNKNOWN` and scan 409.
- New tests fail with the fix reverted.

## P. Resource lifecycle

- The 20-replacement test still passes, with no leak in feeds, sockets, sessions, cache pages or LIVE runtimes.
- Replaced symbols are refused with 400, and the browser made 0 requests for the old symbol after a switch.

## Q. PAPER E2E (local real terminal, real Binance/Bybit public data, 1440px real clicks)

| Step | Result |
|---|---|
| A | BTC LONG 0.002 held, HOT (seeded BATUSDT) flat, scan enabled |
| B0 | scan replaced BAT with real #1 RLCUSDT; old symbol 400, not permitted, 0 browser requests |
| B | HOT tab: Binance feed and chart; LONG 20.4 RLC; BTC unchanged |
| C | button disabled immediately; direct POST returned 409 `HOT_POSITION_OPEN` |
| D | CLOSE → flat; button re-enabled immediately; BTC unchanged |
| E/F | double-click rescan: 1 POST; #1 still RLC (market decided); reload keeps HOT and selection |

- Browser non-GET requests: only `/hot/scan` and the PAPER `/order`. Binance LIVE non-GET: 0.
- UI at 390, 1440 and 1600: overflow 0, the tab strip fits, exactly one selected tab before and after reload, and sub-dollar mark prices (0.98352) render.
- Tabs are `[BTC] [🔥 HOT]` plus the `[코인탐색]` button. There are no ETH/SOL tabs.
- Evidence is in `paper_e2e_20261011/`.

## R. LIVE read-only re-validation (after the fix)

- 287 requests (166 signed), all GET, non-GET sent 0, BTC identity unchanged.
- Evidence: `server_readonly_postfix_20261010/`.

Two incidents during the run, reported as they happened:
- **First attempt hit Binance HTTP 429** (`-1003`, IP limit) and aborted. The production service showed `rate_limited 0`, `errors 0` and no 429 lines in the journal at the time. Pacing by weight was added and the second attempt passed (max used weight 707 of 2,400).
- **One signed `GET /fapi/v1/leverageBracket` returned 400.** All four symbols' brackets were received normally (`brackets_error null`). Which call returned 400 could not be recovered after the scratch files were lost, so its cause is unresolved.

## S. Mutation 0

Order, CLOSE, leverage, AUTO, account: all 0. Before and after, these were identical:
- positions, arm, exit-guard, c1-auto, BTC fills, trade request counter (0)
- the `leverage_capability.json` and `exit_guard.json` hashes
- service PIDs and NRestarts

The LIVE event log gained 5 `LIVE_RECONCILE reason=TTL` lines. These come from the baseline status GETs to the production API, which is a known side effect and not a mutation.

## T. Tests

- Backend crypto: 2,150 passed, 1 xfailed, 34 skipped.
  - 4 failed in `test_crypto_multisymbol`. These are the pre-existing failures caused by gitignored ETH/SOL reference files missing in the clone, the same set as before.
  - New since V1: `test_safe_max_brackets.py` (Safe MAX: below / equal / above band, multi-band boundary, monotone feasibility, missing / malformed / foreign table, balance 0, fixture balances, preview parity, HOT shared path, CLOSE isolation, exact wire payload) and the 3 lock/restart tests.
- Frontend: 1,076 tests with 1 known-flaky integration case under full-suite load. That file alone passed 33/33 three times. tsc passes.

## U. Changed files / commits

- `b1893a0` fix(crypto): price LIVE margin at the leverage the position's band allows
- `36acfed` test(crypto): Safe MAX brackets and dry-run payloads
- **Uncommitted:**
  - `backend/app/crypto/terminal/hot_routes.py` (the lock fix)
  - `backend/tests/crypto/test_hot_symbol_v1.py` (3 tests)
  - this report and its evidence folders

## V. Verdict: B. SAFE_MAX_FIX_PASS

The Safe MAX, metadata, preview, dry-run payload, isolation and LIVE mutation-0 conditions are met. **C is blocked by one PAPER defect** that HOT trading makes routine.

**PAPER restart refuses the HOT run (LEDGER_DIVERGENCE).** This is pre-existing since `735d0a9` and is in production `ec3df85`.

Mechanism:
- Each symbol's ledger records shared-wallet figures (`available_balance`, `equity`).
- At boot each run replays alone, without the other members, so its rebuilt ledger differs and `recover()` refuses.

Reproduced three ways:
- origin/main `5d7863b` with ETH then BTC
- local terminal: BTC LONG + HOT trade → restart → RLC refused
- a strict xfail test

Consequences after a restart:
- The HOT run is not loaded. Its PAPER position is hidden: state returns 500. Its realised PnL and fees drop out of the wallet, so the balance reads wrong.
- Joining stops at the first refused run.
- In the other order (another member's PnL recorded in BTC's ledger), the BTC run itself is refused and PAPER plus C1 AUTO are down.

Scope and current exposure:
- The LIVE backend does not depend on the paper session (it borrows only the KRW rate), so LIVE orders are not affected.
- Production today is not exposed: the ETH and SOL ledgers hold only their start line.
- After deploy, though, the first PAPER HOT trade while BTC holds margin creates the condition. The next `usb-crypto-paper` restart then triggers it.

Fix direction (needs approval, core PAPER persistence): replay all wallet members jointly in global order with the shared cash attached from the start. The alternative of dropping wallet figures from the prefix check weakens the integrity guard and is not recommended.

## W. Production deploy plan (not executed)

1. Decide on the restart defect first: fix it (joint replay), or deploy with PAPER HOT trading accepted as restart-unsafe.
2. Commit the lock fix. Build the frontend from a clean tree off-server with production env. `BINANCE_LIVE_HOT_ORDERS` stays unset (OFF).
3. Before the deploy, check:
   - the nginx `/api/crypto/` prefix covers `/api/crypto/hot*`
   - the server has fapi egress (already proven by this validation)
   - RAM headroom
4. Copy the runtime backend to `/root/usb_runtime/crypto_paper/src`, keeping the previous copy as rollback. Swap the frontend artifact, keeping the previous `.next` as rollback.
5. Restart `usb-crypto-paper` and `usb-frontend` only. Verify the BUILD_ID, the `/api/crypto/hot` GET and the BTC position identity, with mutation 0.
6. The first real HOT order is placed manually by the user, after turning `BINANCE_LIVE_HOT_ORDERS` on with approval. Margin must be freed first, because availableBalance is 0.
