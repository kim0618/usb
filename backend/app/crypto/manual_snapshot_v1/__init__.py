"""Manual Trade Snapshot Store V1: a read-only follower that records context around the user's own
BTC LIVE trades for later research.

Contract: ``docs/crypto/manual_trade_snapshot_v1/CONTRACT_MANUAL_TRADE_SNAPSHOT_V1.md``.

This package reads files that other processes write (the terminal's Binance fill cache, the LIVE
audit mirror, the Context Collector journal) and public market data, and writes only its own store.
It imports nothing from the order, terminal, live-router, AUTO, ExitGuard or collector packages.
"""
