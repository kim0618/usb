# H-V2 Paper Execution Contract V1

Frozen 2026-10-13 KST, before any H paper result existed (no APPROVE, no paper launch, no fill).
Machine-readable contract: `docs/operations/h_paper_execution_v1.json`, sha256
`89f7beed084dd2b9e8beb4c0a6c1b030a6e852e72fa9d1c4835185886c696c9b` (canonical recipe as for every other
frozen contract). Code refuses to load a contract whose checksum does not match
(`app.strategies.h_paper.contract`).

This contract adds a paper book to Strategy H. It does not reopen research: no D3/D4/D5/D6 rule,
threshold or valuation changes, and no rule here was fitted to any price path. The D7 forward shadow
contract (`h_forward_shadow_v1.json`, sha `33fe002f…`) stays frozen and in force. The shadow itself
still never holds a position. This book is separate, and its money is never mixed into forward
outcomes.

## 1. Audit of A/E paper infrastructure (what was reused)

| Component | A | E | Reused for H? | H-specific extension |
|---|---|---|---|---|
| Fill engine | `SimBroker`, next-bar open | `SimBroker`, 09:30 bar open | **Yes, unchanged** | One synthetic `MinuteBar` per fill at the execution price; scope `STRATEGY_H_V2-PAPER` |
| Cost model | `execution_v0` (spread 10, slippage 5 price-embedded; commission 10bp/leg) | `RoundTripCostConfig` 10bp | **Yes, `execution_v0`**: this is what the D7 contract already named for H | none |
| Accounting | ACCOUNTING_V1 (`app.broker.accounting`) | V1 | **Yes** | Daily mark-to-market NAV (weeks-long holds) |
| Sizing | risk_v1, stop-based, fractional | `floor(equity × weight / ref)` whole shares, max 3 selected | **E's floor convention** | 3 equal slots of 1/3 NAV; no stop needed |
| Account | paper DB, one account (unique open symbol per account) | runtime files, book rebuilt per session | Neither (A collides on a shared ticker; E's engine is single-session) | Own append-only event book under the D7 root |
| Calendar | `MarketCalendar` | `MarketCalendar` | **Yes** | none |
| Read layer | `books.a_books`, ledger | `books.e_books`, ledger | `performance.Book`, `DailyPoint`, the six shared routes | `h_paper.service` trade normaliser |
| Paper gate | yes | yes | **Not under it** (frozen A/E gate) | none in V1 |

There is no shared A/E paper account abstraction, so H keeps its own book: initial equity $10,000
(E's `DEFAULT_INITIAL_EQUITY`), leverage 1.0, no shorting.

## 2. Launch and no-backfill

* `H_PAPER_V1_LAUNCH` (`d7/paper/launch.json`) is written once by `paper-launch`. A second launch is
  refused, so the clock cannot be restarted.
* Only a forward-ledger row with `decision_time` **strictly after** the launch is a signal. Before it:
  `SHADOW_ONLY`. After it: `SHADOW_PLUS_PAPER`.
* The six WATCH and two REJECT issuers held since 2026-10-04 are never turned into hypothetical trades.

## 3. Entry

| Rule | V1 |
|---|---|
| Entry signal | `APPROVE` only. WATCH, REJECT and NOT_DECISION_ELIGIBLE never enter |
| D5 contract | `D5_D2R_V1` required, else `APPROVE_REFUSED_D5_CONTRACT` |
| Execution session | first regular session whose **open is strictly after** `decision_time` |
| Price | that session's regular open (raw), through `SimBroker` under `execution_v0` |
| Until the bar is stored | `PENDING_ENTRY` |
| Missing open | stays pending; fills at the next stored open (`execution_delay_sessions` recorded). A close is never used |
| Size | `min(NAV_prev_close / 3, cash) / buy_effective_price(open)`, rounded down to whole shares |
| Slots | max 3 open. No free slot → `APPROVE_WAITING_SLOT`, entered in decision-time order at the first open with a free slot, if still APPROVE |
| Duplicates | identity `ticker|thesis_version|decision_time`. Duplicate → ignored. A ticker already open or pending → `APPROVE_IGNORED_ALREADY_OPEN` (no pyramiding, no averaging down, no forced replacement) |
| Downgrade before fill | `APPROVE_CANCELLED_DECISION_CHANGED` |

## 4. Exits

Evaluated on daily bars from the session **after** the entry session. Levels are the TP1/TP2/Bear of
the APPROVE row that opened the position, frozen at entry.

| Exit | Trigger | Quantity | Raw fill |
|---|---|---|---|
| TP1 (D5 Base) | high ≥ TP1 (first time) | `floor(initial × 0.5)` | open if open ≥ TP1, else TP1 |
| TP2 (D5 Bull) | high ≥ TP2 | all remaining | open if open ≥ TP2, else TP2 |
| BEAR_BREACH | low ≤ Bear | all remaining | open if open ≤ Bear, else Bear |
| DECISION_DOWNGRADE_WATCH / _REJECT | ledger row APPROVE→WATCH / APPROVE→REJECT | all remaining | next session open after that row |
| THESIS_INVALIDATED | ledger row with cause `THESIS_INVALIDATED` (re-evaluation, cites sources) | all remaining | next session open |

* **Same bar:** `execution_v0` `ambiguous_bar_policy = WORST_CASE`. Bear before TP when both are touched
  and the open does not decide it. TP1 then TP2 in one bar is allowed.
* **Bear NOT_AVAILABLE (VRRM, `NEGATIVE_IMPLIED_EQUITY`):** `bear_rule = NOT_EVALUATED`. No 0 and no
  substitute stop. Only TP, downgrade and invalidation apply.
* A missing TP1 or TP2 disables that exit.
* **Invalidation is not detected mechanically.** D3's invalidation candidates are free text with
  sources, and no frozen contract evaluates them. V1 acts only on an explicit re-evaluation record.
* **No new stop numbers:** no fixed %, no ATR stop and no time stop.

## 5. Ledger, NAV, integrity

* `d7/paper/events.jsonl` (append-only) has these event kinds: `PENDING_ENTRY`, `APPROVE_WAITING_SLOT`,
  `BUY`, `SELL_TP1`, `SELL_TP2`, `SELL_DECISION_DOWNGRADE`, `SELL_INVALIDATION`, `SELL_BEAR`,
  `EXIT_PENDING`, `SIGNAL_NO_ACTION`, `NAV` and the refusal and missing-data events.
* Position id: `STRATEGY_H_V2|<entry_session>|<ticker>`. Each fill carries raw, fill price, spread,
  slippage, commission, fx and `cash_after`.
* NAV = cash + Σ shares × close at each stored session. If a held ticker has no close, the session is
  `NAV_INCOMPLETE` and no last price is substituted. An expected session missing from the store stops
  processing (`stalled_at`), so a Bear or TP touch is never skipped.
* Every run recomputes the full event list from the ledger and the prices. The stored events must be
  a prefix of the recomputation, or nothing is appended (`PAPER_INTEGRITY_FAILURE`, exit 3).
* `verify` invariants:
  * no entry from a non-APPROVE row
  * no fill at or before its decision
  * at most 3 open positions
  * no APPROVE entered twice
  * no second position in an open ticker
  * cash + positions value = equity
  * cash ≥ 0
  * D5 = D5_D2R_V1

## 6. Research budget

* No model call is ever scheduled automatically.
* `production_daily_budget_usd = null` (NOT_APPROVED).
* Every research batch freezes its own sample, model and hard cap in a preregistration before any
  call. The first batch is `H_V2_REFRESH_BATCH_R1_PREREGISTRATION_V1.md`.

## 7. What V1 does not claim

* H is not under the A/E paper gate, and H stays out of the A+E combined column.
* Forward Shadow 1D/5D/21D/63D, SPY excess, MFE and MAE are unchanged and are reported separately from
  paper PnL: decision quality versus portfolio result.
* An APPROVE→APPROVE re-evaluation is not a D7 transition, so a position keeps its entry levels.
