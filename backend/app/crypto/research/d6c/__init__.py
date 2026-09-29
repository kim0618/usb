"""D6-C historical replay: the frozen FDN-V1 strategy measured against its own gates.

Two engines meet here and neither is re-implemented.

    app.crypto.research.d6    decides. Features, buckets, filters, score, LONG/HOLD.
    app.crypto.paper          executes and accounts. Fills, fees, funding, equity.

This package is the harness between them: it walks the D2 grid, asks the first engine what the
strategy would do, tells the second engine to do it, and measures the result against the gates
frozen in CRYPTO_D6_C_BACKTEST_CONTRACT_V1.md.

It is deliberately the only place in D6 that touches both. The d6 package stays isolated from
the paper account (its own tests enforce that); this harness is allowed to see both because
somebody has to, and keeping that somebody in one named module is what makes the boundary
reviewable.
"""
