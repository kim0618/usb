"""Layers B and C from the Market Structure V0 journal, replayed forward from each session start.

The Liquidity Map viewer reads this journal backwards because it attaches to a session that is
already running and has to prove that the resting set it reconstructed is whole. A research replay
has the easier problem: it starts at the first record of a session, so every wall that is resting at
any instant was OPENED inside the records already read, and the set is whole by construction. What
this module borrows unchanged is the part that is a *rule* rather than a *read*: `lm-wall.v2`, the
frozen wall selection, imported from `liquidity_map.wallrule`. No threshold in it is touched.

Two honesty constraints from the V0 contract are carried through rather than smoothed over:

* Only the +-0.1 percent band is COMPLETE. The 0.25 / 0.5 / 1.0 percent bands are permanently
  PARTIAL lower bounds because a `limit=1000` snapshot does not reach that far, so anything this
  module reports beyond 0.1 percent is labelled a lower bound and never used as a quantity.
* Flow windows carry their own coverage. A PARTIAL flow window is not a small number, it is an
  unknown one, and it maps to the UNKNOWN state rather than to a neutral reading.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from ...liquidity_map import wallrule
from ...market_structure_v0.contract import COMPLETE

ROOT = Path(__file__).resolve().parents[5]
JOURNAL_ROOTS = (ROOT / "data/runtime/ms_v0_preview", ROOT / "data/runtime/ms_v0_v11")


@dataclass(frozen=True)
class Session:
    root: Path
    session_id: str
    session8: str
    started_ms: int
    ended_ms: int | None
    end_reason: str | None
    records: int | None
    samples: int | None
    contract_sha256: str | None


def _read_jsonl(path: Path) -> Iterator[dict]:
    """Whole records only. A torn trailing line is absent, not data."""
    with open(path, "rb") as fh:
        blob = fh.read()
    cut = blob.rfind(b"\n")
    if cut < 0:
        return
    for line in blob[:cut].split(b"\n"):
        if line:
            yield json.loads(line)


def _files(root: Path, kind: str, session8: str) -> list[Path]:
    return sorted((root / kind).glob(f"{kind}-*-{session8}-*.jsonl"))


def sessions() -> list[Session]:
    out: list[Session] = []
    for root in JOURNAL_ROOTS:
        if not root.exists():
            continue
        starts: dict[str, dict] = {}
        ends: dict[str, dict] = {}
        for path in sorted((root / "session").glob("session-*.jsonl")):
            for rec in _read_jsonl(path):
                p = rec["payload"]
                sid = rec.get("session_id") or p.get("session_id")
                if p.get("event") == "START":
                    starts[p["session_id"]] = p
                elif p.get("event") == "END" and sid:
                    ends[sid] = p
        for sid, start in starts.items():
            end = ends.get(sid, {})
            out.append(Session(root=root, session_id=sid, session8=sid[:8],
                               started_ms=int(start["started_ms"]),
                               ended_ms=end.get("ended_ms"), end_reason=end.get("reason"),
                               records=end.get("records"), samples=end.get("samples"),
                               contract_sha256=(start.get("contract") or {}).get("recorded_sha256")))
    return sorted(out, key=lambda s: s.started_ms)


@dataclass
class Sample:
    """One second of Layer B and Layer C, as the collector published it."""

    ms: int
    session8: str
    mid: float | None
    best_bid: float | None
    best_ask: float | None
    book_state: str
    book_fresh: bool
    book_age_ms: int | None
    generation: int | None
    band01_bid_notional: float | None
    band01_ask_notional: float | None
    band01_coverage: str
    band01_imbalance_usdt: float | None
    known_low: float | None
    known_high: float | None
    flow: dict = field(default_factory=dict)
    walls_bid: list[dict] = field(default_factory=list)
    walls_ask: list[dict] = field(default_factory=list)
    resting_candidates: int = 0


def _f(v) -> float | None:
    return None if v is None else float(v)


def _band01(bands: list[dict]) -> dict:
    for b in bands or []:
        if str(b.get("band_pct")) == "0.1":
            return b
    return {}


def _flow_view(flow: dict) -> dict:
    out = {}
    for w in ("5s", "15s", "60s"):
        f = flow.get(w) or {}
        out[w] = {"coverage": f.get("coverage"),
                  "coverage_reason": f.get("coverage_reason"),
                  "imbalance_usdt": _f(f.get("imbalance_usdt")),
                  "buy_usdt": _f(f.get("buy_usdt")),
                  "sell_usdt": _f(f.get("sell_usdt")),
                  "observed_buy_usdt": _f(f.get("observed_buy_usdt")),
                  "observed_sell_usdt": _f(f.get("observed_sell_usdt")),
                  "trades": f.get("trades")}
    return out


def replay(limit_sessions: int | None = None) -> Iterator[Sample]:
    """Yield one Sample per `derived` record, with the resting wall set as of that record.

    Wall rows and derived rows share one monotonic `seq` inside a session, so the two streams are
    merged on `seq` and the wall set a sample sees is exactly the set the collector had published
    by then. No row from after the sample can reach it.
    """
    for sess in (sessions() if limit_sessions is None else sessions()[:limit_sessions]):
        resting: dict[tuple[str, str], dict] = {}
        walls = [(rec["seq"], rec["payload"]) for path in _files(sess.root, "wall", sess.session8)
                 for rec in _read_jsonl(path)]
        walls.sort(key=lambda x: x[0])
        wi = 0
        for path in _files(sess.root, "derived", sess.session8):
            for rec in _read_jsonl(path):
                seq = rec["seq"]
                while wi < len(walls) and walls[wi][0] < seq:
                    p = walls[wi][1]
                    key = (str(p.get("side") or ""), str(p.get("price") or ""))
                    status, event = str(p.get("status") or ""), str(p.get("event") or "")
                    if status == "ACTIVE" or (status not in ("ENDED", "INTERRUPTED")
                                              and event in ("OPENED", "UPDATED")):
                        resting[key] = p
                    else:
                        resting.pop(key, None)
                    wi += 1
                payload = rec["payload"]
                book = payload.get("book") or {}
                b01 = _band01(book.get("bands"))
                mid = _f(book.get("mid"))
                mid_d = None if book.get("mid") is None else Decimal(str(book["mid"]))
                rows = list(resting.values())
                sel = {}
                for side in ("BID", "ASK"):
                    sel[side] = wallrule.select(rows, side=side, mid=mid_d,
                                                latest_sample_ms=rec["receive_ms"],
                                                known_low=None if book.get("known_low") is None
                                                else Decimal(str(book["known_low"])),
                                                known_high=None if book.get("known_high") is None
                                                else Decimal(str(book["known_high"])))
                yield Sample(
                    ms=int(rec["receive_ms"]), session8=sess.session8, mid=mid,
                    best_bid=_f(book.get("best_bid")), best_ask=_f(book.get("best_ask")),
                    book_state=str(book.get("state") or ""), book_fresh=bool(book.get("fresh")),
                    book_age_ms=book.get("age_ms"), generation=book.get("generation"),
                    band01_bid_notional=_f((b01.get("bid") or {}).get("notional")),
                    band01_ask_notional=_f((b01.get("ask") or {}).get("notional")),
                    band01_coverage=str((b01.get("bid") or {}).get("coverage") or ""),
                    band01_imbalance_usdt=_f(b01.get("imbalance_usdt")),
                    known_low=_f(book.get("known_low")), known_high=_f(book.get("known_high")),
                    flow=_flow_view(payload.get("flow") or {}),
                    walls_bid=sel["BID"].walls, walls_ask=sel["ASK"].walls,
                    resting_candidates=len(rows))


def census() -> dict:
    """What Layers B and C actually cover. Counted before any outcome is computed."""
    ss = sessions()
    per_session = [{"session8": s.session8, "root": s.root.name, "started_ms": s.started_ms,
                    "ended_ms": s.ended_ms, "end_reason": s.end_reason,
                    "elapsed_s": None if s.ended_ms is None else round((s.ended_ms - s.started_ms) / 1000, 1),
                    "samples_reported": s.samples, "records_reported": s.records,
                    "contract_sha256": s.contract_sha256} for s in ss]
    gaps = []
    for a, b in zip(ss[:-1], ss[1:]):
        if a.ended_ms is not None:
            gaps.append({"after": a.session8, "before": b.session8,
                         "gap_s": round((b.started_ms - a.ended_ms) / 1000, 1)})
    n = 0
    flow_cov = {w: {} for w in ("5s", "15s", "60s")}
    band_cov: dict[str, int] = {}
    book_state: dict[str, int] = {}
    walls_bid = walls_ask = 0
    cand_total = 0
    gens: set = set()
    first_ms = last_ms = None
    for s in replay():
        n += 1
        first_ms = first_ms if first_ms is not None else s.ms
        last_ms = s.ms
        for w in flow_cov:
            c = s.flow[w]["coverage"]
            flow_cov[w][c] = flow_cov[w].get(c, 0) + 1
        band_cov[s.band01_coverage] = band_cov.get(s.band01_coverage, 0) + 1
        book_state[s.book_state] = book_state.get(s.book_state, 0) + 1
        walls_bid += len(s.walls_bid)
        walls_ask += len(s.walls_ask)
        cand_total += s.resting_candidates
        if s.generation is not None:
            gens.add(s.generation)
        n_last = s
    return {"sessions": per_session, "inter_session_gaps": gaps,
            "samples": n, "first_sample_ms": first_ms, "last_sample_ms": last_ms,
            "span_s": None if first_ms is None else round((last_ms - first_ms) / 1000, 1),
            "flow_coverage": flow_cov, "band_0_1_coverage": band_cov, "book_state": book_state,
            "wall_rows_bid": walls_bid, "wall_rows_ask": walls_ask,
            "mean_walls_per_sample": round((walls_bid + walls_ask) / n, 3) if n else None,
            "mean_resting_candidates_per_sample": round(cand_total / n, 2) if n else None,
            "generations_seen": sorted(gens),
            "wall_rule": wallrule.rule_identity()}
