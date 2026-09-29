"""D5.4 cost-first event strategy research.

The unit of discovery here is an *executable event trade*, not a bar-level conditional return.
D6-C established why that matters: the bar-level tables reported a HIGH volatility gross of
+34bp where the executed strategy measured +2bp, and reported BEAR as the strongest regime where
the executed strategy lost most. A number that cannot be traded is not evidence.

So every candidate in this package is executed from the first moment it exists: events are
merged into episodes, entered once at the earliest legal price, filled by the real paper engine,
charged real fees and funding, and judged on the equity curve that comes out.

Contract: docs/crypto/CRYPTO_D5_4_COST_FIRST_EVENT_CONTRACT_V1.md
"""
