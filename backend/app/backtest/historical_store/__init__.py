"""US-B Common Historical Store: provider raw kept once, read by every strategy through its own view.

Layout under the shared workspace (``1_US-B``)::

    market_data/raw/massive/{grouped_daily,reference_tickers,splits,minute,per_symbol_daily}/
    market_data/metadata/{historical_snapshot,authority,coverage}/
    state/historical/

Nothing here moves, rewrites or re-compresses an existing file. Legacy A datasets
(``market_data/normalized/...`` + ``state/collector_manifest.sqlite3``) are read only.
"""
