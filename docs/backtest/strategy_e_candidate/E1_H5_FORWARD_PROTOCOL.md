# Strategy E1-H5 Forward Holdout Protocol

| | |
|---|---|
| Status | infrastructure built, **no forward data collected yet** |
| Forward holdout start | **2026-09-17** (the day after the last historical session) |
| Hypothesis | E1's H5, frozen; this protocol changes nothing about it |
| Primary horizon | official open -> 5 minutes (`R_5M`), fixed |
| Historical confirmation | `e1h5-d57b212da84c`, rules `d2a8b5f2…`, **FROZEN** on 2026-09-20 |
| Verdict carried forward | `INCONCLUSIVE - PROMISING` |
| Promotion status | **STRATEGY E = NOT PROMOTED** |
| Code | `backend/app/backtest/strategy_e1_forward/`, CLI `app.dev.run_strategy_e1_forward` |
| Artifacts | `data/runtime/strategy_e_candidate/forward/{registry,decision_seals,labels,checkpoints}/` |

This document is the operating contract for accumulating genuinely out-of-sample evidence on H5.
It proposes no hypothesis, adds no feature, and tunes nothing.

---

## 1. Why a forward holdout is the only remaining option

The confirmation study established two things. H5's edge survives price, liquidity and gap-size
matched controls on the development sample (+17.01 bp matched against a +16.95 bp raw gap,
positive in all six price buckets and all four liquidity buckets). And no holdout could be cut
from existing data, because the minute collector advances **alphabetically over a fixed window**,
so new arrivals are new symbols over sessions already seen, and because the daily snapshot
`USB-HIST-V1` is frozen at 2026-09-16, so no later session can enter the point-in-time universe
at all.

Both blocks that exist have been looked at. Neither can become a holdout by being renamed. The
only uncontaminated evidence left is calendar time that has not happened yet, or has happened but
has never been read.

---

## 2. The boundary, enforced in code

```text
FORWARD_HOLDOUT_START = 2026-09-17
```

`layout.require_forward_session` refuses any earlier date, and every write path goes through it:
the seal builder, the label builder and the registry. A test asserts the refusal for 2026-09-16,
2026-09-15 and a 2024 date. Hiding a slice of old data and calling it a holdout is not a policy
here, it is a `ForwardViolation`.

---

## 3. Two provenances, not one

Sessions 2026-09-17 and 2026-09-18 have already closed. They are unread - no result from them
exists anywhere in this project - so they are legitimate forward evidence, but they cannot carry
the strongest guarantee. The protocol records which guarantee each session has:

| provenance | meaning | guarantee |
|---|---|---|
| `LIVE` | the seal was written at 09:25 ET while the session's 09:30 bars did not yet exist | look-ahead is impossible: the data was not in the world |
| `RECONSTRUCTED` | the seal was built from a tape fetched after the close | look-ahead is prevented by the code's cutoff and the PIT audit only |

2026-09-17 and 2026-09-18 can only ever be `RECONSTRUCTED`. Every session from the first live
run onward should be `LIVE`. The registry carries the label on every row so a later analysis can
report them separately, and the promotion checkpoint should be read with the split in view.

---

## 4. Daily forward data contract

For a decision at 09:25 ET on session D, only these are admissible:

```text
reference   <= D          (the CS snapshot dated on or before D)
daily       <= D-1        (grouped daily, splits, SPY close)
premarket   <= 09:24 D    (minute bars whose start is at or before 09:24 ET)
```

The frozen snapshots are never modified. Forward market data is written to a separate append-only
tree so that a reader can always tell which tape a row came from:

```text
market_data/forward/massive/
  grouped_daily/<year>/<session>.json.gz
  reference/CS_<as_of>.json.gz
  splits/splits_asof_<session>.json.gz
  minute/<SYMBOL>/<SYMBOL>_<session>.json.gz
```

Reference snapshots reuse the `historical_v2` D-1 reference infrastructure already built for B
(`reference_fetch.py`, dated `as_of` CS snapshots carrying ticker, type, active, exchange, CIK,
composite FIGI and `delisted_utc`). **No E-specific reference collector is written.**

---

## 5. The 09:25 decision seal

Each forward session runs in two phases that cannot be reordered.

**Phase 1, at 09:25 ET.** Build the eligible universe, every H5 feature, and the H5 mask from bars
starting at or before 09:24 and daily inputs from D-1. Write them and hash them:

```text
decision_digest = sha256 over (symbol, 11 sealed columns, h5 flag) for every eligible row
```

The mask is produced by importing `strategy_e1_premarket.evaluate.mask("H5", …)` - E1's own
function, unmodified. The seal carries the eleven columns that function reads, not the four H5
strictly needs, precisely so that the import can stay unmodified. A seal is written once; a second
write is refused, and reading a seal re-derives its digest and refuses a tampered file.

**Phase 2, after 09:35 ET.** Attach `R_1M`, `R_5M`, `R_5M_strict`, `R_15M`, MFE and MAE. The label
builder refuses to run without a seal, refuses a session mismatch, and refuses a universe that is
not exactly the sealed one. The registry then refuses labels whose `decision_digest` does not
match the seal they claim to label.

Verified end to end on synthetic rows by `run_strategy_e1_forward rehearse`, which shows the seal
written, a reseal refused, labels attached, the registry appended, a re-append refused, and a
pre-boundary session refused.

---

## 6. Observation registry

Append-only JSONL at `forward/registry/observations.jsonl`, one line per (symbol, session), with
every field the declaration requires:

```text
symbol, session_date,
daily_source_digest, reference_source_digest, minute_source_digest,
rules_digest, feature_digest,
decision_time = "09:25 ET", provenance,
h5, R_5M, available_at, created_at
```

A session already present is refused rather than replaced.

---

## 7. Point-in-time audits

Both are poisoning tests run against the real seal builder, so a look-ahead introduced later
fails them rather than passing silently.

| audit | method | pass condition |
|---|---|---|
| feature cutoff | every bar from 09:25 ET onward replaced with positive, finite, missing-preserving noise; the seal rebuilt | `decision_digest` identical |
| daily / reference cutoff | daily panel and CS membership after D-1 poisoned; the seal rebuilt | `decision_digest` identical |

The digest covers the features **and** the H5 mask, so one comparison tests both. The test suite
includes a deliberately planted leak that the audit detects, and a case where the eligible
universe itself moves, which is also a failure. **An audit failure makes that session's forward
observation invalid**, not merely suspect; it must be excluded, not repaired.

---

## 8. Checkpoint policy

Declared before any forward observation exists, to remove optional stopping:

```text
checkpoints: N = 250, 500, 1000, 2000   (H5 observations)
only N = 2000 may promote
```

Daily returns may be recorded at any time. A PASS/FAIL verdict may be re-stated **only** at a
declared checkpoint, each checkpoint is recorded once, and 250/500/1000 are diagnostic. Enforced
by `checkpoint.py` and its tests.

---

## 9. Statistical promotion gate

Unchanged from the confirmation study; a forward gate is not a relaxed gate.

| criterion | threshold |
|---|---|
| sample | N >= 2,000 H5 observations |
| matched mean lift | >= +15 bp |
| matched median lift | >= +5 bp |
| matched win-rate lift | >= +2%p |
| top 1% removed | > +5 bp |
| top-5 symbol contribution | <= 40% |
| session-cluster bootstrap 95% CI low | > 0 |
| same-session estimator | positive |
| price and liquidity neutralization | positive |
| downside ratio | <= 1.5 |

---

## 10. Execution cost gate, separate and binding

A statistical PASS is not sufficient. The confirmation study measured:

```text
gross break-even round-trip cost = 17.01 bp
```

net matched lift by assumed cost: +17.0 at 0 bp, +7.0 at 10 bp, +2.0 at 15 bp, **-3.0 at 20 bp**.

`EXECUTION_GATE` requires an estimated realistic round-trip cost **comfortably** below the forward
gross alpha. A structure that leaves 2 bp after costs is not approved for live trading. Stocks
Basic still carries no bid/ask - re-verified against the client and the store contract, which
records quotes and the halt feed as `UNAVAILABLE` on this plan - so the cost proxy must come from
elsewhere. Whatever proxy is chosen, **it is declared before the forward results are read**, and
the candidate proxies are named now:

* opening auction print versus the first-minute range;
* first-minute high-low range as a spread upper bound, by price and liquidity bucket;
* realised fills, if a broker ever provides them.

Choosing the proxy after seeing which one makes H5 look best is the failure mode this clause
exists to prevent.

---

## 11. What is still forbidden

Opening execution research stays closed until forward alpha is confirmed. No comparison of 09:30
versus 09:31 entry, no limit versus market, no MOO/MOC, no 3- or 4-minute exit, no trailing stop.
The primary horizon is fixed at five minutes and that is the only execution fact settled.

No Strategy E directory, broker, position sizing, paper trading or LLM work.

---

## 12. Decay monitor

The matched lift by month over the broad-universe period was +45.78, +24.35, +32.74, +25.67,
+1.16, +9.01 bp. The forward run records the same monthly series. Its purpose is to separate a
temporary regime from alpha decay. **H5 is not modified in response to a monthly number**, and a
bad month is not a reason to re-state a verdict outside a checkpoint.

---

## 13. Universe requirement

The confirmation block was 93 symbols that all began with P, because the collector walks the
alphabet. A forward holdout may not inherit that. **Each forward session must cover that session's
whole eligible universe**, not an alphabetical prefix. If a session's tape covers only part of the
universe, that session is INVALID as forward evidence and must be recorded as such rather than
analysed.

---

## 14. Daily operating procedure

```bash
# once, before the first live session
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward estimate
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward rehearse

# each forward session, after the tape for that session exists
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward \
    audit --session <YYYY-MM-DD>
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward \
    seal  --session <YYYY-MM-DD> --provenance LIVE
# after 09:35 ET
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward \
    label --session <YYYY-MM-DD>

# any time
PYTHONPATH=backend .venv/bin/python -m app.dev.run_strategy_e1_forward status
```

The tool never fetches, never takes the writer lock, and refuses clearly when a session's tape is
absent, so it cannot disturb the historical collector that is currently running.

---

# Status Report (2026-09-20)

## A. Historical H5 status

| study | verdict | lineage |
|---|---|---|
| E0 overnight / closing strength | FAIL | CLOSED |
| E1 premarket -> open momentum | INCONCLUSIVE | H5 the only surviving candidate |
| E1-H5 confirmation `e1h5-d57b212da84c` | INCONCLUSIVE | **FROZEN** 2026-09-20 |

Development evidence, matched: +17.01 bp mean, +9.47 bp median, +13.53 bp under the stricter
same-session estimator, +8.76 bp after removing the top 1%, downside ratio 0.88, session-cluster
CI [+6.51, +27.75] bp. Positive in all six price buckets, all four liquidity buckets and all four
regimes. Independent confirmation N = 72, which decides nothing.

## B. Forward holdout start

`2026-09-17`, enforced in code. Sessions 2026-09-17 and 2026-09-18 have closed and remain unread;
they are eligible as `RECONSTRUCTED` evidence. 2026-09-19 and 2026-09-20 are not trading days.
The next live-sealable session is **2026-09-21**.

## C. Forward data requirements

Per session: grouped daily (1 call), CS reference as of D (about 6 paginated calls), splits
(1 call), and minute bars 04:00-09:35 ET for every eligible symbol (1 call each).

## D. Existing common data reuse

B's Common Raw minute pages cover 04:00-20:00 ET, which strictly contains everything H5 reads.
**Wherever B collects a forward session, E reads the same files read-only and fetches nothing.**
No E-specific minute collector, no E-specific reference collector: the `historical_v2` D-1
reference infrastructure is reused as it stands. The forward tree exists only for sessions B does
not cover.

## E. Additional data needed

Nothing exists after 2026-09-16. Every forward session must be collected once, by whichever
process gets there first. Two further prerequisites are not data-collection problems:

1. a **frozen daily snapshot extending past 2026-09-16** - without it the point-in-time universe
   cannot admit a later session at all;
2. **whole-universe coverage per session**. An alphabetical prefix makes a session INVALID as
   forward evidence (section 13).

## M. Expected sample accumulation

At the measured H5 rate of 2.53% of premarket-eligible rows:

| scenario | symbols/session | H5 rows/session | sessions to 250 | to 500 | to 1,000 | to 2,000 |
|---|---|---|---|---|---|---|
| full eligible universe | 2,557 | **23.2** | 10.8 (0.5 mo) | 21.6 (1.0) | 43.2 (2.1) | **86.4 (4.1 mo)** |
| current covered symbols | 1,612 | 14.6 | 17.1 (0.8 mo) | 34.3 (1.6) | 68.5 (3.3) | 137.0 (6.5 mo) |

## N. Storage and API cost

| scenario | calls/session | hours at 5 calls/min | hours at 13 s spacing | MB/session | MB/month |
|---|---|---|---|---|---|
| full eligible universe | 2,565 | **8.55** | 9.26 | 2.12 | 44.6 |
| current covered symbols | 1,620 | 5.40 | 5.85 | 1.34 | 28.1 |

Storage is irrelevant (under 50 MB a month against 7.4 GB free). **The provider rate limit is the
binding constraint**: at Stocks Basic's 5 calls per minute a whole-universe forward session needs
roughly 8.5 hours of continuous fetching, which collides directly with the historical backfill
currently running. Sequencing the two is an operational decision, not a research one.

## O. Regression

E0 32, E1 28, E1-H5 confirmation 25, forward 30 = 115 E-family tests. Full suite result
accompanies this document. The NaN/subset defect found during the confirmation study is covered by
four dedicated tests (NaN-safe session mean, full-universe demeaning under a subset, finite bucket
output, zero-control bucket handling).

## P. Current verdict

```text
INCONCLUSIVE - PROMISING
```

## Q. Promotion status

```text
STRATEGY E = NOT PROMOTED
```

## R. Immediate next action

Infrastructure and accumulation only. No new hypothesis.

1. Freeze a daily snapshot covering sessions after 2026-09-16 (`USB-HIST-V3` or an extension of
   V2). Nothing else can proceed without it.
2. Decide the fetch sequencing between the historical backfill and the forward tape, given that
   one whole-universe forward session costs about 8.5 hours at the plan's rate limit.
3. Collect 2026-09-17 and 2026-09-18 while they remain inside the provider's rolling window, and
   seal them as `RECONSTRUCTED`.
4. Run `audit --session` on every collected session and discard any session that fails.
5. Begin `LIVE` sealing from the first session the pipeline is ready for.
6. Declare the execution-cost proxy in writing before any forward result is read.
7. Re-state a verdict only at N = 250, 500, 1,000 and 2,000, and promote only at 2,000 and only
   with the execution gate also passed.
