"""PAPER-only execution engine for BTCUSDT Linear Perpetual.

No order endpoint is reachable from this package. It holds no exchange credentials and
imports nothing from the equity side of the repository.

Contracts implemented here:
  docs/crypto/CRYPTO_PAPER_EXECUTION_CONTRACT_V1.md
  docs/crypto/CRYPTO_TRADING_STATE_MACHINE_V1.md
"""

PAPER_ENGINE_VERSION = "d4.1"
LEDGER_SCHEMA_VERSION = "crypto.paper.ledger.v3"  # v3 marks catch-up funding as an estimate
INPUT_SCHEMA_VERSION = "crypto.paper.input.v1"
