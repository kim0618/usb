"""Strategy I paper events: the frozen V2.1 ``simulate21`` run on a point-in-time view.

Signals are the last XNYS session of each calendar month from the launch signal on (the frozen rule is
"last stored session of the month"; with a calendar-complete store the two are the same session, and
the calendar form does not need to know whether another session will be stored this month). The
execution is the next XNYS session; until its grouped file exists the signal is pending and no trade
is booked. ``simulate21`` itself is called unchanged, with only its module-level schedule replaced
(``signals``, ``execs``) and the V2 continuity tables emptied (``TRANS``, ``AMBIG``; see the package
docstring). Its outputs are turned into canonical, rounded event dicts so a later run can verify the
stored events as an exact prefix.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date
from typing import Any

from app.market.calendar import MarketCalendar

from . import overlay as OV

FACTOR = "MOM12_2"
BP = 40
TYPE_ORDER = {"SIGNAL": 0, "CONTINUITY_LINKED": 1, "PRICE_STOP_HELD": 2, "CONTINUITY_UNRESOLVED": 3, "TRADE": 4, "EXEC": 5,
              "OVERLAY_OBS": 6, "NAV": 7}


def r6(x: float) -> float:
    return round(float(x), 6)


def month_end_signals(cal: MarketCalendar, launch: date, as_of: date) -> list[date]:
    out = []
    d = launch
    while d <= as_of:
        nxt = cal.next_trading_day(d)
        if nxt.month != d.month and cal.is_trading_day(d):
            out.append(d)
        d = nxt
    return out


def schedule(cal: MarketCalendar, sessions: list[str], launch: date, as_of: date) -> tuple[list[str], list[str], list[str], list[str]]:
    """(signals with an available execution, their executions, pending signals, refused signals)"""
    pos = {s: i for i, s in enumerate(sessions)}
    sigs, exs, pending, refused = [], [], [], []
    for s in month_end_signals(cal, launch, as_of):
        si = s.isoformat()
        if si not in pos or pos[si] < 252:
            refused.append(si); continue
        e = cal.next_trading_day(s).isoformat()
        if e in pos: sigs.append(si); exs.append(e)
        else: pending.append(si)
    return sigs, exs, pending, refused


def compute(m21: Any, cal: MarketCalendar, launch: date, as_of: date, sic_store=None,
            _research_keep_v2_continuity: bool = False, registry: dict[str, dict[str, str]] | None = None,
            ranking_files=None, evidence_files=None) -> dict[str, Any]:
    """``registry`` (name -> first_seen) makes snapshot availability point-in-time (``continuity``). Without
    it every snapshot is taken as available on its own as-of date (historical on-time assumption; tests
    only). ``_research_keep_v2_continuity`` keeps the frozen V2 table: regression test only."""
    from pathlib import Path
    from . import continuity as CT
    S = list(m21.S)
    sigs, exs, pending, refused = schedule(cal, S, launch, as_of)
    events: list[dict[str, Any]] = []
    m1 = m21.m1
    g = m21.__dict__
    if "_v2_trans_original" not in g:
        g["_v2_trans_original"] = dict(g["TRANS"])
    reg = registry or {}
    def avail(p):
        return reg.get(Path(p).name, {}).get("first_seen", "0000-00-00")
    rk_files = list(ranking_files) if ranking_files is not None else []
    ev_files = list(evidence_files) if evidence_files is not None else []
    rk = [CT.read_snapshot(Path(p), avail(p)) for p in rk_files]
    ev = [CT.read_snapshot(Path(p), avail(p)) for p in ev_files]
    # V1 ranking restricted to snapshots available by the signal day (rule itself unchanged)
    full_dates = list(m1.snap_dates)
    ok_sigs, ok_exs = [], []
    for s_, e_ in list(zip(sigs, exs)) + [(p, None) for p in pending]:
        if rk:
            dates = CT.ranking_snap_dates(rk, s_)
            if not dates:
                refused.append(s_); continue
            m1.__dict__["snap_dates"] = dates
        m1.RANK_CACHE.pop((s_, FACTOR), None)
        if rk:
            try:
                m1.get_rank(s_, FACTOR)
            finally:
                m1.__dict__["snap_dates"] = full_dates
        if e_ is not None: ok_sigs.append(s_); ok_exs.append(e_)
    pending = [p for p in pending if p not in refused]
    sigs, exs = ok_sigs, ok_exs
    for k, s in enumerate(sigs + pending):
        R = m1.get_rank(s, FACTOR); n = len(R["order"])
        events.append({"type": "SIGNAL", "session": s, "k": k, "signal": s, "exec": exs[k] if k < len(exs) else None,
                       "eligible": R["eligible"], "ranked": n, "ez": max(1, int(0.10 * n)), "hz": max(1, int(0.20 * n)),
                       "order_sha256": hashlib.sha256("\n".join(R["order"]).encode()).hexdigest(),
                       "snapshot_as_of": max(d for d in (CT.ranking_snap_dates(rk, s) if rk else m1.snap_dates) if d <= s)})
    if _research_keep_v2_continuity:
        trans, cont = dict(g["_v2_trans_original"]), {}
    else:
        trans, cont = CT.build(m21.C, S, rk + ev)
    # SIGNAL rows of pending signals carry exec None; they are re-emitted with exec once it exists, so
    # they are kept out of the stored (verified) stream.
    stable = [e for e in events if e["exec"] is not None]
    result = {"pending": pending, "refused": refused, "signals": sigs, "execs": exs, "continuity_contract": CT.CONTRACT_ID}
    if not sigs:
        result["events"] = stable; return result
    g["signals"], g["execs"] = list(sigs), list(exs)
    g["TRANS"], g["AMBIG"] = trans, {}
    run = m21.simulate21(FACTOR, BP)
    C, pos = m21.C, m21.pos
    ex_k = {e: k for k, e in enumerate(exs)}
    # trades -> events; holdings replay (shares exactly as V2.1 rounds them in its log)
    hold: dict[str, float] = {}
    cash = float(m21.CAP)
    by_exec: dict[str, list] = {}
    for seq, (e, side, tk, sh, nt, fee) in enumerate(run["trades"]):
        by_exec.setdefault(e, []).append((seq, side, tk, sh, nt, fee))
    recs = {r["exec"]: r for r in run["recs"]}
    nav = run["nav"]
    stale_seen: set[tuple[str, str]] = set()
    linked: list[str] = []; unresolved: list[str] = []
    first_exec_i = pos[exs[0]]
    prev_list: list[str] = []
    for i in range(first_exec_i, len(S)):
        s = S[i]
        for ev in run["events"]:     # transitions the frozen simulate21 applied (PIT table, or V2 table in research mode)
            if ev["date"] == s and ev["old"] in hold:
                hold[ev["new"]] = hold.pop(ev["old"])
                d = cont.get(ev["old"], {})
                events.append({"type": "CONTINUITY_LINKED", "session": s, "ticker": ev["old"], "new": ev["new"], "last_close_session": d.get("lo"),
                               "cik": d.get("cik"), "evidence": d.get("evidence"), "evidence_available": d.get("evidence_avail"),
                               "contract": CT.CONTRACT_ID if not _research_keep_v2_continuity else "V2_RESEARCH_REGRESSION_ONLY"})
                linked.append(ev["old"])
        for tk in hold:   # V2.1 share basis: eff = shares * adj[i] / adj0 (ratio is exactly 1 without a split)
            hold[tk] = hold[tk] * float(m21.adj[tk].iloc[i]) / float(m21.adj[tk].iloc[i - 1])
        # continuity: a held name with no close today after having one before (PIT: known today)
        for tk in sorted(hold):
            px = C[tk].iloc[i]
            if math.isnan(px):
                lv = C[tk].iloc[:i].last_valid_index()
                if lv is not None and (tk, lv) not in stale_seen:
                    stale_seen.add((tk, lv))
                    events.append({"type": "PRICE_STOP_HELD", "session": s, "ticker": tk, "last_close_session": lv})
        if s in by_exec:
            k = ex_k[s]
            for seq, side, tk, sh, nt, fee in by_exec[s]:
                stale = side == "SELL" and math.isnan(C[tk].iloc[i])
                if stale and not _research_keep_v2_continuity:
                    d = cont.get(tk, {})
                    # reason as known at s: evidence that became available after s cannot appear in this event
                    why = d.get("reason", "NOT_EVALUATED")
                    if d.get("status") == "LINKED" or (why == "AMBIGUOUS" and d.get("evidence_avail", "9999") > s) or why == "EVIDENCE_AFTER_AS_OF":
                        why = "NO_PIT_EVIDENCE"
                    events.append({"type": "CONTINUITY_UNRESOLVED", "session": s, "ticker": tk, "last_close_session": d.get("lo"),
                                   "reason": why, "cik": d.get("cik"),
                                   "treatment": "V1 price-stop rule: valued at last close, sold at it (frozen V2.1 code, no PIT link)"})
                    unresolved.append(tk)
                events.append({"type": "TRADE", "session": s, "k": k, "seq": seq, "side": side, "ticker": tk, "shares": r6(sh),
                               "notional": r6(nt), "cost": r6(fee), "stale_last_close": stale})
                if side == "SELL":
                    cash += nt - fee; hold[tk] = hold.get(tk, 0.0) - sh
                    if abs(hold[tk]) < 1e-6: del hold[tk]
                else:
                    cash -= nt + fee; hold[tk] = hold.get(tk, 0.0) + sh
            r = recs[s]
            if sorted(hold) != r["holdings_list"]:
                raise RuntimeError("holding replay differs from V2.1 holdings_list at %s" % s)
            events.append({"type": "EXEC", "session": s, "k": k, "signal": sigs[k], "holdings": r["holdings"], "holdings_list": r["holdings_list"],
                           "trade_count": r["trade_count"], "cash_pct": r["cash_pct"], "nav_post": r["nav_post"], "costs": r["costs"],
                           "turnover_one_way": None if r["turnover_one_way"] is None else r6(r["turnover_one_way"]),
                           "C1_violations": r["C1_violations"], "C2_overshoot": r["C2_overshoot"], "cash_from_logged_trades": r6(cash)})
            entries = sorted({tk for _, side, tk, *_ in by_exec[s] if side.startswith("BUY")} - set(prev_list))
            events.append(OV.observe(m21, k, sigs[k], s, hold, cash, sic_store, entries))
            prev_list = list(r["holdings_list"])
        if s in nav.index:
            events.append({"type": "NAV", "session": s, "nav": r6(nav.loc[s])})
    events.sort(key=lambda e: (e["session"], TYPE_ORDER[e["type"]], e.get("seq", 0)))
    result["events"] = [e for e in events if not (e["type"] == "SIGNAL" and e["exec"] is None)]
    result["flags"] = dict(run["flags"])
    # recorded differences to the frozen V2.1 continuity (kept out of the verified event stream: the V2 table
    # depends on every snapshot visible to this run, so it is a per-run record, not a dated event)
    v2 = g["_v2_trans_original"]
    result["v21_continuity_differences"] = [
        {"ticker": tk, "paper": "UNRESOLVED_V1_PRICE_STOP", "reason": cont.get(tk, {}).get("reason"),
         "v21_link": (list(v2[tk][:3]) if tk in v2 else None)} for tk in unresolved if tk in v2] + [
        {"ticker": tk, "paper": "LINKED", "paper_link": [cont[tk]["new"], cont[tk]["a"]], "v21_link": list(v2[tk][:3]) if tk in v2 else None}
        for tk in linked if tk in cont and (tk not in v2 or list(v2[tk][:3]) != [cont[tk]["new"], cont[tk]["lo"], cont[tk]["a"]])]
    result["unresolved"] = unresolved; result["linked"] = linked
    # latest marks for the read model (state.json, not an event): book as of the last stored session
    li = len(S) - 1
    marks = []
    for tk in sorted(hold):
        px = C[tk].iloc[li]; stale = math.isnan(px)
        if stale:
            lv = C[tk].iloc[:li].last_valid_index(); px = C[tk].loc[lv] if lv is not None else float("nan")
        marks.append({"ticker": tk, "shares": r6(hold[tk]), "close": r6(px), "value": r6(hold[tk] * px), "stale": stale})
    result["latest"] = {"session": S[li], "cash_from_logged_trades": r6(cash), "nav": r6(nav.iloc[-1]) if len(nav) else None,
                        "positions": marks}
    return result
