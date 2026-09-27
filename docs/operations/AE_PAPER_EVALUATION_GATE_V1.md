# A/E Paper Evaluation Gate V1

```text
contract   AE_PAPER_EVALUATION_GATE_V1
status     FROZEN_BEFORE_EVALUATION (2026-09-27 KST)
file       docs/operations/ae_paper_evaluation_gate_v1.json
sha256     7a139d6b389cd547cd4384801697680279949f81d96c5765919059560eddd2dd  (canonical JSON)
code       backend/app/strategies/paper_gate.py (verifies the checksum on every load)
```

## 1. What this gate answers

Does each operating strategy, unchanged, earn a cost-adjusted positive result in paper, with
acceptable drawdown, divergence and operational reliability? Verdicts: `PASS`, `INCONCLUSIVE`,
`FAIL`.

This is an **operational paper gate**. It does not define or replace the E-MAX forward promotion
gate: `E_MAX_F0_FORWARD_SHADOW_PROTOCOL_V1` section 13 keeps that as `NOT DEFINED` until its own
contract is frozen before the N = 250 evaluation.

## 2. What was known at freeze

- A: 14 daily rows (2026-09-08..2026-09-25), 3 closed trades.
- E: 1 completed OFFICIAL session (2026-09-25), 3 closed trades.

Thresholds come from development frequency and risk, not from these rows. No metric of these rows
was computed before this file was frozen.

## 3. Thresholds

| Condition | Strategy A | Strategy E (E-MAX V1) |
|---|---:|---:|
| Evidence book | paper DB account of the active runtime | OFFICIAL_KIWOOM_PAPER only |
| Minimum operating sessions | 60 | 60 |
| Minimum closed trades | 30 | 60 |
| Net PnL | > 0 | > 0 |
| Expectancy per trade | > 0 | > 0 |
| PF | >= 1.20 | >= 1.10 |
| MDD (daily equity, fraction of peak) | >= -8% | >= -30% |
| Divergence | paper vs recorded-session replay decision mismatch <= 5% of sessions (external: A parity runner; PENDING until supplied) | mean(paper session return - recorded net_10bp view) >= -0.20% |
| Operational error rate | PASS <= 5%, FAIL > 20% once >= 20 sessions | same |

Why these numbers:

- **A** traded 3 times in its first 14 sessions, and the A V2 baseline 37 times over two years.
  30 trades is the smallest sample on which PF and expectancy mean anything, so A may stay
  INCONCLUSIVE for months. The V2 baseline MDD was 2.94%, so -8% is a failure, not noise.
- **E-MAX V1** development traded about one session in two with up to three names at 2.0x/3.0x
  exposure. Its 10bp development MDD was -29.9%, so -30% is the ceiling, and 60 trades is about 30
  active sessions. Divergence allows realised costs 20bp per session worse than the 10bp
  development scenario.
- **Error session:** for E, an engine ending in ERROR or NO_DECISION, or a session with no engine
  file at all. A decided session with no candidate is normal. For A this layer records no error
  sessions yet, so the condition stays PENDING until the operator enters them.

## 4. Accounting rule

All evaluated trades must share one accounting convention; a mix reads `INCONCLUSIVE`
(`ACCOUNTING_MIXED`) until the rows are restated by a separate, explicit decision.

- `TOTAL_COST_DEDUCTED_V0` (rows written before the PnL accounting contract): spread and slippage
  are charged twice, so the rows are conservative. A PASS under V0 stands. A PnL-based FAIL under V0
  is not final and reads `INCONCLUSIVE` (`ACCOUNTING_V0_OVERCHARGED`).
- `CASH_CHARGES_ONLY_V1`: cash-true. PASS and FAIL are both final.
- An operational FAIL (error rate) is final under either convention.

## 5. Verdict order

1. MDD past the floor or error rate past the fail line: `FAIL` straight away, before the sample
   (except an MDD breach on V0 rows, see section 4).
2. Sample not reached: `INCONCLUSIVE` (`SAMPLE_NOT_REACHED`).
3. Mixed accounting: `INCONCLUSIVE`.
4. Any condition failed: `FAIL` (or `INCONCLUSIVE` on V0 rows for PnL conditions).
5. Any condition pending: `INCONCLUSIVE`.
6. Otherwise `PASS`.

## 6. What may not change

None of these thresholds, and no A or E rule, may change in response to paper results. A change is
a new contract version and is evaluated only on sessions after its own freeze.

## 7. Known structural issue at freeze (reported, not corrected)

SimBroker's default execution config charges 25bp per leg (spread 10 + slippage 5 + commission 10),
so 50bp per round trip under V1 accounting and 80bp under V0. E's frozen cost contract
(`STRATEGY_E_COST_RULES_V1`) stresses 5-20bp per round trip. On 2026-09-25 the E session was
+34.90 USD at raw prices and +14.77 USD in the 10bp development view, but -63.88 USD in paper
under V1 accounting and -123.15 USD as recorded (V0). The E divergence condition will fail from
this cost mismatch alone. Aligning the paper execution cost with E's contract, or accepting it, is
an operator decision. It is not made here.

**Resolved 2026-09-27 (no threshold changed):** E's paper execution now applies its frozen cost
contract (COST_10BP round trip, `AE_ACCOUNTING_V1_AND_E_COST_V1.md`), all new rows are
ACCOUNTING_V1, and only official V1 rows from `AE_OFFICIAL_PAPER_START` reach this gate. The gate
JSON and its checksum are unchanged.
