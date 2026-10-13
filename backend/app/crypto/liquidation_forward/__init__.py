"""D5.3 liquidation forward collector (research data foundation only).

Public endpoints only; no keys, no private or order endpoints. Shares no import, state or file with
app.crypto.paper / app.crypto.terminal. Contract: docs/crypto/CRYPTO_D5_3_LIQUIDATION_FORWARD_CONTRACT_V1.md
"""
COLLECTOR_VERSION = "liqfwd.1.1"
SCHEMA_VERSION = "liq.v1"
EXCHANGES = ("bybit", "binance", "okx")
