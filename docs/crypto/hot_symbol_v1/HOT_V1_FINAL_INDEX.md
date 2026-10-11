# HOT Coin Finder / Dynamic HOT Trading V1 · Final Index

The entry point for all HOT work. The reports linked below are historical records. Each has a FINAL STATUS note at the top, and this page states the current truth.

## 1. Final status (2026-10-11)

| Item | State |
|---|---|
| HOT Coin Finder R0 | **C. FEASIBLE_FOR_IMPLEMENTATION** (scoped to a read-only scanner at the time; trading was later done as V1) |
| HOT Coin Finder V0 | READY_FOR_PRODUCTION_READ_ONLY, later superseded by V1 |
| Dynamic HOT Trading V1 | **READY_FOR_PRODUCTION_MANUAL_TRADING** |
| Production | **PRODUCTION_HOT_V1_PASS** (deployed 2026-10-11 13:05 KST) |
| Safe MAX | **bracket-aware PASS**: effective leverage = min(configured, band allowed); GTC 20x → 10x |
| Multi-symbol PAPER recovery | **PASS**: shared-wallet joint replay, verified on production restart |
| BTC fixed main | YES |
| ETH/SOL fixed tabs | removed from the UI; backend support kept, and a held ETH/SOL position still gets a temporary tab |
| HOT symbol | one persistent dynamic slot, server-side `hot_slot.json`, replaced only by a guarded scan |
| HOT LIVE orders | **OFF**: `BINANCE_LIVE_HOT_ORDERS` unset; server refuses with 409; LIVE screen closes HOT order and leverage controls |
| First real HOT LIVE order | **NOT YET**: the user will place it manually |

HOT lock (scan permission):
- BTC position or BTC order: never blocks; BTC is not even read.
- HOT position, HOT open order, HOT order in flight: blocks.
- HOT state unknown, including a HOT run that failed to load: blocks.
- HOT flat: allowed. A state change during a scan keeps the old HOT symbol.

## 2. Authoritative commit

**`fa67437`** is the same everywhere:
- production deployed code (crypto runtime source and frontend BUILD_ID `SBSvXJ3c0ExwLDe6Y08Jq`)
- `origin/main`
- `origin/feat/dynamic-hot-v1`

Docs and evidence commits after it change no feature code.

## 3. Chronology

| When (KST) | Step | Result |
|---|---|---|
| 10-10 | R0 feasibility: public data, 5m backfill, ranking contract | C, read-only scanner |
| 10-10 | V0 read-only scanner `[코인탐색]` | READY_FOR_PRODUCTION_READ_ONLY (not deployed) |
| 10-10 | V1 dynamic HOT slot, Binance market data, HOT PAPER | B PAPER_READY |
| 10-10 22:46 | Production LIVE read-only validation #1 | B LIVE_READ_ONLY_PASS; found the Safe MAX bracket defect |
| 10-10 23:03 | Safe MAX bracket fix, then production read-only #2 | SAFE_MAX_FIX_PASS; first attempt hit a Binance 429 and was rerun with pacing |
| 10-11 | PAPER E2E and restart audit | found the PAPER shared-wallet restart refusal and the lock fail-open |
| 10-11 12:33 | lock fail-closed | `dae6dfd` |
| 10-11 12:51 | global chronological wallet replay | `4a5213b`; MULTISYMBOL_RECOVERY PASS |
| 10-11 12:59 | LIVE UI closes HOT order controls while the switch is off | `fa67437` |
| 10-11 13:05 | Production deploy | PRODUCTION_HOT_V1_PASS |
| 10-11 | feature branch push, then `main` fast-forward (user push) | main = feat = `fa67437` |

## 4. Commits (on top of `5d7863b`)

| Commit | Subject |
|---|---|
| `322d7db` | feat(crypto): add read-only hot coin finder |
| `52b6758` | docs(crypto): record hot coin finder R0 feasibility |
| `f2218a4` | feat(crypto): replace fixed alt tabs with persistent hot symbol |
| `f37ce1e` | feat(crypto): support dynamic hot symbols in terminal |
| `b0309b6` | test(crypto): cover hot symbol trading isolation |
| `b1893a0` | fix(crypto): price LIVE margin at the leverage the position's band allows |
| `36acfed` | test(crypto): cover bracket-capped Safe MAX and dry-run order payloads |
| `dae6dfd` | fix(crypto): fail closed on unavailable hot position state |
| `4a5213b` | fix(crypto): replay paper ledgers in global chronological order |
| `fa67437` | fix(crypto): close HOT LIVE order controls while the switch is off |

## 5. Reports

| Report | Phase |
|---|---|
| [HOT_COIN_FINDER_R0_REPORT.md](../hot_coin_finder_r0/HOT_COIN_FINDER_R0_REPORT.md) | R0 feasibility: ranking contract, gates, weight budget, traps |
| [HOT_COIN_FINDER_V0_REPORT.md](../hot_coin_finder_v0/HOT_COIN_FINDER_V0_REPORT.md) | V0 read-only scanner |
| [DYNAMIC_HOT_TRADING_V1_REPORT.md](DYNAMIC_HOT_TRADING_V1_REPORT.md) | V1 implementation and PAPER |
| [server_readonly_20261010/README.md](server_readonly_20261010/README.md) | production LIVE read-only #1 |
| [SAFE_MAX_FIX_DRYRUN_REPORT.md](SAFE_MAX_FIX_DRYRUN_REPORT.md) | Safe MAX fix, read-only #2, dry-run payloads, PAPER E2E, restart audit |
| [MULTISYMBOL_RECOVERY_V1_REPORT.md](MULTISYMBOL_RECOVERY_V1_REPORT.md) | lock fail-closed and shared-wallet joint replay |
| [PRODUCTION_DEPLOY_HOT_V1_20261011.md](PRODUCTION_DEPLOY_HOT_V1_20261011.md) | production deploy and QA |
| [EVIDENCE_MANIFEST.md](EVIDENCE_MANIFEST.md) | every evidence file with its sha256 |

## 6. Safe MAX fix

- `effective = min(configured, allowed(band of the whole resulting position at max(fill, mark)))`.
- Bands are validated as contiguous and non-increasing, so feasibility is monotone and there is no oscillation.
- A missing or malformed table refuses with `LEVERAGE_BRACKET_UNKNOWN`; a notional past the last band refuses with `NOTIONAL_ABOVE_MAXIMUM`.
- Sizing, preview and preflight use one leverage.
- Production check: GTC goes from 1,980 to 995 USDT at a 100 USDT fixture. BTC, CFX, BAT and STRK are unchanged.

## 7. Multi-symbol recovery fix

- Every PAPER ledger records shared-wallet figures, but boot replayed each run alone. A HOT run traded beside a BTC position, or BTC after another member's PnL, was refused.
- Fix: runs that touched the wallet recover together.
  - Each starts from a checkpoint at a common cut with no wallet event in between.
  - Records are merged by (`ts_ms`, owner first then symbol, each run's own order).
  - The per-ledger byte check stays, so a disagreement refuses.
- With no members the old path is byte-identical.
- SharedCash sums run in a fixed order.
- A traded HOT run is checkpointed when it is retired.

## 8. Production QA (2026-10-11)

- PAPER:
  - scan selected STRKUSDT
  - LONG → lock 409 → CLOSE → SHORT
  - process restart: BTC and STRK identical, both `WALLET_REPLAY` (BTC from checkpoint 67)
  - CLOSE → `HOT_SYMBOL_FLAT`
- Recovery was pre-validated on a copy at the same path inside an isolated mount namespace.
- LIVE read-only: 78 GET requests, non-GET 0, errors 0.
- UI at 390/1440/1600, through a GET-only proxy (POST control returned 405):
  - overflow 0, exactly one selected tab, sub-$1 prices correct
  - LIVE HOT controls closed
- Mutation 0: trade_requests 0→0. LIVE BTC 0.108 @ 83,913.1 unchanged.

## 9. Known limitations

- **Merge ordering:** two symbols' fills within about 1s across exchange quote clocks cannot be ordered. Recovery refuses, fail closed.
- **Arm button on the HOT tab:** the LIVE HOT tab's top bar still offers the global arm button. Arming opens BTC only.
- **🔥 emoji:** headless browsers render it as a box. It needs one check in a real browser.
- **CASE B:** production BTC full replay (schema v2→v3) is not fixed. The new path needs a usable checkpoint.
- **Unexplained 400:** one signed `leverageBracket` call returned 400 during read-only #2. It did not recur.

## 10. HOT LIVE switch

`BINANCE_LIVE_HOT_ORDERS` is unset in the process environment, the env file and the unit. Setting it to `on` and restarting `usb-crypto-paper` is the user's decision. Nothing else changes.

## 11. First manual live trade (remaining)

1. Free margin: availableBalance is 0 because BTC LONG 0.108 uses it.
2. The user sets `BINANCE_LIVE_HOT_ORDERS=on` and restarts `usb-crypto-paper`.
3. The user places the first HOT order by hand, at minimum size.

## 12. Rollback

Not used.
- Backend: `/root/usb_runtime/crypto_paper/releases/before-hot-v1-20261011-130451/src-backend.tgz`.
- Frontend: `/root/usb/frontend/.next-build.pre-hot-20261011-130451` (BUILD_ID `sX7LPUBG-wazp9sit6KZi`).
- **Caveat:** production PAPER now has a STRKUSDT run that shared the wallet. The old backend recovers runs alone and would refuse it. A backend rollback must also move `run/paper-server-001-STRKUSDT` aside. PAPER only; LIVE is unaffected.
