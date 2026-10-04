"""Run the isolated preview. Nothing in production imports this module.

    MS_V0_ROOT=/path/to/ms_v0_data python -m app.crypto.liquidity_map [port]
"""
from __future__ import annotations

import sys

from .api import DEFAULT_PORT, app


def main(argv: list[str] | None = None) -> int:
    import uvicorn
    args = sys.argv[1:] if argv is None else argv
    port = int(args[0]) if args else DEFAULT_PORT
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
