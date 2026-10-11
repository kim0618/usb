"""One run of the recorder (a oneshot, started once a minute by a timer in production).

    python -m app.crypto.manual_snapshot_v1.recorder --store DIR --s1 FILE --s2 FILE --journal DIR
        [--trigger-url URL] [--now-ms MS] [--max-snapshots N]

Each run re-derives every event and episode from the whole S1 file and appends only rows whose key
is not in the store yet (contract section 6), so restarts, replays and a lost cursor never
duplicate anything.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Callable

from . import episodes as EP
from .collector import Journal, collector_fields
from .features import Window
from .market import BudgetExhausted, Market, VenueBackoff
from .sources import S2, read_s1, read_s2
from .store import Store

CONTRACT = "manual-snapshot.v1"
FOLLOWUP_MIN = (1, 5, 15, 30, 60)
HOLD_STEP_MS = 300_000
READY_LAG_MS = 120_000          # a time is "past" once its 1m bar has closed and been served
TRIGGER_EVERY_MS = 300_000


_IDENTITY = ("order_id", "event_type", "origin", "direction", "qty", "vwap", "qty_before",
             "qty_after", "realized_pnl", "commission", "fills")


def _conflicts(store: Store, events: list[EP.OrderEvent]) -> dict:
    """Recorded events that S1 no longer yields, or yields with different figures."""
    stored = {str(r["event_id"]): r for r in store.rows("events")}
    now = {str(e.event_id): e.row() for e in events}
    missing = sorted(k for k in stored if k not in now)
    changed = sorted(k for k in stored if k in now
                     and any(str(stored[k].get(f)) != str(now[k].get(f)) for f in _IDENTITY))
    return {"missing": missing, "changed": changed}


def _intent_and_leverage(ev: EP.OrderEvent, s2: S2) -> dict:
    sent = s2.sent_ms.get(ev.client_order_id or "")
    intents = [t for t, _, _ in s2.intents if sent is not None and t <= sent]
    lev = [v for t, v in s2.leverage if t <= ev.first_fill_ms]
    return {"sent_ms": sent, "intent_ms": intents[-1] if intents else None,
            "leverage": lev[-1] if lev else None}


def _position(events: list[EP.OrderEvent], at_ms: int) -> tuple:
    last = None
    for e in events:
        if e.last_fill_ms <= at_ms:
            last = e
        else:
            break
    if last is None:
        return 0.0, None, None
    q = float(last.qty_after)
    entry = float(last.vwap_entry_after) if last.vwap_entry_after is not None else None
    return q, entry, last


def run_once(*, store_root: Path, s1_path: Path, s2_path: Path, journal_root: Path | None,
             market: Market, now_ms: int, max_snapshots: int = 5_000,
             trigger: Callable[[], None] | None = None) -> dict:
    store = Store(store_root)
    store.lock()
    try:
        store.load()
        cursor_path = store_root / "cursor.json"
        cursor = json.loads(cursor_path.read_text()) if cursor_path.exists() else {}
        first_run_ms = int(cursor.get("first_run_ms") or now_ms)
        triggered = None
        if trigger is not None and now_ms - int(cursor.get("last_trigger_ms") or 0) >= TRIGGER_EVERY_MS:
            try:
                trigger()
                triggered = "OK"
            except Exception as exc:          # the terminal being down is not the recorder's error
                triggered = f"FAILED:{type(exc).__name__}"
            cursor["last_trigger_ms"] = now_ms
        s1 = read_s1(s1_path)
        s2 = read_s2(s2_path, index_path=store_root / "runtime" / "s2_index.json")
        events, episodes = EP.derive(s1, s2)
        written = {"events": 0, "snapshots": 0, "episodes": 0}
        # S1 must still contain everything already recorded, unchanged. If it lost or changed a
        # fill, a closed episode can re-derive as open and every row from here on would describe
        # a position that does not exist: write nothing until S1 agrees with the store again.
        conflicts = _conflicts(store, events)
        if conflicts["missing"] or conflicts["changed"]:
            cursor.update(contract=CONTRACT, first_run_ms=first_run_ms, last_run_ms=now_ms,
                          status="HALTED_S1_DISAGREES_WITH_STORE", written=written,
                          anomalies={"events_no_longer_in_s1": conflicts["missing"],
                                     "events_changed_in_s1": conflicts["changed"]},
                          s1_fills=len(s1.fills), s1_error=s1.error, trigger=triggered)
            store.write_cursor(cursor)
            return cursor
        for ev in events:
            row = {**ev.row(), **_intent_and_leverage(ev, s2), "contract": CONTRACT,
                   "backfilled": ev.last_fill_ms < first_run_ms, "recorded_at_ms": now_ms}
            written["events"] += store.append("events", row)

        # ---- snapshot plan: (snapshot_id, kind, as_of, extra) ------------------------------
        plan: list[tuple] = []
        ready = now_ms - READY_LAG_MS
        by_id = {e.event_id: e for e in events}
        for ev in events:
            sid = f"ev:{ev.event_id}"
            if not store.has("snapshots", sid) and ev.last_fill_ms <= ready:
                plan.append((sid, "EVENT", ev.last_fill_ms, ev.event_id))
            for m in FOLLOWUP_MIN:
                t = ev.last_fill_ms + m * 60_000
                sid = f"fu:{ev.event_id}:{m}"
                if t <= ready and not store.has("snapshots", sid):
                    plan.append((sid, "FOLLOWUP", t, ev.event_id))
        for ep in episodes:
            start = ep.events[0].first_fill_ms
            end = ep.events[-1].last_fill_ms if ep.closed else now_ms
            s = (start // HOLD_STEP_MS + 1) * HOLD_STEP_MS
            while s <= min(end, ready):
                sid = f"hold:{ep.episode_id}:{s}"
                if not store.has("snapshots", sid):
                    plan.append((sid, "HOLD", s, ep.episode_id))
                s += HOLD_STEP_MS
        plan.sort(key=lambda x: (x[2], x[0]))
        planned_total = len(plan)
        plan = plan[:max_snapshots]

        backoff_until = int(cursor.get("backoff_until_ms") or 0)
        market_status = "OK"
        if now_ms < backoff_until:
            market_status = "BACKOFF_ACTIVE"
            plan = []
        try:
            _write_snapshots(plan, market, journal_root, episodes, events, by_id, store, written,
                             first_run_ms, now_ms)
            _write_episodes(episodes, market, store, written, s1, ready, first_run_ms, now_ms,
                            skip=market_status != "OK")
        except BudgetExhausted:
            market_status = "DEFERRED_REQUEST_BUDGET"
        except VenueBackoff as exc:
            market_status = f"VENUE_BACKOFF_{exc.status}"
            cursor["backoff_until_ms"] = now_ms + exc.retry_after_s * 1000

        deferred = planned_total - written["snapshots"]
        cursor.update(status="OK", market_status=market_status,
                      market_requests=getattr(market, "requests", None), contract=CONTRACT,
                      first_run_ms=first_run_ms, last_run_ms=now_ms,
                      s1_fills=len(s1.fills), s1_last_calculated_ms=s1.last_calculated_ms,
                      s1_error=s1.error, s1_classification_complete=s1.classification_complete,
                      s2_lines=s2.lines, s2_first_ms=s2.first_ms, events=len(events),
                      episodes=len(episodes), open_episode=next(
                          (ep.episode_id for ep in episodes if not ep.closed), None),
                      written=written, deferred_snapshots=deferred, trigger=triggered,
                      anomalies={"events_no_longer_in_s1": [], "events_changed_in_s1": []},
                      waiting_for_s1_classification=EP.unclassified_orders(s1),
                      repaired_torn_bytes=store.repaired)
        store.write_cursor(cursor)
        return cursor
    finally:
        store.unlock()


def _write_snapshots(plan, market, journal_root, episodes, events, by_id, store, written,
                     first_run_ms, now_ms) -> None:
    if not plan:
        return
    ep_open = {ep.episode_id: ep.events[0].first_fill_ms for ep in episodes}
    path_from = min(ep_open[ref] if kind == "HOLD" else by_id[ref].last_fill_ms
                    for _, kind, _, ref in plan)
    window = Window(market, min(p[2] for p in plan), max(p[2] for p in plan), path_from_ms=path_from)
    rows = Journal(journal_root).rows_at([p[2] for p in plan])
    for sid, kind, as_of, ref in plan:
        snap = _snapshot(sid, kind, as_of, ref, by_id, episodes, events, window, rows.get(as_of),
                         first_run_ms, now_ms)
        written["snapshots"] += store.append("snapshots", snap)


def _write_episodes(episodes, market, store, written, s1, ready, first_run_ms, now_ms, *,
                    skip: bool) -> None:
    closed = [ep for ep in episodes if ep.closed and not store.has("episodes", ep.episode_id)
              and ep.events[-1].last_fill_ms <= ready]
    if not closed or skip:
        return
    first_open = min(ep.events[0].first_fill_ms for ep in closed)
    window = Window(market, first_open, max(ep.events[-1].last_fill_ms for ep in closed),
                    path_from_ms=first_open)
    for ep in closed:
        summary = EP.episode_summary(ep, s1.funding, None)
        ref = float(summary["vwap_entry"]) if summary["vwap_entry"] else None
        path = window.path(ep.events[0].first_fill_ms, ep.events[-1].last_fill_ms, ref,
                           ep.direction) if ref else None
        summary = EP.episode_summary(ep, s1.funding, path)
        summary.update(contract=CONTRACT, recorded_at_ms=now_ms,
                       backfilled=ep.events[-1].last_fill_ms < first_run_ms)
        written["episodes"] += store.append("episodes", summary)


def _snapshot(sid: str, kind: str, as_of: int, ref: str, by_id: dict, episodes: list,
              events: list, window: Window, row: dict | None, first_run_ms: int, now_ms: int) -> dict:
    market, mstatus, mreason = window.market_fields(as_of)
    cfields, cstatus, creason = collector_fields(row, as_of)
    tfs = window.timeframes(as_of, with_15s=(kind == "EVENT"))
    qty, entry, _last = _position(events, as_of)
    price = market.get("last_1m_close")
    out = {"snapshot_id": sid, "kind": kind, "as_of_ms": as_of, "recorded_at_ms": now_ms,
           "backfilled": as_of < first_run_ms, "contract": CONTRACT,
           "market": market, "timeframes": tfs, "collector": cfields,
           "position": {"qty_signed": qty, "vwap_entry": entry,
                        "unrealized_usdt": (round((price - entry) * qty, 6)
                                            if price is not None and entry is not None else None)},
           "liquidation": {"status": "NOT_COLLECTED", "reason": "NO_AS_OF_READER_IN_V1"},
           "c1": {"status": "NOT_COLLECTED", "reason": "NO_HISTORY_SOURCE"},
           "layer_status": {
               "market": {"status": mstatus, "reason": mreason},
               "collector": {"status": cstatus, "reason": creason},
               "timeframes": {tf: v.get("status") for tf, v in tfs.items()},
               "position": {"status": "OK"}},
           "source_sessions": {"collector": cfields.get("session_id")}}
    if kind in ("EVENT", "FOLLOWUP"):
        ev = by_id[ref]
        out.update(event_id=ev.event_id, episode_id=ev.episode_id, event_type=ev.event_type,
                   origin=ev.origin, direction=ev.direction)
        if kind == "FOLLOWUP":
            minutes = int(sid.rsplit(":", 1)[1])
            d = 1 if ev.direction == "LONG" else -1
            v = float(ev.vwap)
            out.update(minutes_after=minutes,
                       ret_bp=(round(d * (price - v) / v * 1e4, 4) if price is not None else None),
                       **{f"path_{k}": x for k, x in window.path(ev.last_fill_ms, as_of, v,
                                                                 ev.direction).items()})
    else:
        ep = next(e for e in episodes if e.episode_id == ref)
        first = ep.events[0]
        out.update(episode_id=ep.episode_id, direction=ep.direction,
                   position_age_ms=as_of - first.first_fill_ms)
        if entry:
            out.update(**{f"path_{k}": x for k, x in window.path(first.first_fill_ms, as_of, entry,
                                                                 ep.direction).items()})
    return out


def _trigger(url: str):
    def call():
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    return call


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", type=Path, required=True)
    ap.add_argument("--s1", type=Path, required=True)
    ap.add_argument("--s2", type=Path, required=True)
    ap.add_argument("--journal", type=Path, default=None)
    ap.add_argument("--trigger-url", default="")
    ap.add_argument("--now-ms", type=int, default=None)
    ap.add_argument("--max-snapshots", type=int, default=5_000)
    ap.add_argument("--request-budget", type=int, default=12,
                    help="public REST requests per run (shared IP with the terminal)")
    ap.add_argument("--request-spacing-s", type=float, default=0.25)
    a = ap.parse_args(argv)
    now = a.now_ms if a.now_ms is not None else int(time.time() * 1000)
    market = Market(a.store / "runtime" / "market_cache", now_ms=now, budget=a.request_budget,
                    spacing_s=a.request_spacing_s)
    cur = run_once(store_root=a.store, s1_path=a.s1, s2_path=a.s2, journal_root=a.journal,
                   market=market, now_ms=now, max_snapshots=a.max_snapshots,
                   trigger=_trigger(a.trigger_url) if a.trigger_url else None)
    print(json.dumps({k: cur.get(k) for k in ("status", "written", "deferred_snapshots", "events",
                                              "episodes", "open_episode", "market_status",
                                              "market_requests", "trigger", "s1_error")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
