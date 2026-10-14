"""`python -m app.crypto.context_collector_v1 collect|serve|prune|retain ...`"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ("collect", "serve", "prune", "retain"):
        print("usage: python -m app.crypto.context_collector_v1 collect|serve|prune|retain [options]",
              file=sys.stderr)
        return 2
    command, rest = args[0], args[1:]
    if command == "collect":
        from .collector import main as collect
        return collect(rest)
    if command == "prune":
        from .retention import main as prune
        return prune(rest)
    if command == "retain":
        from .retention import retain_main
        return retain_main(rest)
    from .api import main as serve
    return serve(rest)


if __name__ == "__main__":
    sys.exit(main())
