"""Read-only Massive requests for the C-M selection study, cached one file per request.

Three request kinds, all Stocks Basic:

* grouped daily (``adjusted=false``, OTC excluded by the endpoint default) for every XNYS
  session in the range, saved as the provider's own response bytes (gzip);
* reference tickers ``type=CS`` as of named snapshot dates (``active=true`` as of that date,
  so a ticker delisted later is still present);
* market-wide split records executed in the range (``/v3/reference/splits`` without a ticker).

A file that exists is never requested again, so an interrupted fetch resumes at zero cost.
Requests are strictly serial on the client's 5-calls-per-minute limiter. A request that stays
rate limited after the client's own retries waits ``RATE_LIMIT_COOLDOWN_SECONDS`` and is tried
again, because another local process may share the key. Nothing here writes to a workspace or
a manifest.
"""

from collections.abc import Callable, Sequence
from datetime import date
import gzip
import json
from pathlib import Path
import time
from typing import Any

from app.backtest.workspace.guards import assert_no_secret_like
from app.integrations.massive.client import SPLITS_PATH, MassiveAggregatesClient, MassiveError

RAW_FORMAT_VERSION = "strategy-c-selection-raw-v1"
SPLITS_PAGE_LIMIT = 1_000
SPLITS_MAX_PAGES = 12
RATE_LIMIT_COOLDOWN_SECONDS = 120.0
RATE_LIMIT_MAX_COOLDOWNS = 5
#: Reference fields the universe rule reads. ``name`` is not kept (free text, never a rule input).
TICKER_FIELDS = ("ticker", "type", "market", "locale", "primary_exchange", "active", "cik",
                 "composite_figi", "share_class_figi", "delisted_utc", "last_updated_utc")


def grouped_path(root: Path, session: date) -> Path:
    return root / "grouped" / f"{session.isoformat()}.json.gz"


def tickers_path(root: Path, as_of: date) -> Path:
    return root / "tickers" / f"CS_{as_of.isoformat()}.json.gz"


def splits_path(root: Path, start: date, end: date) -> Path:
    return root / "splits" / f"splits_{start.isoformat()}_{end.isoformat()}.json.gz"


def _write_json_gz(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    with gzip.open(partial, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    partial.replace(path)


def _with_cooldown(call: Callable[[], Any], *, log: Callable[[str], None],
                   sleeper: Callable[[float], None]) -> Any:
    for attempt in range(RATE_LIMIT_MAX_COOLDOWNS + 1):
        try:
            return call()
        except MassiveError as error:
            if getattr(error, "code", None) != "RATE_LIMITED" or attempt == RATE_LIMIT_MAX_COOLDOWNS:
                raise
            log(f"rate limited after client retries; cooldown {RATE_LIMIT_COOLDOWN_SECONDS:.0f}s")
            sleeper(RATE_LIMIT_COOLDOWN_SECONDS)
    raise AssertionError("bounded cooldown loop exhausted")


def fetch_grouped(client: MassiveAggregatesClient, root: Path, session: date, *,
                  log: Callable[[str], None] = print,
                  sleeper: Callable[[float], None] = time.sleep) -> str:
    path = grouped_path(root, session)
    if path.exists():
        return "cached"
    try:
        body = _with_cooldown(lambda: client.grouped_daily(session), log=log, sleeper=sleeper)
    except MassiveError as error:
        code = getattr(error, "code", "MASSIVE_ERROR")
        if code in {"PLAN_TIMEFRAME_NOT_INCLUDED", "NOT_AUTHORIZED"}:
            _write_json_gz(path, {"format": RAW_FORMAT_VERSION, "session": session.isoformat(),
                                  "error": code})
            return code
        raise
    if body.get("adjusted") is not False:
        raise MassiveError("MALFORMED_PAYLOAD", f"grouped {session} did not echo adjusted=false")
    assert_no_secret_like(body, where=f"grouped {session}")
    _write_json_gz(path, {"format": RAW_FORMAT_VERSION, "session": session.isoformat(), "body": body})
    return f"ok rows={body.get('resultsCount')}"


def fetch_tickers(client: MassiveAggregatesClient, root: Path, as_of: date, *,
                  log: Callable[[str], None] = print,
                  sleeper: Callable[[float], None] = time.sleep) -> str:
    path = tickers_path(root, as_of)
    if path.exists():
        return "cached"
    pages = _with_cooldown(lambda: client.reference_tickers(as_of, security_type="CS"),
                           log=log, sleeper=sleeper)
    rows = [{name: item.get(name) for name in TICKER_FIELDS}
            for page in pages for item in (page.get("results") or ())]
    for row in rows:
        assert_no_secret_like(row, where=f"tickers {as_of}")
    _write_json_gz(path, {"format": RAW_FORMAT_VERSION, "as_of": as_of.isoformat(),
                          "security_type": "CS", "pages": len(pages), "results": rows})
    return f"ok pages={len(pages)} rows={len(rows)}"


def fetch_splits(client: MassiveAggregatesClient, root: Path, start: date, end: date, *,
                 log: Callable[[str], None] = print,
                 sleeper: Callable[[float], None] = time.sleep) -> str:
    """Market-wide splits. Uses the client's guarded ``_get`` and same-path ``next_url`` check;
    the public ``reference_splits`` is per ticker and one page only."""
    path = splits_path(root, start, end)
    if path.exists():
        return "cached"
    url = f"{client._base_url}{SPLITS_PATH}"  # noqa: SLF001 - reuse the client's guarded transport
    params: dict[str, str] | None = {
        "execution_date.gte": start.isoformat(), "execution_date.lte": end.isoformat(),
        "limit": str(SPLITS_PAGE_LIMIT), "sort": "execution_date", "order": "asc"}
    rows: list[dict[str, Any]] = []
    seen = {url}
    for page in range(1, SPLITS_MAX_PAGES + 1):
        body = _with_cooldown(lambda url=url, params=params: client._get(url, params),  # noqa: SLF001
                              log=log, sleeper=sleeper)
        rows.extend(dict(item) for item in (body.get("results") or ()))
        next_url = body.get("next_url")
        if not next_url:
            for row in rows:
                assert_no_secret_like(row, where="splits")
            _write_json_gz(path, {"format": RAW_FORMAT_VERSION, "start": start.isoformat(),
                                  "end": end.isoformat(), "pages": page, "results": rows})
            return f"ok pages={page} rows={len(rows)}"
        url = client._trusted_reference_url(next_url, SPLITS_PATH)  # noqa: SLF001
        params = None
        if url in seen:
            raise MassiveError("PAGINATION_LOOP", "splits next_url repeats")
        seen.add(url)
    raise MassiveError("PAGINATION_LIMIT", f"splits next_url remained after {SPLITS_MAX_PAGES} pages")


def fetch_plan(client: MassiveAggregatesClient, root: Path, *, sessions: Sequence[date],
               ticker_dates: Sequence[date], split_range: tuple[date, date],
               log: Callable[[str], None] = print) -> None:
    """Oldest grouped sessions first (they are the next to leave the rolling Basic window),
    then reference snapshots and splits."""
    log(f"splits {split_range[0]}..{split_range[1]}: {fetch_splits(client, root, *split_range, log=log)}")
    for as_of in ticker_dates:
        log(f"tickers CS {as_of}: {fetch_tickers(client, root, as_of, log=log)} "
            f"http={client.accounting.http_requests}")
    for session in sorted(sessions):
        before = client.accounting.http_requests
        started = time.perf_counter()
        outcome = fetch_grouped(client, root, session, log=log)
        if outcome != "cached":
            log(f"grouped {session}: {outcome} http={client.accounting.http_requests - before} "
                f"wall_s={time.perf_counter() - started:.1f} statuses={dict(client.accounting.status_codes)}")
