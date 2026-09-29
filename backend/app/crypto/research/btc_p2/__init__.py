"""BTC-P2: given that a large move looks likely, can we say which way it goes?

BTC-P1 answered a narrower question than it appeared to. It predicts whether BTC makes a large
move, with Brier skill +0.180 and AUC 0.727 on its strongest target, but its directional AUC was
0.502 to 0.533: the UP and DOWN probabilities rise together because both are driven by
volatility. A model like that cannot produce a LONG or a SHORT.

So this study measures direction directly. The target is which side is touched first, not whether
a side is touched, and the headline metric is discrimination between UP and DOWN rather than each
one's own AUC. That distinction is the whole point: P1 would have passed a per-direction gate
while carrying no directional information at all.

No orders, no sizing, no cost, no PnL. Probability quality only.
"""
