# Dynamic HOT Trading V1 · Production Deploy (2026-10-11)

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state: Production HOT V1 PASS. Production code, `origin/main` and `origin/feat/dynamic-hot-v1` are all `fa67437`. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md).

Verdict: **PRODUCTION_HOT_V1_PASS**. Push is pending: this host has no GitHub credentials (see A).

HOT LIVE switch `BINANCE_LIVE_HOT_ORDERS`:
- **unset (OFF)** in the process environment, the env file and the unit
- the server refuses HOT orders with 409
- the LIVE screen closes the HOT order controls

No HOT LIVE order, LIVE CLOSE, leverage change, AUTO change, exit-guard change or account change was made.

## A. Git / push
- Deployed tree: `fa67437` on `feat/dynamic-hot-v1`, a direct descendant of `origin/main` = `5d7863b`.
- Ancestry verified for the Dynamic HOT V1 and Safe MAX commits: `322d7db f2218a4 f37ce1e b0309b6 b1893a0 36acfed dae6dfd 4a5213b fa67437`.
- `fa67437` was added in this deploy. It closes the HOT LIVE order controls on the screen while the switch is off. Before it, an armed operator saw LONG/SHORT/CLOSE and leverage enabled on the HOT tab, which is a FAIL under item 11. Covered by:
  - backend `_symbol_gates` test
  - frontend gate, ticket and leverage tests
  - mutation checks on both sides
- **Push not done:** `fatal: could not read Username for 'https://github.com'`. No credential helper and no `gh` on this machine, the same as the 10/10 deploy. The user should push with:
  `cd ~/usb-hot-coin-finder-r0 && git push https://github.com/kim0618/usb.git feat/dynamic-hot-v1`
  Fast-forwarding `main` is possible too, since it is a clean descendant of origin/main.

## B. Pre-deploy state
- Server repo `/root/usb`: `db41f04` on `main`, 4 dirty entries from other sessions. Untouched.
- crypto runtime source = the ec3df85 tree exactly, minus the never-deployed `liquidity_map` and `market_structure_v0` packages.
- Frontend BUILD_ID `sX7LPUBG-wazp9sit6KZi`.

## C. Deployed state
- **Backend:** exactly the 20 files ec3df85→fa67437 under `app/crypto`, with liquidity_map and market_structure_v0 excluded. Per-file sha256 verified after copy (`deploy_files.sha`). Runtime source sha `c544fda2…` → `6537948d…`.
- **Frontend:** clean `git archive fa67437 frontend`, node v20.20.1 and npm 10.8.2 (same as the server), only `NEXT_PUBLIC_API_BASE_URL` and `NEXT_PUBLIC_CRYPTO_API_BASE` set. BUILD_ID **`SBSvXJ3c0ExwLDe6Y08Jq`**, 19 routes (same set), 4.6MB, tarball sha256 `36b86c56…`.

## D. Build result
Artifact grep, with the same counts taken on the old deployed build as the control:

| Marker | New | Old (control) |
|---|---|---|
| `127.0.0.1:8000` | 0 | 0 |
| `127.0.0.1:8100` | 0 | 0 |
| `usb.jptcalc.kr` | 2 | 2 |
| `/crypto-api` | 2 | 2 |
| `8012` | 1 | 1 |
| HOT markers | present | absent |
| ETH tab | absent | present |

## E. Restarted services
| Service | Restarts | Notes |
|---|---|---|
| usb-crypto-paper | 2 | 1537967→1663746 (deploy, health 41s) and →1665078 (recovery test F, 44s); NRestarts 0 |
| usb-frontend | 1 | 1551071→1664258, stop/swap/start, 200 after 44s; NRestarts 0 |

nginx, usb-backend, context, liqfwd and e-rvol: not touched, PIDs unchanged.

## F. Production PAPER HOT
1. From BTC, a scan selected **STRKUSDT** (1H range 15.3%), with Binance feed, quote, chart and order preview working.
2. PAPER LONG 114.6 → `HOT_POSITION_OPEN`, scan 409.
3. CLOSE → flat.
4. SHORT 114.6 kept open for the restart test.
5. After the restart test: CLOSE → flat, `HOT_SYMBOL_FLAT`.

The PAPER BTC position (SHORT 0.064, C1 AUTO) was unchanged throughout.

## G. Restart recovery
- **Pre-validated first, before touching production.** A copy of the production run directory was bind-mounted at the same path inside an isolated mount namespace, so the live path had 0 mounts. Recovery there: BTC from checkpoint 67 via `WALLET_REPLAY`, STRK SHORT, every ledger hash equal to disk, wallet and available equal to production.
- **Then the production restart.** BTC and STRK positions, realised PnL, fees, funding, used margin, wallet `10310.54355…` and available all came back identical. Both used `WALLET_REPLAY` (BTC from checkpoint 67). C1 AUTO was unchanged.

## H. HOT lock
`HOT_POSITION_OPEN` held across the restart, scan was 409 while the position was open, and the lock read `HOT_SYMBOL_FLAT` after close.

## I. Safe MAX (production account, read-only, fixture balance applied to the pure sizing function only)
| Symbol | Configured | Bracket max | Effective | Fixture 100 USDT → notional |
|---|---|---|---|---|
| BTC | 20x | 150x | 20x | 1,821.98 |
| CFX | 20x | 75x | 20x | 1,980.12 |
| BAT | 20x | 50x | 20x | 1,980.18 |
| GTC | 20x | **10x** | **10x** (capped) | **994.99** |
| STRK | 20x | 75x | 20x | 1,980.18 |

These match the 10/10 read-only values, including GTC's 995. Real availableBalance is 0 (BTC uses the margin), so real Safe MAX is 0 `INSUFFICIENT_MARGIN`.

## J. Binance-only symbol (GTC, not listed on Bybit)
Filters, bracket, commission, position, open orders, chart (1m and 1h) and quote all read normally.

## K. UI
Checked at 390/1440/1600: the deployed artifact rendered on production data behind a GET-only proxy. Control: a POST through the proxy returned 405.

PAPER:
- Tabs `[BTC] [🔥 STRK HOT]` plus `코인탐색`, no ETH/SOL tabs.
- Overflow 0, exactly one selected tab before and after reload.
- Sub-$1 mark price shown correctly (0.104xx).
- HOT persists across reload.

LIVE on HOT:
- LONG/SHORT/CLOSE disabled, with "HOT 심볼 LIVE 주문은 꺼져 있습니다. 조회만 가능합니다."
- No activation link offered.
- All 8 leverage buttons disabled.

LIVE BTC is as before: locked only because the session is not armed.

Browser writes: 0. Proxy refusals: 0.

## L. LIVE read-only
78 requests (42 signed), all GET, non-GET 0, HTTP errors 0. The guard control (a POST to /order) was blocked before it reached the network.

## M. Mutation 0
- `trade_requests` 0 → 0.
- LIVE event log additions: `LIVE_RECONCILE` (FORCED/TTL, from reads), `LIVE_DISARM BOOT` ×2 (the two restarts), and stream state only.
- arm (false), exit-guard (OFF), c1-auto (`enabled_at` unchanged), leverage (20/20/20), BTC fills (17) and the PAPER BTC ledger hash (`d985b78e…`, 287 events): all unchanged.

## N. BTC position
LIVE BTC LONG 0.108 @ 83,913.1, update_time 1791339778021: identical before, during and after.

## O. HOT LIVE switch
- Before and after: process env UNSET, env file 0 mentions, unit 0 mentions.
- `/binance/status` gates now expose `symbol_orders_flag_name=BINANCE_LIVE_HOT_ORDERS`.
- `symbol_orders_enabled` is false on the HOT symbol, as shown in the UI.

## P. Other services
nginx 707, usb-backend 1556760, usb-crypto-context 1582777, usb-crypto-liqfwd 3250885, usb-e-rvol 2515148: PIDs unchanged, NRestarts 0.

## Q. Rollback
Prepared, not used.
- **Backend:** `/root/usb_runtime/crypto_paper/releases/before-hot-v1-20261011-130451/src-backend.tgz` plus the unit copy. Restore with:
  `tar xzf … -C /root/usb_runtime/crypto_paper/src && systemctl restart usb-crypto-paper`
- **Frontend:** `/root/usb/frontend/.next-build.pre-hot-20261011-130451` (BUILD_ID sX7LPUBG). Stop usb-frontend, mv, start.
- **Staging artifacts:** `releases/staging-hot-v1-20261011-130451/`.

Rollback caveat: production PAPER now has a STRK run that shared the wallet. The old backend recovers each run alone and would refuse STRK. Rolling back the backend therefore also needs that run directory moved aside. PAPER only; LIVE is unaffected.

## R. Remaining limitations
- Push pending (A).
- Disk 2.2G free / 77%. Memory available about 230 MB after deploy, with swap 418 MB used before.
- The top trade bar on the LIVE HOT tab still offers the global "거래 활성화" (arm) button. Arming opens BTC only; HOT stays closed by its own gate. A possible UX refinement.
- The headless browser renders 🔥 as a box (font). It needs one look in a real browser.
- The 400 on one leverageBracket call in the 10/10 validation stays unexplained. It did not recur here (0 HTTP errors).
- The residual merge case from the recovery work still applies. A cross-symbol fill within about 1s across exchange quote clocks is refused, not guessed.
- The first real HOT LIVE order needs, in this order:
  1. free margin (availableBalance is 0)
  2. the user's own decision to set `BINANCE_LIVE_HOT_ORDERS=on`
  3. a `usb-crypto-paper` restart
