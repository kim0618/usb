"""Strategy B on the shared backtest workspace: dataset, adapter and scanner-only runs.

This package is the bridge between the pure research layer (``app.strategy_b``, stdlib only,
no I/O) and the shared backtest infrastructure (workspace, manifest, calendar, run store,
``app.backtest.engine``). Nothing here imports Strategy A's strategy, risk, broker or
portfolio code, and nothing in Strategy A imports this package.
"""
