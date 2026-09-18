"""Strategy-agnostic replay plumbing: an adapter contract, a clock, a driver, a run store.

What lives here is what two strategies can share without either one's decisions leaking into
the other: when a replay moment exists (``clock``), the loop that hands every moment to one
strategy adapter in global chronological order (``driver``), how a run names itself
(``identity``), and how an immutable research run is stored (``store``). Every rule it needs
from the existing core is imported, not restated: the calendar, the bar-completion rule and
the tick epsilon from ``app.backtest.replay.clock``, the digest recipe from
``app.backtest.research.contract``, and the write contract from ``app.backtest.workspace``.

Strategy A is not routed through this driver. ``MultiSymbolPortfolioReplay`` fixes
Production's entry, position and end-of-day order inside its own tick loop; moving it here is
a separate, regression-gated refactor. Strategy B's scanner is the first adapter.
"""
