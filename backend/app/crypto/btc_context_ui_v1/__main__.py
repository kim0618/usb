"""`python -m app.crypto.btc_context_ui_v1 [--root ...] [--port 8014]`"""
from __future__ import annotations

import sys

from .api import main

if __name__ == "__main__":
    sys.exit(main())
