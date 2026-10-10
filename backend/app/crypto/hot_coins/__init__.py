"""HOT Coin Finder V0: a read-only, on-demand scan of Binance USD-M perpetuals.

Public market data only. Nothing in this package holds a key, reads an account, or knows any
order route, and a structural test keeps it that way: it imports neither `crypto.live` nor the
paper engine. The contract it implements is the one HOT Coin Finder R0 fixed before looking at
outcomes (docs/crypto/hot_coin_finder_r0), so changing a gate here is a contract change.
"""
