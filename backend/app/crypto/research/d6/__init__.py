"""FDN-V1 score engine: the D6-A contract turned into a deterministic decision function.

    docs/crypto/CRYPTO_D6_AUTO_STRATEGY_DESIGN_CONTRACT_V1.md   the authority
    data/research/crypto/d6/strategy_contract_v1.json           the machine-readable copy
    docs/crypto/CRYPTO_D6_B_SCORE_ENGINE_REPORT_V1.md           what this build does and does not do

The package computes features, buckets, hard filters, a score and a LONG/HOLD decision, plus the
size and stop the contract derives from them. It does not compute a forward return, a PnL or any
performance figure, it does not build or send an order, and it does not read the manual paper
account. D6-C is the stage that measures outcomes; keeping that out of here is what stops D6-C
from being contaminated by its own inputs.

Import direction is one way: this package reads `..dataset` (the D5 loader) and nothing from
`app.crypto.paper` or `app.crypto.terminal`. `test_d6_isolation.py` enforces that.
"""
