"""A-MOVER-SCANNER-V1: a premarket opportunity scanner for Strategy A, research path only.

The deployed morning Scanner ranks Kiwoom's trade-value TOP10 with ``QuantScanner``. Trade
value is a property of the company, not of the morning, so in the US market that board is
structurally the same dozen mega caps every day, and Strategy A's entry gate (premarket gap
in 2-15% and premarket volume at or above 5% of the 20-session average) is a property of the
morning. The two disagree, which is why 79 of 82 paper evaluations were PREMARKET_REJECTED.

This package answers the scanner question instead: which symbols are actually moving on real
money before the open. It computes nothing about entries, stops, risk, exits or the GPT prompt,
and it never writes to the Production universe, Scanner or Strategy path. ``handoff`` exists so
the existing GPT pipeline can read this scanner's output without the prompt changing.

``actionability`` is V1.1: the same discovery, handed off through a mask that keeps only the
candidates the current Strategy A execution contract could admit, so a GPT call is not spent on
a symbol the premarket gate will refuse for its gap. It reads the band from ``StrategyConfig``
and changes no score, weight or universe rule, which is why ``config.checksum`` is still V1's.
``handoff_study`` is its replay-free validation over the same 83-session sample.

Every threshold lives in ``config`` and is checksummed there, so a report can name the exact
rules that produced it.
"""
