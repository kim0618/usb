"""Strategy C V2 decomposition research (C-V2A short horizon, C-V2B volatility expansion).

Read-only on top of the frozen C-M V1 study: the V1 package supplies panel, features,
eligibility, matching and base labels unchanged, and this package only adds forward
measurements and statistics declared in ``docs/backtest/strategy_c/v2/c_v2ab_rules_v1.json``.
Nothing here creates an order, a position or a strategy adapter, and nothing writes to the V1
raw cache or V1 run directories.
"""
