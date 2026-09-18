"""Session Audit over Common Minute Raw and the legacy STRICT minute dataset.

One row per (symbol, session). The audit describes the data; it never edits a bar, never fills
a silent minute and never decides eligibility. Views read it: A STRICT keeps only
``regular_complete`` sessions, B HYBRID-S keeps every session with bars and reads the flags.

Fields:
  premarket_rows / regular_rows / postmarket_rows  bars per part (04:00-20:00 ET, XNYS close)
  expected_regular_rows   390, or 210 on an early close
  regular_complete        regular_rows == expected_regular_rows
  api_loss_suspect        regular minutes missing while grouped daily shows at least
                          ``API_LOSS_TRADES_PER_MINUTE`` trades per regular minute (a silent
                          minute is then improbable); a suspicion, not a verdict
  empty_session           no bar at all in 04:00-20:00
  corporate_action_suspect a split of this symbol executes on this session (splits freeze)
"""

from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import date, datetime, timezone
import gzip
import json
from pathlib import Path

import pyarrow.parquet as pq

from app.backtest.historical_store.raw_fetch import MINUTE_DIR, read_ledger
from app.integrations.massive.minute_bars import ET, SessionPart, classify
from app.market.calendar import MarketCalendar

AUDIT_VERSION = "minute-session-audit-v1"
API_LOSS_TRADES_PER_MINUTE = 10.0


def _parts_from_ms(stamps: Iterable[int], calendar: MarketCalendar) -> dict[date, Counter]:
    out: dict[date, Counter] = {}
    for ms in stamps:
        moment = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(ET)
        window = calendar.session(moment.date())
        part = classify(moment, window).value if window else "NON_SESSION"
        out.setdefault(moment.date(), Counter())[part] += 1
    return out


def common_raw_parts(workspace_root: Path, symbol: str, calendar: MarketCalendar) -> dict[date, Counter]:
    folder = workspace_root / MINUTE_DIR / symbol
    stamps: list[int] = []
    for ledger_path in sorted(folder.glob("*.request.json")) if folder.is_dir() else ():
        ledger = read_ledger(ledger_path)
        if ledger is None or ledger.get("status") != "COMPLETE":
            continue
        for page in ledger["pages"]:
            body = json.loads(gzip.decompress((folder / page["file"]).read_bytes()))
            stamps.extend(bar["t"] for bar in body.get("results") or ())
    return _parts_from_ms(stamps, calendar)


def legacy_parts(workspace_root: Path, relative_dir: str) -> dict[date, Counter]:
    out: dict[date, Counter] = {}
    for path in sorted((workspace_root / relative_dir).glob("*.parquet")):
        table = pq.read_table(path, columns=["trading_date", "session"])
        for day, part in zip(table.column("trading_date").to_pylist(), table.column("session").to_pylist()):
            out.setdefault(day, Counter())[part] += 1
    return out


def audit_rows(symbol: str, source: str, parts: Mapping[date, Counter], sessions: Iterable[date],
               calendar: MarketCalendar, *, grouped_trades: Mapping[date, float],
               split_days: set[date]) -> list[dict]:
    rows = []
    for session in sessions:
        counts = parts.get(session, Counter())
        window = calendar.session(session)
        expected = 210 if window.is_early_close else 390
        regular = counts.get(SessionPart.REGULAR.value, 0)
        trades = grouped_trades.get(session)
        total = sum(counts.values())
        rows.append({
            "symbol": symbol, "session_date": session.isoformat(), "source": source,
            "premarket_rows": counts.get(SessionPart.PREMARKET.value, 0), "regular_rows": regular,
            "postmarket_rows": counts.get(SessionPart.POSTMARKET.value, 0),
            "outside_rows": counts.get(SessionPart.OUTSIDE.value, 0) + counts.get("NON_SESSION", 0),
            "expected_regular_rows": expected, "regular_complete": regular == expected,
            "api_loss_suspect": bool(0 < regular < expected and trades is not None
                                     and trades >= API_LOSS_TRADES_PER_MINUTE * expected),
            "empty_session": total == 0, "grouped_trades": trades,
            "corporate_action_suspect": session in split_days,
            "audit_version": AUDIT_VERSION})
    return rows
