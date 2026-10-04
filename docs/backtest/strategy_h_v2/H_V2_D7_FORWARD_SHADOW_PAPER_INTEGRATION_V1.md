# H-V2-D7 Forward Shadow + Paper Integration V1

Contract: `h_v2_d7_forward_shadow_paper_integration_v1`
Frozen rules: `docs/operations/h_forward_shadow_v1.json` (sha256 `33fe002f…c915b1a`)
Written 2026-10-04 (KST). Model calls 0. Research cost $0.

---

## A. Purpose

Strategy H's research is finished. D1 through D6 produced, on the 2026-09-16 decision session,
eight decision-eligible issuers with no APPROVE, six WATCH and two REJECT, and D5-D2R repaired the
one window-rule defect D6 had surfaced. This step does not research anything further.

It does two things:

1. launches Strategy H as a **forward shadow** that reads only data dated after the launch;
2. puts H on the **same operating screens, API and performance board** Strategy A and Strategy E
   already use, additively, so the three strategies can be read side by side from now on.

Research build is CLOSED. Forward observation is ACTIVE. The step is deliberately not a chance to
retune anything: the frozen contract records what would count as evidence before any of it exists.

---

## B. The existing A/E paper architecture, as the code actually stands

Audited by reading the repository, not by recollection.

| Component | Strategy A | Strategy E | Reusable for H? | H-specific extension |
|---|---|---|---|---|
| runner | `app.services.simulation_runtime` + `run_morning_scanner`, live `SimBroker` | `usb-e-paper.service` driving `app.strategy_e_max_rt.*` | **No** - both are intraday price-signal runners | offline `app.dev.run_h_v2_d7` |
| paper ledger | `simulation_trades` rows in the paper DB | `run/paper_state_v1/<evidence>/<id>/` session JSON | **No** - both are trade-shaped | append-only `forward_ledger.jsonl` |
| ledger normaliser | `ledger.a_trade_record` | `ledger.e_trade_records` | Yes (shape) | H emits zero trade rows; decisions are a separate schema |
| positions | `broker_projection(SimBroker)` | engine `entries`/`exits` | Yes (shape) | H has none; see §F |
| candidate records | scanner entry board | `decision.selected` | No | H's cohort is the D6 decision set |
| performance calculation | `performance.strategy_metrics(Book)` | same | **Yes, unchanged** | H's `Book` has no trade and no equity series |
| combined column / portfolio | `combined_metrics([a,e])`, `portfolio_baseline(a,e)` | same | **No** - both sum initial equities | H is excluded, by §G |
| paper gate | `paper_gate.evaluate` against a frozen per-strategy contract | same | Yes (it answers `NOT_UNDER_THIS_GATE`) | H has its own D7 evaluation state |
| official clock | `strategies.official` (`AE_OFFICIAL_PAPER_START`) | same | No (it classifies accounting-V1 rows) | H has its own launch snapshot |
| backend API | `app/api/strategies.py`: six questions per registry row, plus cards/ledger/performance/portfolio | same | **Yes** | two extra `/forward` routes |
| registry | `app.strategies.registry.REGISTRY` | same | **Yes** | one row |
| frontend | `lib/strategies.ts` + `ae-operations.tsx`, `/strategy-compare`, `/dashboard` | same | **Yes** | `h-forward.tsx`, `/strategy-h`, a strategy selector |
| scheduler | `end_of_day_runtime`, `position_management_runtime` | systemd timers on the server | Yes (pattern) | §L, documented and not deployed |
| market data | Kiwoom | Kiwoom | No | Massive grouped daily, into H's own store |
| position sizing | `risk_v1`: 0.5% of equity per 1R, needs an initial stop | 1/3 of equity per slot, needs a same-session exit | **No** | §F |

The important finding is the last row, and it is the reason §F says what it says.

---

## C. H integration design

```
app/strategies/h_forward/
  contract.py   the frozen D7 rules, checksum-verified; refuses a non-D5_D2R_V1 launch
  store.py      append-only launch snapshot + forward ledger (JSON Lines)
  prices.py     H's own daily price store and the market-calendar session arithmetic
  outcomes.py   exact horizon maturation; PENDING / INCOMPLETE; never a substituted price
  cohort.py     the read side: cohort rows, counts, maturity, D7 evaluation state
  views.py      H answered in the six shapes the multi-strategy read layer asks of everyone
app/dev/run_h_v2_d7.py    preflight / launch / collect-prices / update / refresh-scan / status / verify
```

Three design choices are worth stating because the alternatives were available and are worse.

**The valuation is recomputed at launch, not copied.** The launch snapshot has to say which contract
produced each number. The runner therefore re-runs the published `value_issuer` and `decide` with
`WindowSelectionContract.D5_D2R_V1` passed explicitly, and then checks the result against the stored
D5-D2R artifact by canonical hash. At launch this reproduced all 13 valuation rows and all 13
decision records byte-identically, and the decision counts matched. A mismatch is a launch blocker,
not a warning. The two published steps keep their own `D5_D2_V1` pin at their call sites, so this
run cannot alter either of them.

**H's price store is its own.** The D3-D6 price panel is read from
`data/runtime/strategy_b_e0/mirror/...`, which belongs to the frozen USB-HIST-V1 dataset whose digest
is an audited fact. Forward sessions go to `data/runtime/strategy_h_v2/d7/prices/<year>/<session>.json`
instead: one small file per session holding the cohort's bars plus SPY, with the fetch recorded. The
frozen mirror is never written to.

**H is additive everywhere.** No A or E branch, book, figure, route or ledger row changed. H is not
in the A+E combined column, not in the portfolio simulation and not under the A/E paper gate; it has
its own column, its own panels and its own evaluation state. The regression evidence is in §M.

---

## D. Initial H cohort

Decision session 2026-09-16. Launch baseline session 2026-10-02 (see §I for why they differ).
Cohort tag `INITIAL_D7_COHORT`. D5 contract `D5_D2R_V1` for all eight.

| Ticker | Decision | D4 gap / conf | Method | Window | Val. conf | Decision close | Launch price | Bear | TP1 | TP2 | TP1 upside | Key binding clause |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| SCCO | WATCH | NEUTRAL / LOW | P/FCF | FULL_2Y | LOW | 189.88 | 205.54 | 133.96 | 190.26 | 260.17 | +0.2% | APPROVE_BLOCKED: expectation_gap |
| DORM | WATCH | UNKNOWN / UNKNOWN | EV/EBIT | FULL_2Y | MEDIUM | 124.96 | 123.06 | 114.10 | 128.85 | 143.41 | +3.1% | APPROVE_BLOCKED: expectation_gap |
| TG | WATCH | NEUTRAL / LOW | EV/FCF | RECENT_6M | LOW | 6.89 | 7.03 | 7.76 | 8.78 | 13.40 | +27.5% | APPROVE_BLOCKED: expectation_gap |
| COLL | WATCH | NEUTRAL / LOW | P/E | FULL_2Y | LOW | 22.19 | 22.02 | 16.63 | 24.66 | 41.50 | +11.1% | APPROVE_BLOCKED: expectation_gap |
| FG | WATCH | UNKNOWN / UNKNOWN | P/B | RECENT_6M | LOW | 23.05 | 21.92 | 23.56 | 27.42 | 29.81 | +19.0% | APPROVE_BLOCKED: expectation_gap |
| VRRM | WATCH | UNKNOWN / UNKNOWN | EV/EBIT | RECENT_6M | MEDIUM | 3.54 | 2.81 | **N/A** | 4.42 | 5.72 | +24.8% | APPROVE_BLOCKED: expectation_gap |
| IDCC | REJECT | NEUTRAL / LOW | EV/EBIT | FULL_2Y | MEDIUM | 328.72 | 335.13 | 168.98 | 214.71 | 274.30 | -34.7% | REJECT: valuation_fully_prices_the_positive_case |
| AEYE | REJECT | NEUTRAL / LOW | P/FCF | RECENT_6M | MEDIUM | 7.21 | 6.92 | 5.67 | 7.07 | 8.80 | -1.9% | REJECT: future_business_story_only_without_corroborating_catalyst |

`APPROVE = 0 · WATCH = 6 · REJECT = 2`. Five further D4-covered issuers (BSY, GOOG, CRK, FRPT,
SPSC) are `NOT_DECISION_ELIGIBLE` and are not in the cohort: an upstream layer abstained on each, so
there is no decision to observe.

**VRRM's Bear is `N/A`, never 0.** D5-D2R refused it: at the RECENT_6M P10 multiple the implied
enterprise value (966,156,377) does not cover net debt (985,096,000), so the per-share value would be
negative and `NEGATIVE_IMPLIED_EQUITY` is published instead of a number. The UI renders `N/A` with
that reason in the cell's title. VRRM therefore enters the shadow as a WATCH with no downside anchor,
and `range_complete` is false for it.

Four facts in this table are worth carrying forward because they are falsifiable:

* **every WATCH is held by the same clause.** All six are blocked by `expectation_gap_permits_approve`
  and nothing else is the first blocker. H's bottleneck at launch is D4, not valuation.
* **IDCC's Bull leg is below its price** (TP2 274.30 against a 335.13 launch price, -16.6% from the
  decision close). That is the cleanest pre-registered forward question: does it cross it?
* **TG and FG launched below their own Bear anchors** (7.03 against 7.76; 21.92 against 23.56). The
  downside leg was already breached before the observation started; no horizon can "discover" it.
* **SCCO's TP1 upside was +0.2%** - a valuation that said the price was already fair.

---

## E. H state model

```
APPROVE  the only state that may become a paper entry candidate
WATCH    an observed candidate, no position, and no return claim
REJECT   not selected, no position, kept in the cohort so the rejection stays falsifiable
```

`NOT_DECISION_ELIGIBLE` is a fourth label but not a fourth state: it means an upstream layer
abstained, and those issuers are outside the cohort.

Allowed transitions, frozen: `WATCH->APPROVE`, `WATCH->REJECT`, `WATCH->WATCH`, `REJECT->WATCH`,
`REJECT->APPROVE`, `APPROVE->WATCH`, `APPROVE->REJECT`.

A transition is only caused by a `D3 -> D4 -> D5 -> D6` re-evaluation on new material evidence.
`price_alone_is_not_a_cause` is a contract field, and `verify` fails any ledger row whose transition
has no evidential cause. A thesis version is immutable: a changed decision appends a row with
`previous_decision` set and a new `thesis_version` (a hash over the D3, D4, D5 and D6 checksums), and
the launch snapshot's `decision` keeps saying what it said at launch.

---

## F. Paper position rules

**No decision state creates a position in D7 v1, including APPROVE.** An APPROVE creates an
`entry candidate`; converting one into a sized position is fail-closed and reports
`SIZING_CONTRACT_REQUIRED`.

That is not caution for its own sake. Strategy A sizes from `risk_v1` - 0.5% of equity per 1R - which
requires an initial stop. Strategy E sizes 1/3 of equity per slot, which requires a same-session
exit. D6's own `d6_never_produced` list states that it produces neither an entry level, nor a stop,
nor an exit. So neither existing paper convention can be applied to H without inventing a parameter
H's research never produced, and §18 of this step's brief forbids researching sizing here.

The execution convention is nevertheless fixed in advance so that it is not chosen later to suit a
result: when a sizing contract is frozen, an H entry fills at **the next regular session's open
through `app.broker.sim.SimBroker` under `app.execution.config` `execution_v0`** - the A/E paper
convention, unchanged. Nothing new was invented for H.

APPROVE is 0 at launch, so this blocks nothing today. It is a launch blocker for a *position*, not
for the launch.

---

## G. A/E/H strategy isolation

Ledger identity is `(strategy_id, ticker, thesis_version, decision_time)`, so the strategy is part of
the key. The same ticker may legitimately sit in several strategies at once - `A = OPEN`, `E = NONE`,
`H = WATCH` is a normal state and the API reports it as three separate rows in three separate books.

Physically: A writes `simulation_trades` in the paper DB, E writes its engine session files, H writes
`data/runtime/strategy_h_v2/d7/*.jsonl`. None of the three can read another's storage, and a test
asserts that H's files contain neither A's nor E's strategy id.

H is kept out of two places on purpose:

* **the A+E combined column.** `performance.combined_daily` sums initial equities; H has none, so
  including it would mean either inventing an equity or silently returning nothing. The board states
  this in words rather than leaving the reader to infer it.
* **the A/E paper gate.** The frozen gate contract does not list H. H's own state is published beside
  the gate panel instead, and the panel says why H is absent.

---

## H. Forward ledger

`data/runtime/strategy_h_v2/d7/forward_ledger.jsonl`, JSON Lines, append-only. Each row carries
`strategy_id`, `ticker`, `thesis_version`, `decision_time`, `record`, `cohort_tag`, `decision`,
`previous_decision`, `transition`, `cause`, `effective_session`, `decision_session`, `current_price`,
`tp1`, `tp2`, `bear`, `bear_refusal`, `valuation_confidence`, `valuation_method`, `valuation_window`,
`key_binding_clause`, `position`, `position_reason`, the four upstream checksums and `d5_contract`.

`data/runtime/strategy_h_v2/d7/launch_snapshot.jsonl`, same discipline, holds the immutable launch
record per issuer: ticker, CIK, `security_id` (the FIGI-based id D1 assigned, read from the evidence
package D3 actually consumed and cross-checked against the CIK the D3 attempt recorded), the four
research legs with their checksums, the D5-D2R artifact hash, the D7 contract hash, the decision, the
binding clause, the launch timestamp, the launch baseline session and the launch price.

`append` raises `AppendOnlyViolation` on a duplicate identity rather than skipping it, so re-running
the launcher cannot write a second launch record for an issuer. A stored price session is likewise
never rewritten with different numbers.

---

## I. Outcome tracking

Horizons are 1D, 5D, 21D and 63D, counted in **regular trading sessions** by
`app.market.MarketCalendar` - the calendar A and E already use. Horizon *n* matures on the *n*th
session after the baseline and on no other.

```
baseline 2026-10-02 ->  1D 2026-10-05 · 5D 2026-10-09 · 21D 2026-11-02 · 63D 2027-01-04
```

Per matured horizon: security return, SPY return, excess return, MFE, MAE, TP1 hit, TP2 hit and Bear
breach. Path metrics read the daily high and low of the sessions inside the window, so a target
touched intraday counts as touched; a target that was merely approached at the close does not.

States: `MATURED`, `PENDING` (the maturity session is not stored yet), `INCOMPLETE` (a session
*inside* the window is missing from the store, which is named), `NO_BASELINE`. Substituting the last
available price for a missing session is not implemented at all, so it cannot be reached by accident.

**Why the baseline is 2026-10-02 and not the decision session.** Three weeks separate the thesis from
this launch. Measuring from 2026-09-16 would have backfilled twelve already-known sessions into a
"forward" result. The baseline is therefore the last settled session at launch; every horizon lies in
the future; and the 2026-09-16 -> 2026-10-02 interval is reported as `pre_launch_drift` with
`is_forward_evidence: false`. It is shown because a reader comparing a frozen TP1 upside against
today's price deserves to know the thesis price is three weeks old - not because it is a result.

The thesis's own upside figures stay anchored where D5 computed them; the live TP distances are
recomputed from the current price each session. The two are different questions and are labelled
differently on screen.

---

## J. Event refresh

No daily full re-run. A refresh is triggered by new material evidence: 10-Q, 10-K, 8-K, earnings
release, guidance, or a material corporate event.

`refresh-scan` builds `refresh_queue.json` by reading the SEC submissions caches this repository
already holds (`strategy_h_v2/d1_1/sec_raw`, `strategy_h/h_pv2c/sec_raw`, `strategy_c/e0/raw`,
`strategy_h/h0_5/sec_raw`). It performs no fetch: fetching belongs to
`app.dev.acquire_strategy_h_v2_fundamentals`, whose raw-source immutability and cached-skip
guarantees are reused rather than re-implemented.

The queue is careful about what a cache licenses it to say, because dropping that qualifier is how a
stale cache becomes a false all-clear:

| state | what it means |
|---|---|
| `REFRESH_DUE` | a material filing dated after the decision session is in the cache |
| `CACHE_NOT_NEWER_THAN_THESIS` | the cache predates the thesis, so it could not have seen anything |
| `NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE` | nothing new **up to the cache date**, which is not today |
| `NO_NEW_MATERIAL_EVIDENCE` | nothing new, from a cache refreshed today |
| `NO_SUBMISSIONS_CACHE` | this issuer has no cache; the queue knows nothing about it |

Every row carries `evidence_known_through`, and the file carries `unverified_since_cache` and an
explicit `claim_limit`.

**At launch this found one issuer already due.** AEYE filed an 8-K on 2026-09-18 (accession
`0001104659-26-108940`), two days after the decision session. Its REJECT therefore rests on a thesis
that has not seen that filing. The other seven read `NO_NEW_MATERIAL_EVIDENCE_AS_OF_CACHE` with
caches from 2026-09-21 and 2026-09-28, so nothing is claimed about the fortnight since.

Fundamental-trajectory change detection (`app.backtest.strategy_h_v2.change_detection`) is reused
unchanged when a refreshed issuer goes back through D3->D6.

---

## K. UI / API integration

Backend, all read-only:

```
GET /api/v1/strategies                      H appears as a registry row
GET /api/v1/strategies/STRATEGY_H_V2/status      runtime, launch, counts, evaluation
            .../account                         money fields null with reasons, never 0
            .../positions                       []
            .../trades                          []
            .../equity                          no points, no baseline
GET /api/v1/strategies/cards                H's card: counts and states where A/E show money
GET /api/v1/strategies/performance          + an H column, + an `h_forward` block
GET /api/v1/strategies/STRATEGY_H_V2/forward         the whole cohort
GET /api/v1/strategies/STRATEGY_H_V2/forward/{tkr}   one issuer in full
```

`/strategies/portfolio` is unchanged and stays A+E.

Frontend:

* `/strategy-h` - H's own screen: launch facts, D7 evaluation, the cohort table, forward outcomes
  and a per-issuer detail panel (the four research legs, the decision history, every horizon).
* `/strategy-compare` - a `ALL / A / E / H` selector. Under ALL every operating strategy gets a
  column and A+E additionally get Combined; selecting one narrows to it. H's own panels appear under
  ALL and under H.
* `/dashboard` - H gets a card from the registry automatically, showing decision counts, zero
  positions and horizon maturity instead of equity.
* Columns are derived from the registry (`enabled` and a paper mode), not from a list typed into a
  screen, so this file is the only place H's arrival is spelled out.

Rendering rules the tests pin: a refused Bear renders `N/A` with its reason in the title and never
`$0.00`; an unmatured horizon renders `PENDING` and never the latest price; `H Paper Positions = 0`
and `H Watchlist = 6` render as states.

---

## L. Daily operation

After the regular session settles:

```
1. A/E paper update                  unchanged (their own runners and timers)
2. python -m app.dev.run_h_v2_d7 update
     - collect the settled session into H's price store (1 Massive call per missing session)
     - recompute every horizon (matured / pending / incomplete)
     - rebuild the material-event refresh queue
3. material issuers that are REFRESH_DUE -> D3->D6 re-evaluation (manual, not automatic)
4. a resulting transition -> one appended ledger row with its cause
```

`update` cannot move a decision. It says so in its own output. A systemd unit for it would follow the
`usb-e-paper.timer` pattern; none is written or deployed here, because deployment is the operator's
call.

---

## M. Tests

Backend, `backend/tests/strategy_h_v2/forward/test_d7_forward_shadow.py`, 35 tests, all against a
temporary store so none of them reads the real snapshot:

* the contract is frozen by checksum (a tampered horizon list refuses to load);
* `D5_D2R_V1` is required and `D5_D2_V1` is refused;
* no decision state creates a position; only APPROVE is an entry candidate; sizing is `NOT_DEFINED`;
* A, E and H are all enabled, H is a forward shadow, and A's and E's registry rows are unchanged;
* the same ticker in A, E and H stays in its own book, and H's files contain neither A's nor E's id;
* the launch snapshot cannot be written twice for one issuer; a changed decision appends and the old
  thesis survives; a stored price session cannot be rewritten with other numbers;
* WATCH and REJECT create no position (parametrized); an APPROVE is fail-closed without sizing;
* a refused Bear is `N/A` with its reason, is never 0, has no distance, and its breach is `None`
  rather than `False`;
* the horizon window is the market calendar (5D skips the weekend; 21D -> 2026-11-02; 63D ->
  2027-01-04); an unmatured horizon is PENDING and computes nothing; **21D and 63D mature only on
  their own session, and the maturity price is that session's close, not the newest one** (asserted
  by walking all 63 sessions); a gap inside the window is INCOMPLETE and names the missing session;
* SPY is the benchmark on the same sessions and excess is the difference; MFE/MAE/TP-hit come from
  the daily high and low;
* the decision-to-launch interval is not forward evidence and every horizon is still pending;
* H answers the six questions with no invented zero; H's metrics come from the shared calculator and
  report `NO_CLOSED_TRADE` rather than 0; zero positions and zero APPROVE are states, with
  `verdict = INCONCLUSIVE` and `under_ae_paper_gate = false`;
* every contract transition is representable and a price is never a cause;
* H's prices live in H's own store, expected sessions exclude the baseline, and a gap is reported;
* a stale cache cannot claim "no new evidence", a cache older than the thesis says it could not have
  seen anything, and an issuer with no cache says so instead of reading as clear.

Frontend, `frontend/components/h-forward.test.tsx`, 21 tests: the summary's zero/6/2/0 counts, the
required D5 contract on screen, the position-rule note, `N/A` for a refused Bear with no `$0.00`
anywhere in the row, the incomplete-range flag beside a MEDIUM confidence, each issuer's binding
clause, PENDING for every horizon with its maturity session and no "vs SPY" figure, excess return
only once matured, the drift column labelled "forward 아님", A/E/H columns with Combined kept to
A+E, H's decision/maturity rows, narrowing to H alone, `N/A` where H has no capital book, and the
`ALL / A / E / H` selector.

A/E regression: `backend/tests/strategy_e_max/` (A/E operations, accounting V1, strategies API) and
`frontend/components/ae-operations.test.tsx` were run unchanged. Results are in the step's final
report.

---

## N. Known limitations

1. **No APPROVE sizing contract.** The single real gap. An APPROVE today produces a candidate and
   refuses to become a position (§F). It needs a separately frozen sizing rule, which is a user
   decision because it is a capital-allocation question, not a research one.
2. **A MEDIUM valuation confidence can carry a range that lost a leg.** VRRM's confidence rose to
   MEDIUM at the same time its Bear leg was refused, because `valuation_confidence` has no
   completeness input. D5-D2R declared this and handed it to D7 unresolved; D7 surfaces it (the
   `range_complete` flag and an on-screen warning) but does not fix it, because fixing it means
   changing a published field's semantics.
3. **Two issuers launched below their own Bear anchors** (TG, FG). Their downside legs were already
   breached before observation began, so a `bear_breach` on them carries little information.
4. **The pre-launch interval is unobserved.** Twelve sessions of the cohort's history sit between the
   thesis and the baseline. They are stored and reported as drift but can never be forward evidence.
5. **The refresh scan does not fetch.** It reports what the local caches know and labels every row
   with how far that knowledge reaches. Seeing filings newer than the caches requires running the
   acquisition step, and seven of the eight issuers are unverified since their cache date.
6. **AEYE enters the shadow with a known-stale thesis.** Its 2026-09-18 8-K post-dates the decision
   session, so it is `REFRESH_DUE` from day one. Its forward observation is still recorded (the
   cohort is frozen at launch by design), but its REJECT should be read as pending re-evaluation
   rather than as a current judgement. Re-evaluating it is a D3 to D6 run and therefore a model-call
   decision for the operator, not something this step performs.
7. **MFE/MAE are daily-bar extremes**, not the intraday path. A target touched between two daily
   extremes is invisible. Neither A's nor E's ledger records a path either, so this is not worse than
   the rest of the system - it is simply not better.
8. **`/strategy-h` is a client-rendered screen** reading the forward routes; the A/E portfolio
   simulation remains A+E and is not extended to three strategies (§G).
9. **No scheduler unit is deployed.** §L describes the daily job; running it is manual until an
   operator installs a timer.

---

## O. Launch state

```
H-V2-D7                 = LAUNCHED
contract                = H_V2_D7_FORWARD_SHADOW_V1 (sha256 33fe002f…c915b1a)
D5 contract             = D5_D2R_V1   (D5_D2_V1 refused by the launcher)
replay reproduces       = YES - 13/13 valuation rows and 13/13 decision records byte-identical
                          against D5_D2R-20261004T075216Z.json; decision counts match
decision session        = 2026-09-16
launch baseline session = 2026-10-02
cohort                  = 8   (INITIAL_D7_COHORT)
APPROVE / WATCH / REJECT= 0 / 6 / 2
H paper positions       = 0
H watchlist             = 6
price store             = 12 sessions (2026-09-17 .. 2026-10-02), Massive grouped daily, H's own files
1D / 5D / 21D / 63D     = PENDING for all 8
evaluation              = RUNNING · INCONCLUSIVE (SAMPLE_NOT_REACHED)
refresh queue           = AEYE REFRESH_DUE (8-K 2026-09-18); 7 unverified since their cache date
A / E                   = unchanged
model calls             = 0
cost                    = $0
production deployed     = NO
```

Pre-registered forward questions, fixed before any of the answers exist:

1. does an issuer whose Bull leg sat below its price (IDCC, TP2 -16.6%) cross it?
2. does any stated invalidation condition fire?
3. does an issuer whose RECENT_6M window governed get re-rated back toward its FULL_2Y range? This
   is the only item that tests D5-D2R, and it is the reason that step was done before this one.

No performance verdict will be produced before the pre-registered sample (21D and 63D matured for
all eight). Until then `INCONCLUSIVE` is the correct reading, and thresholds are not to be changed
because APPROVE stayed at zero, because a WATCH rose, or because a REJECT rose.
