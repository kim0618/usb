# A/E Paper Accounting V1 and Strategy E Cost Contract (2026-09-27)

```text
contract   AE_OFFICIAL_PAPER_V1   docs/operations/ae_official_paper_v1.json
sha256     b7c0943db31607f64708c353bbb219c3c41f6c63fbefa3f359f974971c458959 (canonical JSON)
gate       AE_PAPER_EVALUATION_GATE_V1 7a139d6b... unchanged
```

## 1. V0 root cause

`execution_v0` builds every SimBroker fill like this (`app/broker/sim.py`, `app/execution/costs.py`):

```text
raw bar open
  -> fill_price = raw +/- raw x (spread 10 + slippage 5) bp          price-embedded
  -> commission = raw x qty x 10 bp, fx = 0                           cash charge
  -> total_cost = spread + slippage + commission + fx                 (25 bp of raw notional per leg)
cash   : BUY pays fill_price x qty + commission + fx, SELL receives fill_price x qty - commission - fx
gross  : (exit fill - entry fill) x qty                               already net of spread/slippage
V0 net : gross - total_cost                                           spread/slippage charged again
```

So V0 net understates cash by exactly the price-embedded part, 15 of every 25 bp of cost.
A's three server trades: total_cost 13.5384 x 15/25 = **8.1231** = equity delta 80.4785 - ledger
72.3555. E's 2026-09-25 session: fill gross -24.37 - total_cost 98.78 = recorded -123.15; the cash
figure under the same execution config is -63.88.

## 2. ACCOUNTING_V1 (adjusted-fill method)

`app/broker/accounting.py`: spread and slippage stay inside the fill price, commission and FX are
cash charges subtracted once. `net = gross(fill prices) - (commission + fx)`. For every closed
lifecycle, `cash delta = sum(net_pnl)`. `total_cost` becomes analytics only. This is the
accounting contract of 2026-09-17, included here unchanged.

## 3. Cost contracts

| | A | E |
|---|---|---|
| Source | `execution_v0` (`docs/SIM_BROKER_SHADOW.md`, A parity audit) | `STRATEGY_E_COST_RULES_V1` (E-D4, b2229fe5...) + E-MAX V1 `definition.cost.primary` (b30a3e95...) |
| Official | spread 10 + slippage 5 in price, commission 10 bp, per leg | **COST_10BP, total round trip, one deduction, no leg split** |
| Stress | none | COST_05BP, COST_15BP, COST_20BP (GROSS_0BP diagnostic) |
| Changed now | no | paper now applies the frozen contract |

Before this change the E paper runner passed no execution config, so E paid A's 25 bp per leg
(50 bp round trip under V1, 80 bp under V0) against a contract of 10 bp round trip.

E is applied by `RoundTripCostConfig` (`app/execution/config.py`): both legs fill at the raw price,
and the whole 10 bp of entry notional is one cash charge on the entry fill, so
`session net = exposure x (gross - 0.0010) x equity`. It is a subclass, so A's `ExecutionConfig`
fields and every config fingerprint are unchanged. The bp value is read from the two frozen files
with their canonical checksums (`app/strategy_e_max_rt/cost.py`). Stress results stay the recorded
`fixed_bp_views`; they are not charged.

## 4. Where rows live

| | Legacy V0 (kept, read-only) | Official V1 |
|---|---|---|
| A | `simulation_trades` rows detected V0 (net = gross - total_cost) | rows detected V1, entered on or after the official start |
| E | `run/paper_state/<evidence>/STRATEGY_E_MAX_V1` | `run/paper_state_v1/<evidence>/STRATEGY_E_MAX_V1` |

The E engine stamps `execution_version`, `accounting_version` and `cost_contract_version` on every
new book and session, and refuses (ERROR `BOOK_EXECUTION_VERSION_MISMATCH`) to append to a book
opened under another version, so the V0 book can never be continued.

A rows carry no stored version column: a schema change would need migration 0019 on top of the
still-undeployed 0018. The version is detected from the row's own arithmetic, which is exact for A
because its spread and slippage are non-zero. Any non-V1 row inside the official window shows in
`accounting_mix`, and the gate reads it as `ACCOUNTING_MIXED`.

The ledger publishes `recorded_net_pnl` as stored and, for V0 rows, `recomputed_v1_net_pnl` beside
it (A: gross - 0.4 x total_cost; E: gross - cash part of each leg's fill_cost). Nothing is
overwritten.

## 5. Official clock

`AE_OFFICIAL_PAPER_START` (ET session date) in the usb-backend environment. Unset means
NOT_STARTED: official metrics are empty and the gate sample is 0. Set it to the first session that
runs after this change is deployed and verified. It is one date for A and E. Sessions, trades, PF, MDD,
divergence and error rate count from it. V0 operating days never count toward the 60 sessions.

## 6. Deployment list (not deployed)

Backend files: `app/broker/{accounting.py (new), sim.py}`, `app/execution/{config.py, costs.py}`,
`app/strategy_e_max_rt/{cost.py (new), engine.py, readback.py}`, `app/dev/run_e_rt2_dryrun.py`,
`app/strategies/*`, `app/api/strategies.py`, `docs/operations/*.json|.sha256`,
`docs/backtest/strategy_e_*/{cost, e-max v1} rules` (already on the server, read for checksums).
Frontend: build locally, ship `.next-build` + sources.
Restarts: `usb-backend` (A's broker is rehydrated; it holds no open position today, so no trade
straddles the accounting change). `usb-e-paper` runs as a timer and picks the code up on its next
run. No DB migration.
After: health, `/api/v1/strategies/{cards,performance,portfolio}` show official 0 / legacy 3 per
strategy, then set `AE_OFFICIAL_PAPER_START` to the next session and restart `usb-backend`.
