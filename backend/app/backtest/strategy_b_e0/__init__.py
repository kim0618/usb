"""The B-E0 screening run: contract, universe, preflight, replay wiring and the gate.

Everything here orchestrates. No module in this package decides whether a candidate is good,
when to enter or when to leave: those answers come from ``app.strategy_b`` (the research layer)
and ``app.backtest.strategy_b`` (the session engine), and this package only arranges the inputs,
records what happened and applies the pre-registered verdict rules to the result.

If a module here ever appears to make a judgement, that is a defect, not a feature.
"""
