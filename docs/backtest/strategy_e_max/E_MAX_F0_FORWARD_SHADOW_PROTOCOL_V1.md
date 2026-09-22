# Strategy E-MAX - F0 Forward / Shadow Evidence Protocol V1

| | |
|---|---|
| Verdict | **E-MAX-F0 DATA COLLECTION REQUIRED** |
| Machine authority | `strategy_e_max_forward_rules_v1.json`, canonical `dd8ddf42…cfbaa` |
| Candidate | `STRATEGY_E_MAX_V1` (rules `b30a3e95…`, M6 result `2f052e91…`), immutable |
| Comparator | `STRATEGY_E_TRADING_V1_1` (E-BASE, rules `b90573bd…`) |
| Code | `app.strategy_e_max_forward.{rules,shadow,readiness}`, CLI `app.dev.run_strategy_e_max_f0` |
| Tests | `backend/tests/strategy_e_max/test_e_max_f0_forward.py` |
| Status | DEVELOPMENT FROZEN · FORWARD SHADOW OBSERVATION · PAPER NOT APPROVED · LIVE NOT APPROVED |

F0 records nothing and judges nothing. It freezes the contract under which E-MAX V1 will be
recorded, unchanged, on data created after it was frozen. Forward data is evidence only; it is never
used to choose or tune anything.

## 1. Preflight and provenance closure

- Branch `main`, HEAD `95ab73d`. Two unrelated commits (Strategy A `95ab73d`, B-E0 `77ead1c`) sit on
  top of M6 `0a439a3`. No E-MAX file changed after M6.
- The staged area was empty at start. The unrelated dirty worktree was left untouched.
- `v1.load_rules()` passes, which re-verifies the whole M0-M6 chain, the committed results, the
  winners and the E-Base V1.1 identity.
- **M6 provenance closure: PASS.** Each value is checked against the committed M6 result:

| Check | Result |
|---|---|
| V1 rules digest | `b30a3e95…` |
| M6 result file | `2f052e91…` |
| M6 verdict | `E-MAX-M6 PASS — E-MAX V1 DEVELOPMENT CANDIDATE FROZEN` |
| M6 code identity recomputed now | `e4c4a6e3…` = committed |
| Development tape | `d12ff28a…` |
| E-BASE V1.1 | `b90573bd…` |

- **Code placement.** The forward code lives in the new packages `app.strategy_e_max_forward` and
  `app.dev.run_strategy_e_max_f0`. M6's code identity hashes every module in `app.strategy_e_max`
  and `app.backtest.strategy_e_max`, so placing a file there would make M6 unreproducible. A test
  pins this.

## 2. Forward boundary

`FORWARD_HOLDOUT_START = 2026-09-17`, reused from the E1 forward layout (`require_forward_session`).
The development data ends at 2026-09-16 and is frozen.

- Every forward write path refuses an earlier date: the seal, the registry, the session rows and
  the readiness assessment.
- The development replay reads only its 2,152-file manifest through a symlink view, so forward
  files cannot enter it.
- Forward data goes to `market_data/forward/massive/…` on Drive and to
  `data/runtime/strategy_e_max/forward/…` in the repo. Both are append-only.

## 3. Existing forward infrastructure (reused, not modified)

| Piece | Module | Use |
|---|---|---|
| Boundary and layout | `strategy_e1_forward.layout` | forward gate and tree |
| Checkpoints | `strategy_e1_forward.checkpoint` | N = 250 / 500 / 1,000 / 2,000 |
| Context gate | `strategy_e_v1_1.context.require_complete` | FEATURE_CONTEXT_INCOMPLETE / FutureContextViolation |
| 09:25 universe and seal | `strategy_e_v1_1.universe`, `strategy_e_v1_1.decision.seal` | V1.1 PIT universe, H5, E-BASE max 3 |
| Execution | `strategy_e_max.capacity.execute` | E-D2 / E-D3 / E-D4 / E-D5 unmodified |
| E-MAX composition | `strategy_e_max.v1.compose` | B2, then 2.0x |

The E1 forward `seal.py` and `run_strategy_e1_forward._features_for` are **not** used. E-F0
found that their feature builder has no daily join, so H5 is always empty. The forward feature
builder that fills a V1.1 `DecisionFrame` from forward files is still unbuilt; it is the next
implementation step once data exists (section 8).

## 4. Required feature context

This is the E-R1 contract (`78bb90b4…`), used verbatim:

- daily <= D-1: grouped daily for close(D-1), the 21-session window, previous-day dollar volume
  and SPY close;
- CS reference <= D-1;
- a split list covering executions through D;
- minute bars starting <= 09:24 ET on D, and prior pages covering the RVOL history.

A LIVE seal must be written before 09:30:00 ET.

## 5. Current data inventory (probed 2026-09-22, read-only)

| Input | Store | Available through | Forward status |
|---|---|---|---|
| Grouped daily | `raw/massive/grouped_daily` | **2026-09-16** | D-1 context only for D = 2026-09-17 |
| Forward tree `market_data/forward` | Drive | **does not exist** | nothing collected after 2026-09-16 |
| Splits | `splits_2024-09-16_2026-09-16` | executions through **2026-09-16** | no `splits_asof_<D>` for any forward D |
| CS reference | `reference_tickers/CS_2026-07-01` | 2026-07-01 | valid latest <= D-1 |
| Minute (Common Raw) | 3,884 symbol dirs | last page end **2026-09-16** | no page for any forward session, SPY included |
| RVOL history | Common Raw pages | through 2026-09-16 | **2,549 of 2,550** D-1 eligible symbols; 2,544 with >= 60 contiguous sessions |

**Re-verification of the old figures.**

- The E-R1 note "1,612 of 2,936 symbols covered" is superseded. The USB-HIST-V2 recent-window
  collection filled the history.
- For D = 2026-09-17 the D-1 daily-eligible universe is **2,550**. This is an upper bound, because
  the (09-16, 09-17] split flag needs a split list through 09-17.
- Forward grouped daily and per-session `splits_asof` are still entirely missing, as E-R1 reported.

**New structural finding: `CON`.** CON is D-1 eligible and has no minute directory. `CON` is a DOS
device name: `mkdir CON` fails on the Windows-hosted store, so `raw_fetch` records and skips it.
Under the whole-universe rule (E1 forward section 13), that would make every forward session
permanently NOT_READY. The contract therefore stores reserved names under `_<SYMBOL>`
(`readiness.forward_minute_dir`). This changes the storage path only; no rule changes. CON also
never appeared in the development tape.

## 6. Session readiness matrix (`readiness_2026-09-22.json`)

| Session | State | Reasons |
|---|---|---|
| 2026-09-17 | NOT_READY | MISSING_SPLITS_ASOF, MISSING_MINUTE (0 / 2,550), MISSING_RVOL_HISTORY (CON), MISSING_SPY_CONTEXT (SPY page) |
| 2026-09-18 | NOT_READY | MISSING_DAILY_CONTEXT, MISSING_SPLITS_ASOF, MISSING_MINUTE, MISSING_RVOL_HISTORY, MISSING_SPY_CONTEXT |
| 2026-09-21 | NOT_READY | as 2026-09-18 |
| 2026-09-22 | NOT_READY | SESSION_NOT_CLOSED (Basic serves T-1) plus the above |

**0 READY, 4 NOT_READY.** Every NOT_READY session is FEATURE_CONTEXT_INCOMPLETE: it gets no seal
and no H5 = False, and it stays out of the forward timeline. The history check works at page-range
level; the exact per-session RVOL range check belongs to the forward builder.

## 7. LIVE versus RECONSTRUCTED

| Mode | Evidence | Status 2026-09-22 |
|---|---|---|
| LIVE | PRIMARY | **NOT AVAILABLE** |
| RECONSTRUCTED | SECONDARY | available once the data exists |

**Why LIVE is not available.** Massive Stocks Basic serves no same-day session (a same-day request
returns 403 `PLAN_TIMEFRAME_NOT_INCLUDED`, and the end date is always T-1). No 09:24 premarket
bar exists at 09:25 from the bound source.

**What a LIVE path would need.** A real-time premarket minute source. Kiwoom is real-time, but its
volumes differ from Massive's (regular session about 0.70x), and RVOL and dollar volume are H5
inputs. Using it would need its own source-equivalence contract before any LIVE seal.

**Consequence.** Until then, all E-MAX forward evidence is **RECONSTRUCTED / SECONDARY**. Every
seal, registry row and session row carries `forward_mode` and `evidence_class`, and the two
classes are never pooled without the split shown.

## 8. Collection requirements (nothing started in F0)

| Input | Collector | Status | Cost |
|---|---|---|---|
| Grouped daily | `strategy_c_selection.raw_fetch.fetch_grouped` | extend: forward tree path | 1 call / session |
| Splits as of D | `strategy_c_selection.raw_fetch.fetch_splits` | extend: `splits_asof_<D>`, executions through D | 1 call / batch |
| CS reference | `historical_store.reference_fetch` | reusable (not window-limited) | 6-12 pages / snapshot |
| Minute | `historical_store.raw_fetch` (ledger resume, cooldown retry, 403 → NOT_AVAILABLE, exact bytes) | extend: forward root, `_<SYMBOL>` dirs | 1 call / symbol / batch, about 2,551 incl. SPY |
| RVOL history | none | only CON (under `_CON`) | 1-2 calls |
| Execution quotes | none | needs a new collector and a LIVE path (Kiwoom FT) | - |

- **Batching.** One minute request covers up to 50 sessions, so RECONSTRUCTED collection in weekly
  batches costs about 2,551 calls (about 8.5 hours at 5 calls/min) per week rather than per
  session. That is the binding constraint, and it competes with any historical collector for the
  single writer lock.
- **Minimum for any new collector:** source, date range, symbols, resume, retry, dedup, checksum,
  manifest, PIT storage.

## 9. Shadow path (implemented, synthetic-tested)

**Phase 1 (09:25 seal, `shadow.shadow_decision`).** From the V1.1 seal and its frame, one record
fixes:

- universe identity and all universe rows (11 sealed columns and the H5 flag);
- the H5 candidates;
- E-BASE's canonical max-3 selection;
- E-MAX's R1 order and max-3 selection;
- the B2 state: universe rows, H5 count, h5_rate, high breadth, 1.5 or 1.0;
- the global 2.0x and the final exposure (2 or 3);
- the V1.1 / V1 / forward / context digests and the V1.1 seal digest.

It is hashed into `decision_digest`. The record is written once, a second write is refused, and a
read re-derives the digest. LIVE mode refuses a timestamp at or after 09:30 ET.

**Phase 2 (`shadow.shadow_outcome`).** It accepts only a verified record together with its own
V1.1 seal.

- Both strategies run through `capacity.execute` with exact 09:30 open and exact 09:34 close,
  ENTRY_INVALID / UNRESOLVED_EXIT, no fallback and no backfill.
- E-MAX goes through `v1.compose` and is refused if its exposure differs from the sealed breadth
  state.
- E-BASE is 1.0x.

## 10. Cost tracking

- **Scenarios.** 0 / 5 / 10 / 15 / 20 bp are always recorded, with these roles: gross edge / low
  friction / **primary** / stress / severe stress.
- **Scaling.** Levered net = exposure x (gross - cost).
- **The cost model is frozen.** Seeing forward results never moves it to 5 bp. A change needs a
  new version.

## 11. Execution-friction availability

| Field | Status |
|---|---|
| 09:30 / 09:34 bar volume | **recorded** |
| bid, ask, spread, nearby prints at 09:30 / 09:34 | **NOT AVAILABLE** (Massive Basic has no quotes; Kiwoom FT quotes are live-only and need the LIVE path) |

Registry rows carry `bid_*` and `ask_*` as null with `friction_status = QUOTES_NOT_AVAILABLE`.
An OHLC high-low is never recorded as a spread. `observed_execution_friction_bp` is reserved for
when quote or fill evidence exists, and it never replaces COST_10BP.

## 12. Registry schema

- **Position rows:** one per H5 candidate per strategy, with the rules'
  `registry.position_fields`. That is strategy and version, mode and evidence class, session,
  decision timestamp and digest, universe / H5 / breadth fields, symbol, rank and selected, entry
  and exit timestamp / price / valid / reason, base / breadth / global / final weight, gross and
  the four net scenarios, bar volumes, the null quote fields, upstream digests and the forward
  rules digest.
- **Session rows:** one per strategy, with exposure, activity, trades and five scenario returns.
- **Append-only:** a second (strategy, mode, session) is refused.
- **Timeline:** a READY session with no trade is a 0-return session (the E-D6 convention). A
  NOT_READY session is absent, not 0.

## 13. Checkpoints

- **N.** Re-verified in code: `strategy_e1_forward.checkpoint.CHECKPOINTS = (250, 500, 1000, 2000)`,
  with N = H5 observation rows (`registry.h5_count`). For E-MAX, N is the H5 candidate count over
  recorded forward sessions. It is shared by both strategies because they share the seal, and it is
  reported with the LIVE / RECONSTRUCTED split.
- **N = 250** is a sanity check: sign, paired delta, R1 and breadth behaviour, tail, data quality,
  friction availability.
- **N = 500** is the first decision checkpoint: is continued forward verification worth it? A
  one-shot 5-10 year untouched historical validation may be reviewed here.
- **N = 1,000 and 2,000** keep their E1 roles: diagnostic, and the only checkpoint that may promote.
- **Promotion gate: NOT DEFINED.** An E-MAX forward gate (own metrics and paired delta) must be
  frozen as a separate contract before the N = 250 evaluation is first computed. Development
  thresholds are not copied.
- **Evaluation schema** (at every cost):
  - E-MAX: observations, trades, active sessions, coverage, mean trade and session, median, win
    rate, PF, cumulative, MDD, Sharpe, Sortino, Calmar, positive months and blocks, unique symbols,
    top 1 / 3 / 5 session share, top 1 / 5 symbols, HHI;
  - paired E-MAX - E-BASE: mean, median, positive rate, bootstrap CI, P(delta <= 0);
  - breadth: high-breadth counts, sign and contribution against normal, with the 3.0x effect
    separated.

## 14. What stays forbidden

In response to forward results, none of these may change:

- H5, R1, max 3, the B2 threshold, the 09:34 exit;
- 2.0x / 3.0x (no 2.1x, 2.5x or 3.5x);
- symbol exclusion (ABSI, HIMS or any other);
- the cost model;
- any development replay optimisation.

A change is **E-MAX V2**, a separate strategy, and never relabels V1 forward evidence. No long
historical data is purchased in F0.

## 15. Gate

```text
E-MAX-F0 DATA COLLECTION REQUIRED
```

The shadow path reproduces the frozen rule without change. It needs no input after 09:25, keeps
development and forward apart as well as E-BASE and E-MAX, and builds an immutable seal. So nothing
here is a BLOCK. What is missing is data: no closed forward session can be sealed yet. In addition,
LIVE / PRIMARY evidence is unavailable on the current data plan, so forward evidence will be
SECONDARY until a real-time source contract exists.
