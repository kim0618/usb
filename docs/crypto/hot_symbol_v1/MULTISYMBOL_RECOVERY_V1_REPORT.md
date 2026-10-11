# PAPER Multi-Symbol Recovery V1 + HOT Lock Safety

> **FINAL STATUS (added 2026-10-11, after the fact).** This report is kept as written at the time. Its verdict and any "not deployed", "not pushed" or "main not merged" statements are superseded. Current state (updated after the production fixes): production and `origin/main` are at `37c1c49`, and `main` is the only branch. HOT LIVE orders are OFF, and the first real HOT LIVE order has not been placed (the user will place it manually). Entry point: [HOT_V1_FINAL_INDEX.md](HOT_V1_FINAL_INDEX.md).

Date: 2026-10-11 · Branch: `feat/dynamic-hot-v1` (isolated clone, push disabled)
Verdict: **C. READY_FOR_PRODUCTION_MANUAL_TRADING** (deploy not done, waiting for approval)

Commits:
- `dae6dfd` fix(crypto): fail closed on unavailable hot position state
- `4a5213b` fix(crypto): replay paper ledgers in global chronological order

No LIVE order, no deploy, no production restart, no Binance call of any kind in this work. No change to AUTO logic, the ledger, tape or checkpoint format, or the LIVE code (`live/` diff is empty).

## 1. Lock safety (`dae6dfd`)

`PaperExposure` read a missing paper session as flat. A HOT run whose replay was refused at boot therefore read as `HOT_SYMBOL_FLAT`, and a scan could replace a HOT symbol that still held a position.

Fix: a missing session whose run directory exists raises, so the lock reads `HOT_STATE_UNKNOWN`.

| Case | Result |
|---|---|
| HOT recovery failure | scan 409, HOT kept |
| position unknown / open-order unknown / in-flight unknown | blocked |
| BTC unreadable | HOT scan unaffected; BTC is never read |
| never-traded HOT | flat |

## 2. Root cause

| Field in a ledger line | Owner |
|---|---|
| position qty, avg entry, leverage, fills, own realised PnL, fees, funding | the run (SYMBOL) |
| `available_balance`, `equity` on fills/closes; the margin check behind accept/reject | the wallet (GLOBAL): capital base + every member's realised PnL − charges, minus every member's posted margin |

Market ticks never change the shared part. It changes only at fills, fees, funding, liquidation and resets. Startup replayed each run alone, so a run's rebuilt wallet figures lacked the other members' money and `recover()` refused (LEDGER_DIVERGENCE).

Event-level example, real terminal data from this morning:

```
BTC  POSITION_OPEN 0.002   available 727.37   (BTC alone in wallet)
RLC  POSITION_OPEN 20.4    available 719.03   (BTC margin already posted)
restart: RLC replayed alone -> rebuilds 727.3x on that line -> refused
```

The reverse order breaks BTC itself, which takes down PAPER and C1 AUTO. That was reproduced in test E and on origin/main.

## 3. Global chronological replay (`paper/wallet_recovery.py`)

- **Members:** the owner plus every run whose ledger has a wallet-touching event (anything except RUN_START, MODE_CHANGE and LEVERAGE_CHANGE), or whose tape ends in a wallet command the ledger has not caught up with. Runs that never touched the wallet keep the old path.
- **No members → `recover_wallet` returns None.** Every run then uses the unchanged single-run `recover()`, byte for byte. Production today is exactly this case: the ETH and SOL ledgers hold only RUN_START.
- **Ordering:** each record's own `ts_ms`, which is the quote time already on the tape for markets and commands. Ties go to member rank (owner first, then symbol), then each run's own sequence. A run's records are never reordered. No new clock was introduced.
- **Common cut instead of a full replay:**
  - Why: production BTC cannot be replayed from its first record (CASE B: its ledger began on schema v2, the build writes v3).
  - How: each member starts from a checkpoint at or before a cut X, with no wallet event between that checkpoint and X (±5s margin for exchange clock skew). So at X every member's wallet share is its checkpoint's. Everything after is merged and replayed with one `SharedCash` attached.
  - A member with no usable checkpoint replays from its first record, which is allowed only if it had no wallet event before X.
  - The newest torn or invalid checkpoint falls back to an older one, using the same checks as `recover_from_checkpoint`.
- **Integrity:**
  - The per-ledger byte-prefix check is unchanged. Every member is checked before anything is written, so a refusal leaves every file untouched.
  - Missing ledger tails (kill -9 between the tape append and the ledger append) are rebuilt from the tape, which is the authority.
- **Two supporting changes:**
  - `SharedCash` sums run owner first, then by symbol. Join order differs between the live process and a restart, and 28-digit Decimal rounding is order sensitive. A test uses values that round differently. With one member, or members at zero, the figures are unchanged.
  - Retiring a traded HOT run takes a checkpoint, using the existing rotation, so an old HOT run never forces the cut back before its first trade.

Residual: if two symbols' quote clocks stamp a later fill earlier than an earlier one, the merge cannot know. This needs both within the ticker staleness window, roughly 1s for the Binance markPrice stream. The rebuild then differs and recovery refuses (fail closed, no guess). A test pins this.

## 4. Scenarios (`test_paper_wallet_recovery.py`, 20 tests; restart state compared to the live state before stop)

| | Result |
|---|---|
| A BTC only | old path, identical bytes, ledgers untouched |
| B HOT only | identical |
| C BTC LONG + HOT LONG | identical; HOT alone refused (control) |
| D BTC LONG + HOT SHORT | identical; HOT alone refused (control) |
| E BTC → HOT → BTC | identical; HOT alone and BTC alone both refused (control) |
| F HOT → BTC → HOT close | identical |
| funding on both symbols across an 8h boundary | identical |
| two HOT runs, one retired | identical |
| G runtime restart with HOT position | both positions back, lock `HOT_POSITION_OPEN` |
| H HOT closed, retired, restart | scan allowed (`HOT_SYMBOL_FLAT`), wallet equal, BTC kept |

Each case compares wallet, available, used margin, realised PnL, fees, funding, fills, positions and ledger bytes before and after the restart. All equal.

Determinism:
- Restarting twice gives the same result as once.
- The same timestamp puts the owner first.
- Sums do not depend on join order.

Crash recovery:
- kill -9 with a torn tape line and a lagging ledger: the tail is rebuilt and the bytes match the live file.
- Checkpoint start: BTC starts from a checkpoint, not its first record.
- Newest checkpoint torn: the older one is used, with parity.
- Owner with no usable checkpoint and an old-schema ledger: refused (`LEDGER_SCHEMA_MISMATCH`).
- Tampered ledger: refused, and nothing written.

Mutation checks:
- Replaying members one after another instead of merged: 5 tests fail.
- Insertion-order sums: the rounding test fails.
- No checkpoint on retire: the retire test fails.

## 5. Real data

- **QA run on real Bybit/Binance quote clocks** (BTC 0.002 + RLC 20.4): the previous build refused it. It now recovers both, ledger hashes are unchanged, and the wallet is 743.9453.
- **QA run 101:** still refused, correctly.
  - RLC rebuilds identically.
  - BTC's last line was written by the old build after a failed restart had left RLC out of the wallet. It recorded available 719.0301 instead of 719.0060; the gap of 0.0241 is RLC's loss plus fees.
  - The history itself is wrong, so refusing is right. Production never reached this state.
- **Local `paper-d4-v3-001`:** the old and new builds refuse identically (CASE B full-replay divergence, a pre-existing defect not fixed here). The data files are byte-identical after both.

## 6. PAPER E2E with process restarts (real terminal, 1440px)

| Step | Result |
|---|---|
| BTC LONG 0.002 | done |
| scan selects HOT | USUSDT |
| HOT LONG 976 US | done |
| restart backend | both positions, entry, realised, fees, wallet 743.94541, available 725.36155 identical |
| HOT lock after restart | `HOT_POSITION_OPEN`; scan POST 409; screen shows button disabled and LONG (`e2e_after_restart_hot.png`) |
| HOT CLOSE | lock `HOT_SYMBOL_FLAT` |
| restart again | identical state, scan allowed |

Recovery source on this data: BTC and US both `WALLET_REPLAY`. LIVE orders: 0.

## 7. CASE B vs this defect

- **CASE B** is the full-replay fallback that was already broken in production: BTC schema v2→v3, and SOL divergence. It is not fixed here.
- The new path never needs BTC's full replay while a usable checkpoint exists before the cut. If none exists, the replay refuses, as before.
- The multi-symbol defect fixed here is separate. It happened even when every run's full replay was valid.

## 8. Safe MAX regression

No LIVE code changed in this work. `test_safe_max_brackets.py` passes, covering BTC/CFX/BAT unchanged and GTC 20x configured / 10x allowed → 10x effective. The production figures from the 10/10 read-only run stand.

## 9. Tests

- Backend crypto: 2,178 passed, 34 skipped, 0 failed. This run had the gitignored ETH/SOL reference files present, so the 4 multisymbol tests that usually fail in this clone passed (43/43). The files were removed afterwards.
- Frontend: 1,076/1,076. tsc passes.

## 10. Production notes (not done)

- First boot after deploy takes the old path (no wallet members yet).
- The first PAPER HOT trade makes BTC and that HOT run members. Restarts then replay from a cut. The cut is bounded by the members' checkpoints: every 20k records, and at HOT retirement.
- Startup work grows by at most one segment of BTC tape per member window.
