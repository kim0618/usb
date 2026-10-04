"""BTC Market Structure V0: a read-only Binance USDⓈ-M forward research collector.

This package captures continuous orderbook depth and public aggregate trades for BTCUSDT and
turns them into second-sampled market structure observations. It is a *data foundation* and
nothing else. It holds no credentials, calls no private or order endpoint, produces no score and
no signal, and shares no import, state or file with `app.crypto.paper`, `app.crypto.live` or
`app.crypto.terminal`. It runs as its own process so that a collector failure cannot stop the
trading service.

The frozen contract is `docs/crypto/market_structure_v0/DATA_CONTRACT_V0.md`
(`btc-ms.v0.1`). Semantics change by issuing a new version, never by editing that document.
Every session record carries the contract's SHA256 so a dataset states which contract produced
it.

Run it with:

    MS_V0_ROOT=/path/to/data python -m app.crypto.market_structure_v0.collector
"""

#: Contract/schema/algorithm version. Written into every record.
VERSION = "btc-ms.v0.1"
#: Code version, moved when behaviour changes without a contract change.
COLLECTOR_VERSION = "btc-ms.collector.0.1"
EXCHANGE = "binance_usdm"
SYMBOL = "BTCUSDT"

__all__ = ["VERSION", "COLLECTOR_VERSION", "EXCHANGE", "SYMBOL"]
