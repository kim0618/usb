"""Q1/Q2/Q3 Forward Detector V1 (`q123-forward.v1`): Market Context R0's T1 families, collected forward.

Research and forward tracking only. Contract: docs/crypto/q123_forward_v1/CONTRACT_Q123_FORWARD_V1.md.
The questions are R0's F1-F4 called through `market_context_r0.families.joint_family` unchanged; the
liquidity and flow layers are the Context Collector V1.1 journal read through its own reader; the
structure layer is R1's engine on public Binance 1m klines. Nothing here reaches an order, account,
leverage or AUTO path, and nothing here is a LONG / SHORT / WAIT verdict.

    python -m app.crypto.research.q123_forward_v1 fetch --out <dir>
    python -m app.crypto.research.q123_forward_v1 run --klines <file> --root <collector root> [...]
"""

VERSION = "q123-forward.v1"
CONTRACT_RELATIVE_PATH = "docs/crypto/q123_forward_v1/CONTRACT_Q123_FORWARD_V1.md"
CONTRACT_SHA256 = "9d855ad7d340a2feb08a15691eab84cf14ba006462f11b796bdd9ce06ec5a5ba"

__all__ = ["VERSION", "CONTRACT_RELATIVE_PATH", "CONTRACT_SHA256"]
