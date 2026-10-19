"""PHASE 1: the H-N1 event table. No forward price, no quantile.

Every price read here is at or before the signal-formation session (reaction window, price and
liquidity filters). The entry session is checked for presence only. Horizon validity is a calendar
check on the session grid. ``FORBIDDEN_COLUMNS`` is asserted against the written table.
"""

from __future__ import annotations

from datetime import date
import gzip
import json
import os

import numpy as np

from app.backtest.strategy_h_n1.common import (
    BENCH, COMPANYFACTS, FILING_W0, FILING_W1, SNAPSHOT_DATE, SUBMISSIONS, HN1Stop, Panel,
    TickerMaps, split_of,
)

REV_TAGS = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax"]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss"]
HORIZONS = (1, 5, 21, 63)
FORBIDDEN_COLUMNS = ("ret", "return", "excess", "exit", "fwd", "forward", "net_", "pnl", "entry_open")


def _d(s: str) -> date:
    return date.fromisoformat(s)


# ---------------------------------------------------------------- SEC inputs

def load_submissions(ciks: set[str]) -> dict[str, dict]:
    """cik -> {"filings": {accn: (form, filingDate, items)}, "oldest": date, "has_more_pages": bool}."""
    out: dict[str, dict] = {}
    for store in SUBMISSIONS:
        for name in os.listdir(store):
            cik = name[3:].lstrip("0")
            if cik not in ciks:
                continue
            p = store / name / f"{name}.json.gz"
            if not p.exists():
                continue
            j = json.loads(gzip.open(p).read())
            r = j.get("filings", {}).get("recent", {})
            e = out.setdefault(cik, {"filings": {}, "oldest": "9999", "has_more_pages": False})
            for a, f, d, it in zip(r.get("accessionNumber", []), r.get("form", []),
                                   r.get("filingDate", []), r.get("items", [])):
                e["filings"].setdefault(a, (f, d, it or ""))
            if r.get("filingDate"):
                e["oldest"] = min(e["oldest"], min(r["filingDate"]))
            e["has_more_pages"] |= bool(j.get("filings", {}).get("files"))
    return out


def _facts(cik: str):
    p = COMPANYFACTS / f"CIK{int(cik):010d}.json.gz"
    if not p.exists():
        return None
    return json.loads(gzip.open(p).read()).get("facts", {}).get("us-gaap", {})


def _usd_rows(g: dict, tag: str) -> list[dict]:
    return [r for r in g.get(tag, {}).get("units", {}).get("USD", []) if "start" in r and "end" in r]


def _dur(r) -> int:
    return (_d(r["end"]) - _d(r["start"])).days


def eval_field(g: dict, tags: list[str], accn: str, filed: str) -> dict:
    """Current and prior-year comparable value for one field, as known at ``filed``."""
    any_current = False
    period_bad = False
    for tag in tags:
        rows = _usd_rows(g, tag)
        cur = [r for r in rows if r.get("accn") == accn and 80 <= _dur(r) <= 100]
        if not cur:
            continue
        any_current = True
        end = max(r["end"] for r in cur)
        c = sorted([r for r in cur if r["end"] == end], key=lambda r: abs(_dur(r) - 91))[0]
        ce, L = _d(c["end"]), _dur(c)
        if not (c["end"] < filed and (_d(filed) - ce).days <= 180):
            period_bad = True
            continue
        pri = [r for r in rows if r.get("filed", "") <= filed
               and abs((ce - _d(r["end"])).days - 365) <= 20 and abs(_dur(r) - L) <= 15]
        if not pri:
            continue
        p = sorted(pri, key=lambda r: (abs((ce - _d(r["end"])).days - 365), _neg(r["filed"]),
                                       r.get("accn") != accn))[0]
        return {"status": "OK", "tag": tag, "current": float(c["val"]), "prior": float(p["val"]),
                "cur_end": c["end"], "prior_end": p["end"], "prior_filed": p["filed"]}
    if period_bad:
        return {"status": "INVALID_PERIOD"}
    return {"status": "PRIOR_MISSING" if any_current else "MISSING"}


def _neg(s: str) -> str:
    # sort key for "latest filed first"
    return "".join(chr(255 - ord(ch)) for ch in s)


def fundamentals(g, accn: str, filed: str, accns: set[str]) -> dict:
    if g is None:
        return classify_f({"fund_status": "XBRL_MISSING"})
    if accn not in accns:
        return classify_f({"fund_status": "INVALID_FILING_MAPPING"})
    rev = eval_field(g, REV_TAGS, accn, filed)
    ni = eval_field(g, NI_TAGS, accn, filed)
    out = {"rev_status": rev["status"], "ni_status": ni["status"],
           "rev_tag": rev.get("tag"), "rev_cur": rev.get("current"), "rev_prior": rev.get("prior"),
           "rev_cur_end": rev.get("cur_end"), "rev_prior_end": rev.get("prior_end"),
           "ni_tag": ni.get("tag"), "ni_cur": ni.get("current"), "ni_prior": ni.get("prior"),
           "ni_cur_end": ni.get("cur_end"), "ni_prior_end": ni.get("prior_end")}
    reason = None
    for name, st in (("REVENUE", rev["status"]), ("NET_INCOME", ni["status"])):
        if st == "OK":
            continue
        reason = reason or {"MISSING": f"{name}_MISSING", "PRIOR_MISSING": f"{name}_PRIOR_MISSING",
                            "INVALID_PERIOD": "INVALID_PERIOD"}[st]
    out["fund_status"] = reason or "VALID"
    return classify_f(out)


def classify_f(row: dict) -> dict:
    """Amendment B: F+ / F- exist only for FUNDAMENTAL_VALID rows."""
    valid = row.get("fund_status") == "VALID"
    row["fundamental_valid"] = valid
    if not valid:
        row["f_plus"] = None
        row["f_minus"] = None
        return row
    fp = (row["rev_prior"] > 0 and row["rev_cur"] > row["rev_prior"] and row["ni_cur"] > row["ni_prior"])
    row["f_plus"], row["f_minus"] = bool(fp), not fp
    return row


# ---------------------------------------------------------------- price side (<= signal session only)

def adv20(panel: Panel, ticker: str, t: int) -> float | None:
    if t < 19:
        return None
    j = panel.tickers.get(ticker)
    if j is None:
        return None
    c = panel.close[t - 19:t + 1, j]
    v = panel.volume[t - 19:t + 1, j]
    if not np.isfinite(c).all():
        return None
    return float(np.median(c * v))


def signal_ticker(panel: Panel, maps: TickerMaps, cik: str, t: int) -> tuple[str | None, bool]:
    """Ticker of ``cik`` at session t from snapshots dated <= that session; (ticker, changed)."""
    base = [x for x in maps.universe.get(cik, []) if panel.present(x, t)]
    changed = False
    if not base:
        base = [x for x in maps.at_or_before(cik, panel.sessions[t]) if panel.present(x, t)]
        changed = bool(base)
    if not base:
        return None, False
    if len(base) > 1:
        base.sort(key=lambda x: (-(adv20(panel, x, t) or -1.0), x))
    return base[0], changed


def reaction(panel: Panel, maps: TickerMaps, cik: str, ticker: str, anchor_date: str, t_sig: int):
    a = panel.last_before(anchor_date)
    if a is None or a >= t_sig:
        return None, a
    start_t = ticker if panel.present(ticker, a) else next(
        (x for x in maps.at_or_before(cik, panel.sessions[t_sig]) if panel.present(x, a)), None)
    if start_t is None:
        return None, a
    f = panel.split_factor(ticker, a, t_sig)
    if start_t != ticker:
        f *= panel.split_factor(start_t, a, t_sig)
    return panel.close_at(ticker, t_sig) * f / panel.close_at(start_t, a) - 1.0, a


def anchor_8k(sub: dict | None, cik_filed: str) -> tuple[str | None, str | None, bool]:
    """Nearest preceding 8-K with item 2.02 within 60 days; (accession, date, coverage_truncated)."""
    if sub is None:
        return None, None, True
    lo = date.fromordinal(_d(cik_filed).toordinal() - 60).isoformat()
    c = [(d, a) for a, (f, d, it) in sub["filings"].items()
         if f == "8-K" and "2.02" in it.split(",") and lo <= d <= cik_filed]
    trunc = sub["has_more_pages"] and sub["oldest"] > lo
    if not c:
        return None, None, trunc
    best = max(d for d, _ in c)
    acc = min(a for d, a in c if d == best)
    return acc, best, trunc


# ---------------------------------------------------------------- table

def build_events(panel: Panel, maps: TickerMaps, subs: dict[str, dict], cik_list: list[str], log=print):
    events: list[dict] = []
    last = len(panel.sessions) - 1
    spy_ok = panel.present(BENCH, 0)
    assert spy_ok
    for n, cik in enumerate(cik_list):
        sub = subs.get(cik)
        filings: dict[str, tuple[str, str]] = {}
        if sub:
            for a, (f, d, _) in sub["filings"].items():
                if f == "10-Q" and FILING_W0 <= d <= FILING_W1:
                    filings[a] = (f, d)
        g = _facts(cik)
        accns: set[str] = set()
        if g is not None:
            for v in g.values():
                for rr in v.get("units", {}).values():
                    for r in rr:
                        accns.add(r.get("accn"))
                        if r.get("form") == "10-Q" and FILING_W0 <= r.get("filed", "") <= FILING_W1:
                            filings.setdefault(r["accn"], ("10-Q", r["filed"]))
        by_day: dict[str, str] = {}
        for a, (_, d) in filings.items():
            if d not in by_day or a < by_day[d]:
                by_day[d] = a
        days = sorted(by_day)
        for k, fd in enumerate(days):
            accn = by_day[fd]
            ev = {"cik": cik, "accession": accn, "filing_date": fd, "form": "10-Q",
                  "next_10q_filing_date": days[k + 1] if k + 1 < len(days) else None,
                  "submissions_present": sub is not None}
            ev["exclusion"] = None
            t_sig = panel.first_after(fd)
            ev["signal_session"] = panel.sessions[t_sig] if t_sig is not None else None
            ev["split"] = split_of(ev["signal_session"]) if t_sig is not None else None
            if t_sig is None:
                ev["exclusion"] = "PIT_NO_SIGNAL_SESSION"
                events.append(ev)
                continue
            if ev["split"] == "PRE_SNAPSHOT":
                ev["exclusion"] = "EXCLUDED_PRE_UNIVERSE_SNAPSHOT"
                events.append(ev)
                continue
            ev["signal_idx"] = t_sig
            ev["entry_session"] = panel.sessions[t_sig + 1] if t_sig + 1 <= last else None
            ev["universe_snapshot"] = SNAPSHOT_DATE
            ev["universe_member"] = cik in maps.universe
            tk, changed = signal_ticker(panel, maps, cik, t_sig) if ev["universe_member"] else (None, False)
            ev["ticker"], ev["ticker_changed_at_signal"] = tk, changed
            ev["cik_continuity"] = "CIK"
            # fundamentals and reaction inputs are recorded for every in-snapshot event
            ev.update(fundamentals(g, accn, fd, accns))
            acc8, d8, trunc = anchor_8k(sub, fd)
            ev["anchor_8k_accession"], ev["anchor_8k_date"] = acc8, d8
            ev["anchor_date"] = d8 or fd
            ev["anchor_is_10q"] = d8 is None
            ev["lag_days"] = (_d(fd) - _d(d8)).days if d8 else None
            ev["submissions_truncated"] = trunc
            if tk is not None:
                ev["close_signal_unadj"] = panel.close_at(tk, t_sig)
                ev["adv20"] = adv20(panel, tk, t_sig)
                r, a = reaction(panel, maps, cik, tk, ev["anchor_date"], t_sig)
                ev["reaction"] = r
                ev["reaction_start_session"] = panel.sessions[a] if a is not None else None
                ev["entry_present"] = bool(t_sig + 1 <= last and panel.present(BENCH, t_sig + 1)
                                           and _entry_present(panel, maps, cik, tk, t_sig + 1))
            else:
                ev["close_signal_unadj"] = ev["adv20"] = ev["reaction"] = None
                ev["entry_present"] = False
            ev["price_ok"] = ev["close_signal_unadj"] is not None and ev["close_signal_unadj"] >= 5.0
            ev["adv_ok"] = ev["adv20"] is not None and ev["adv20"] >= 5_000_000
            ev["reaction_available"] = bool(ev.get("reaction") is not None and np.isfinite(ev["reaction"]))
            for h in HORIZONS:
                ev[f"h{h}_calendar_valid"] = t_sig + h <= last
            ev["exclusion"] = funnel_reason(ev)
            events.append(ev)
        if n % 500 == 0:
            log(f"  {n}/{len(cik_list)} CIKs, {len(events)} events")
    for ev in events:
        bad = [k for k in ev if any(k.startswith(f) or f in k for f in FORBIDDEN_COLUMNS)]
        if bad:
            raise HN1Stop(f"forward-looking column in Phase 1 table: {bad}")
    return events


def _entry_present(panel, maps, cik, tk, t) -> bool:
    if panel.present(tk, t):
        return True
    return any(panel.present(x, t) for x in maps.any_snapshot(cik) if x != tk)


def funnel_reason(ev: dict) -> str | None:
    """First failing funnel step before the reaction bucket (contract funnel order)."""
    if not ev["universe_member"]:
        return "NOT_IN_UNIVERSE_SNAPSHOT"
    if ev["ticker"] is None:
        return "TICKER_UNRESOLVED_AT_SIGNAL"
    if not ev["price_ok"]:
        return "PRICE_LT_5"
    if not ev["adv_ok"]:
        return "ADV_LT_5M_OR_LT_20_SESSIONS"
    if not ev["fundamental_valid"]:
        return "FUND_" + ev["fund_status"]
    if not ev["reaction_available"]:
        return "REACTION_UNAVAILABLE"
    return None
