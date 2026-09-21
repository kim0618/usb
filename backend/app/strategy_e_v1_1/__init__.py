"""Strategy E Trading V1.1: the E-R1 point-in-time universe correction.

V1.1 changes one thing relative to Trading V1: membership of the 09:25 decision universe may no
longer depend on whether session D's 09:30 bar (minute tape) or D's daily open (grouped daily)
exists. Both were pre-selection filters in V1 and neither is knowable at 09:25.

H5, candidate priority, the three-position cap, entry, exit, cost and sizing are not
re-implemented here. The decision step calls ``app.strategy_e.signal.evaluate_h5_signal`` and
execution calls ``app.strategy_e.execution.build_entry_records`` unchanged; E-D3, E-D4 and E-D5
apply downstream exactly as frozen.

This package lives outside ``app.strategy_e`` on purpose: E-D6's ``code_digest`` hashes every
module in that directory, and adding a file there would move the identity of a frozen result.
"""
