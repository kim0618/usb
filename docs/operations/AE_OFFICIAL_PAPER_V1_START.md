# US-B A/E Official Paper V1 — Start Record

```text
US-B STOCK — PAPER VALIDATION PHASE

Deployment status   STARTED (deployed 2026-09-27 14:05 KST)
Official start      2026-09-28 (ET session; one date for A and E)

Strategy A          OFFICIAL PAPER V1 / ACTIVE      start 2026-09-28
Strategy E          OFFICIAL PAPER V1 / ACTIVE      start 2026-09-28   (E-MAX V1)
Strategy B          RETIRED
Strategy C          RETIRED
Strategy D          RETIRED

Accounting          V1
E cost              STRATEGY_E_COST_V1 / COST_10BP (one round-trip deduction)
A cost              execution_v0 (unchanged)
Paper evaluation    AE_PAPER_V1
Gate                AE_PAPER_EVALUATION_GATE_V1, sha256 7a139d6b389cd547cd4384801697680279949f81d96c5765919059560eddd2dd
```

## 1. What was deployed

| | |
|---|---|
| Backend | `df4d55a` — Accounting V1, E cost contract, official/legacy ledger, A/E API |
| Frontend | `2bd9265` — A/E operating UI, official/legacy paper split |
| Clock setting | `AE_OFFICIAL_PAPER_START=2026-09-28` in `/etc/systemd/system/usb-backend.service.d/ae-official-paper.conf` |
| Runtime record | `/root/usb_runtime/ae_official_paper_v1.json` |
| Rollback | `/root/usb_runtime/backups/ae_v1_20260927` |

Details of the deployment and its checks: `AE_ACCOUNTING_V1_AND_E_COST_V1.md` section 7.

## 2. Legacy (PRE-GATE)

| Strategy | V0 trades | Recorded net (V0) | Status |
|---|---:|---:|---|
| A | 3 | +72.3555 | LEGACY, kept as recorded |
| E | 3 | -123.1542 | LEGACY, kept as recorded (`paper_state/`, read-only) |

V0 rows never enter official metrics, the gate, the combined column or the portfolio view.

## 3. Official starting counters (verified on the server at start)

| Strategy | Sessions | Trades |
|---|---:|---:|
| A | 0 / 60 | 0 / 30 |
| E | 0 / 60 | 0 / 60 |

Gate verdicts at start: A INCONCLUSIVE, E INCONCLUSIVE (SAMPLE_NOT_REACHED). The thresholds are
the ones in `AE_PAPER_EVALUATION_GATE_V1.md`; this record changes none of them.

## 4. Rules during paper validation

Forbidden until the gate sample is reached and evaluated:

- A parameter tuning
- E H5 tuning, E-MAX tuning
- cost contract changes (A or E)
- Paper Gate changes
- mixing V0 and V1 rows
- modifying a strategy because of recent losses

The next official evaluation is at A: 60 sessions + 30 trades, E: 60 sessions + 60 trades. Until
then every verdict is "sample insufficient". E's H5 forward research validation is separate
evidence and is not merged with this paper gate.

## 5. 5-year data purchase gate

Not now. It is considered only after the paper gate has a sufficient sample and A or E passes.
Then the order is:

```text
5Y historical OOS -> A/E long-history validation -> A+E portfolio validation -> small live decision
```

## 6. Next checkpoints

- Operations smoke: after the first real V1 trade (E's first run is 2026-09-28 03:45 ET).
- Formal evaluation: A 60 sessions / 30 trades, E 60 sessions / 60 trades.
