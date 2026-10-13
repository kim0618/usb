"""Run the isolated panel preview.

    MS_V0_ROOT=/path/to/journal python -m app.crypto.market_context_v1 [port]

Nothing in the deployed terminal imports this module or the app it serves.
"""
from __future__ import annotations

import sys

import uvicorn

from .api import DEFAULT_PORT, app

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
