# HOT V1 · Production Fixes (2026-10-11)

Two defects were found after the HOT V1 deploy. Both are fixed and deployed. Entry point: [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md).

| Fix | Commit | Deployed (KST) | Verdict |
|---|---|---|---|
| Sub-dollar HOT: PAPER Safe MAX 0 and 15 s axis -6..+6 | `c5fb88b` | 13:46 (backend `paper/sizing.py` + frontend) | HOT_SUBDOLLAR_FIX_PRODUCTION_PASS |
| BTC/HOT 15 s -> 1m showed one or two candles | `37c1c49` | 14:08 (frontend only) | TIMEFRAME_RANGE_FIX_PRODUCTION_PASS |

No LIVE order, CLOSE, leverage, AUTO or account change during either deploy. The HOT LIVE switch stayed OFF.

## 1. Sub-dollar HOT (`c5fb88b`)

Reported on production STRKUSDT (about 0.104 USDT, PAPER available 9,775 USDT): Safe MAX was "0.000 STRK", all quick sizes were disabled, and the 15 s axis read about -6..+6.

**Safe MAX root cause:** PAPER `max_entry` started its search at the minimum quantity. 0.1 STRK is about 0.01 USDT, far below the 5 USDT minimum notional, so the first probe failed and the search returned 0 with `NOTIONAL_BELOW_MINIMUM`.
- Balance, leverage and the margin table were all fine.
- LIVE sizing already started above the notional floor (`smallest_qty`).
- BTC never showed it: 0.001 BTC is about 83 USDT.

**Safe MAX fix:** the search now starts at the larger of:
- the minimum quantity
- the minimum notional divided by the entry quote, rounded up to the step

If a book walk falls short, it moves up a few steps. BTC is unchanged.

**Axis root cause:** the 15 s chart's minimum visible price span was a fixed 10 USDT. Volume was already on its own scale and was not involved.

**Axis fix:** the minimum span is 100 ticks. That is exactly 10 USDT on BTC (tick 0.10) and 0.001 on STRK.

**Not defects:**
- "Below minimum notional" for 0.1 STRK is correct. The smallest valid size is about 47.9 STRK at 0.1045.
- 15 s candles start "since selection" by design: they are built from live trades only.
- BTC quick sizes are disabled in production because C1 AUTO holds a PAPER SHORT (`REVERSE_NOT_ALLOWED`).
- Order-preview safe max equals sizing max at the same instant; any visible difference comes from book depth changing.

**Production check:**
- STRK Safe MAX > 0 (for example 30,360 STRK), with all quick sizes enabled.
- The 15 s axis reads 0.1038–0.1053.
- BTC 15 s minimum span is still 10.00, and BTC SHORT max is 1.171 (1.172 before, market movement).
- Recovery was pre-validated on an isolated copy before the restart; the restart used `WALLET_REPLAY`.

Evidence: [subdollar_fix_20261011/](subdollar_fix_20261011/) (before on production data, after on the local fixed stack, production redeploy).

## 2. Timeframe range (`37c1c49`)

**Symptom:** on BTC, switching 1m → 15s → 1m showed one or two huge candles. The first 1m load was normal (240 visible on 1440px). The deployed build before the fix behaved the same.

**Root cause, proven by an instrumented trace** (`timeframe_range_fix_20261011/before_btc.json`):
1. The 1m history hook kept its 2,880 candles while 15 s was shown and served them again for one render when the same series key came back.
2. The next render replaced them with the empty restart and the 120-bar live fallback. The chart saw the same key and held the 2,880-bar viewport onto 120 bars.
3. When history landed, the chart shifted that view by the 2,760 prepended bars, which put it past the newest bar.

**Fix:** the hook drops its candles when it leaves a series. Coming back starts from the fallback exactly like a first visit and lands on the intended initial range. Viewport holding within a series is unchanged.

The first regression test could not see the single stale render, because `renderHook` exposes the settled value only. The test now records every render, and it fails with the fix removed.

**Production check** (the deployed artifact on production data, GET-only proxy):

| Case | Visible candles |
|---|---|
| BTC 1m → 15s → 1m | 240 → 59 → 240 |
| BTC 1m → 1h → 1m | 240 → 240 → 240 |
| HOT 1m → 15s → 1m | 240 → 60 → 240 |
| 390px | 113 |
| 1600px | 294 |
| Reload | 240 |

Other results:
- 1m history held 2,880 bars.
- Zoom from 240 to 136 held through pan, the 15 s tail refresh, and a 32 s history outage (8 aborted requests) with recovery.
- Only a new timeframe applies the initial range.
- Overflow 0, selected symbol and timeframe correct, 0 browser writes.
- Only `usb-frontend` changed PID (1669735→1672252). The timer list was unchanged.
- LIVE events added were TTL reconciles only. `trade_requests` stayed 0→0. BTC LIVE 0.108 @ 83,913.1 was unchanged.

Evidence: [timeframe_range_fix_20261011/](timeframe_range_fix_20261011/), with traces before and after, regression runs, and `production_deploy/`.

## 3. Rollback (not used)

- Before the timeframe fix: frontend `.next-build.pre-tfrange-20261011-140835` (BUILD_ID `Br5dMBIGZx8OUb7jEZsph`).
- Before the sub-dollar fix: backend `releases/before-subdollar-20261011-134500/sizing.py`; frontend `.next-build.pre-subdollar-20261011-134500` (BUILD_ID `SBSvXJ3c0ExwLDe6Y08Jq`).
