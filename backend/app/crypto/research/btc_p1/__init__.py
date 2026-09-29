"""BTC-P1: can we estimate the probability of a large BTC move, hours ahead?

Six trading studies in a row found nothing (D5, D5.1, D5.2, D6 FDN-V1, D5.4, D5.5). Each mixed
prediction with execution, so when the edge disappeared it was never clear which half had failed.
This study removes execution entirely: no orders, no fills, no cost, no position, no PnL. The
only question is whether the probability of a move large enough to matter can be estimated better
than its base rate, and whether those probabilities are calibrated.

If that fails, no trading rule built on top of it could have worked either.
"""
