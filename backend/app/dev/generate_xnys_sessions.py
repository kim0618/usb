"""Generate the static XNYS session table that ``app.market.calendar`` reads at runtime.

Offline only: this is the one place ``exchange_calendars`` (and through it pandas) is imported for
the market calendar, so the backend process never loads either. Every row is copied from the
library's own schedule; no holiday or early-close rule is written here.

    PYTHONPATH=backend .venv/bin/python -m app.dev.generate_xnys_sessions

Rows: ``date,open_utc,close_utc,early_close`` with epoch seconds. ``early_close`` uses the rule the
facade has always applied (New York close before 16:00). The sidecar ``.meta.json`` records the
range, row count, library version and the table's sha256; the runtime checks each of them against the
rows on load and refuses to start on a mismatch.
Regenerate when ``exchange_calendars`` is upgraded or the range has to move, then run
``tests/test_market_calendar_table.py`` in an environment that has the library installed.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, time, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

GENERATOR_VERSION = "xnys-sessions-v1"
RANGE_START = date(2000, 1, 1)
RANGE_END = date(2035, 12, 31)
DATA_DIR = Path(__file__).resolve().parents[1] / "market" / "data"
NY = ZoneInfo("America/New_York")


def build_rows(start: date = RANGE_START, end: date = RANGE_END) -> list[tuple[str, int, int, int]]:
    import exchange_calendars as xcals

    schedule = xcals.get_calendar("XNYS", start=start.isoformat(), end=end.isoformat()).schedule
    rows = []
    for label, opened, closed in zip(schedule.index, schedule["open"], schedule["close"]):
        close_ny = closed.to_pydatetime().astimezone(NY)
        rows.append((label.date().isoformat(), int(opened.timestamp()), int(closed.timestamp()),
                     int(close_ny.timetz().replace(tzinfo=None) < time(16, 0))))
    return rows


def render(rows: list[tuple[str, int, int, int]]) -> bytes:
    lines = ["date,open_utc,close_utc,early_close"] + ["%s,%d,%d,%d" % row for row in rows]
    return ("\n".join(lines) + "\n").encode("ascii")


def main() -> None:
    import exchange_calendars as xcals

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args()
    rows = build_rows()
    body = render(rows)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "xnys_sessions.csv").write_bytes(body)
    meta = {"generator_version": GENERATOR_VERSION, "calendar": "XNYS",
            "exchange_calendars_version": xcals.__version__,
            "range_start": RANGE_START.isoformat(), "range_end": RANGE_END.isoformat(),
            "first_session": rows[0][0], "last_session": rows[-1][0], "row_count": len(rows),
            "sha256": hashlib.sha256(body).hexdigest(),
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat()}
    (args.out_dir / "xnys_sessions.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
