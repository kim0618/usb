# CRYPTO UI POLLING OPTIMIZATION V1: production deploy record (2026-10-13)

## What changed

Backend (`/root/usb_runtime/crypto_paper/src/backend/app/crypto`, five files replaced, nothing else):

| file | change |
|---|---|
| `terminal/server.py` | `GET /sizing` prices on `trade_preview.advanced()` clone; no tape record, no fsync |
| `paper/segments.py` | `SegmentedTape.storage()` optional TTL cache, dropped on rotate (default 0 = off) |
| `terminal/session.py` | terminal sessions use `STORAGE_TTL_MS = 10_000` |
| `terminal/live_routes.py` | `GET binance/sizing` uses snapshot tiers (`force=False`); `LiveExposure.recent_exposure` for display |
| `terminal/hot_routes.py` | `GET /hot?source=PAPER` leaves exchange sources out; `source=BINANCE_LIVE` reuses a signed read 30 s; scan unchanged |

Unchanged on purpose: `preflight_open` still forces a full account read before an OPEN; the paper order
route still forces its observation; the HOT scan reads every source before and after. The four 8551d5b
files (`c1/runtime.py`, `c1/sources.py`, `hot_coins/market.py`, `terminal/chart_history.py`) were not
touched; their runtime sha256 prefixes were identical before and after (3516e21e, 4e6262e3, f363f62c,
f70333c9).

Frontend: built locally from a clean `git archive 3e2a8ad` (the deployed base: the running build held a
3e2a8ad string and no 5093f6c string) plus the polling files only, `NEXT_DIST_DIR=.next-build`, production
`NEXT_PUBLIC_*` values. BUILD_ID `w3LLcmPdetY4iarG7TXCX` (was `CJzmsJll8gufq0NNjZR91`).

| cadence | before | after |
|---|---|---|
| state | 1 s | 1 s |
| sizing | 1 s | 5 s |
| ledger / performance / trades | 1 s | 10 s |
| pnl-breakdown | 1 s | 5 s with a position, once otherwise |
| paper routes on the LIVE screen | 1 s | none |
| hidden tab | everything kept polling | `lib/visible-poll.ts` stops every loop; visible reads at once |
| chart seed | 2,000-2,880 bars, 15 s retry | 500 bars, 15/30/60/120 s backoff |

## Deploy

- 15:13:15 KST backend backup `releases/before-polling-v1-20261013-151315/` (MANIFEST.txt with sha256)
- 15:13:32 `systemctl restart usb-crypto-paper` (health after 48 s, NRestarts 0, LIVE `DISARM BOOT` 15:14:09)
- 15:15:41 frontend backup `/root/usb/frontend/.next-build.pre-polling-20261013-151541`, restart `usb-frontend`

## Production QA (one headless tab through an ssh tunnel and a GET-only local proxy)

| phase | req/min | binance/* | paper-only routes | LIVE_RECONCILE |
|---|---|---|---|---|
| PAPER visible 120 s (paper SHORT open) | 320 (170 of them `/live` 333 ms, position-gated) | 1 (`status` on mount) | - | 1 |
| LIVE visible 60 s | 200 | 104 | 0 | 2 |
| hidden 90 s | 1 (a run already in flight) | 1 | 0 | 0 |
| LIVE back 30 s | 234 | 60 | 0 | 1 |
| PAPER back 30 s | 342 | 3 (in-flight tails, < 0.9 s after the switch) | - | 1 |

Before the deploy the LIVE screen produced LIVE_RECONCILE 12-15/min (binance/sizing forced a full read
every 5 s). `/sizing` x10 in 20 s: tape +0.89 lines/s (the 1 Hz loop alone), ledger +0 bytes. Chart seed
`limit=500`: all 200. 429/418 after the restart: 0 (the 16 mirror lines matching "429"/"418" before it are
digits inside prices, every one with `blockers: []`).

## Rollback

```
# backend
B=/root/usb_runtime/crypto_paper/releases/before-polling-v1-20261013-151315
R=/root/usb_runtime/crypto_paper/src/backend/app/crypto
cp -p $B/terminal/*.py $R/terminal/ && cp -p $B/paper/segments.py $R/paper/
(cd $B && sha256sum -c MANIFEST.txt --ignore-missing)   # then compare $R files to the manifest
systemctl restart usb-crypto-paper
# frontend
cd /root/usb/frontend && mv .next-build .next-build.polling-v1 && mv .next-build.pre-polling-20261013-151541 .next-build
systemctl restart usb-frontend
```

## Follow-up issues (not part of this deploy)

- **E-RVOL sqlite thread sharing**: `RvolStore.staged_history` raises `ValueError: not enough values to
  unpack` from worker threads sharing one connection (once in serve's history, twice on the first `once`
  run, which then finished on one worker at 3.42/s). The same run then re-bootstrapped one symbol every
  ~24 s (105 pages each) until a Kiwoom listing error ended it with exit 1 at 01:59 ET; serve restarted
  itself (`Restart=always`), the oneshot waits for the 10:00 ET run. Append itself was complete
  (2,565 symbols, store quick_check ok, no duplicate keys).

## Next candidates, in order

A. `PaperSession.observe` -> `maybe_rotate` -> `should_rotate()` -> `active_records()` reads the whole
   active tape (4.5 MB at the time) every second per symbol, UI or not. A record counter kept in memory
   removes it. Locally, with no request at all, the paper process still used 3.8% CPU.
B. `order-preview` re-requests whenever the quote timestamp moves (about 54/min on a flat PAPER screen).
C. LIVE signed requests: still about 60/min flat, 75-85/min with a position (account fast tier every
   2 s, positions 5 s, position card userTrades/income 5 s).
