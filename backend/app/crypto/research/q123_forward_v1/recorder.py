"""Q1/Q2/Q3 Forward Recorder (`q123-forward-recorder.v1`): the detector, run forward, kept forever.

Contract: docs/crypto/q123_forward_v1/CONTRACT_Q123_FORWARD_V1.md (unchanged). This module adds no
condition, threshold, window, horizon or cost. It runs the same functions the batch runner runs -
`detect.detect` (R0 `minute_episodes` / `joint_family`), `outcome.outcome` (R0 `second_outcome`) -
on a bounded window of data read incrementally, and appends what becomes final to `store.Store`.

    python -m app.crypto.research.q123_forward_v1.recorder record \\
        --root /root/usb_runtime/crypto_context/journal \\
        --store /root/usb_runtime/q123_forward/store \\
        --runtime /root/usb_runtime/q123_forward/runtime [--klines-file F] [--until-idle]

**When a fact is final.**
* An approach (structural episode x question) is final once the newest sample read is at least
  65 s past the minute close (5 s anchor bound + W = 60 s): after that no later sample can change
  its anchor, its decision second or its R0 reason. Episodes whose minute close is before the
  first sample this store ever read are `OUTSIDE_JOURNAL`, exactly as the batch runner reports.
* An outcome horizon is final once a sample at or after its target exists (`outcome.outcome`
  stops returning PENDING). MEASURED and MISSING are both final and both stored.

**Bounded memory.**
* Seconds are kept only from the earliest unfinished approach or outcome (minus a margin).
* The R1 engine is run over every bar since SERIES_START but publishes rows only for a window
  around the unfinished episodes (the engine's publish range is an output filter: rows are
  identical, re-tested). R0's 60-minute episode overlap control depends on earlier episodes, so
  the window is seeded with one row per (level, side) whose last episode, already in the store,
  is under 60 minutes before the window: `minute_episodes` then sees the same `last` it sees on
  the full series.

Nothing here imports an order, account, leverage, AUTO or ExitGuard path, writes to the collector
root or takes its lock. Network use: the public `/fapi/v1/klines` endpoint only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import signal
import sys
import time
from array import array
from pathlib import Path

import numpy as np

from ..market_context_r0 import families as R0F
from ..market_context_r0 import layers as R0L
from ..market_context_r0.structure import ContextEngine
from ..price_structure_r0.data import Bars, aggregate, wilder_atr
from ..price_structure_r1.engine import EngineConfig
from . import CONTRACT_SHA256, VERSION
from . import contract as K
from . import detect as D
from . import frames as F
from . import outcome as O
from . import store as STO
from . import stream as ST

RECORDER_VERSION = "q123-forward-recorder.v1"
MINUTE_MS = 60_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
FINAL_AFTER_CLOSE_MS = 5_000 + R0F.WALL_W_MS
SAMPLE_MARGIN_MS = 120_000
ROW_MARGIN_MS = 2 * HOUR_MS
ROW_CHUNK_MS = DAY_MS
CHECKPOINT_EVERY_MS = 10 * MINUTE_MS
CHECKPOINT_KEEP_MS = 6 * HOUR_MS
MAX_BUFFER_SAMPLES = 8 * 3600
INGEST_PER_STEP = 600
FAULT_ENV = "Q123_REC_FAULT"
EXIT_STORE_CORRUPT = 5


# ----------------------------------------------------------------------------- faults (tests)
class Faults:
    """`Q123_REC_FAULT=point[@n][,point[@n]]`: at the n-th time `point` is reached, exit like kill -9."""

    def __init__(self, spec: str | None):
        self.want: dict[str, int] = {}
        for part in (spec or "").split(","):
            if part:
                name, _, n = part.partition("@")
                self.want[name] = int(n or 1)
        self.seen: dict[str, int] = {}

    def __call__(self, point: str):
        if point not in self.want:
            return False
        self.seen[point] = self.seen.get(point, 0) + 1
        if self.seen[point] == self.want[point]:
            if point.startswith("torn_append"):
                return True
            os._exit(137)
        return False


# ----------------------------------------------------------------------------- klines
class KlineBook:
    """Closed 1m bars from SERIES_START, as arrays. Live mode appends to a rebuildable cache."""

    COLS = ("open", "high", "low", "close", "volume", "turnover")

    def __init__(self, cache: Path | None, fixed: Path | None = None):
        self.cache = cache
        self.fixed = fixed
        self.ts = array("q")
        self.cols = {c: array("d") for c in self.COLS}
        self.version = 0
        self.last_error: str | None = None
        self.last_fetch_ms: int | None = None

    def _add(self, r: dict) -> None:
        self.ts.append(int(r["timestamp_ms"]))
        for c in self.COLS:
            self.cols[c].append(float(r[c]))

    def load(self) -> None:
        path = self.fixed or self.cache
        if path is None or not path.exists():
            return
        expect = K.SERIES_START_MS
        with open(path, "rb") as handle:
            for raw in handle:
                if not raw.endswith(b"\n"):
                    break
                r = json.loads(raw)
                if int(r["timestamp_ms"]) != expect:
                    if self.fixed:
                        raise RuntimeError("kline file is not a gap-free grid from SERIES_START")
                    break                           # cache from a bad run: keep the good prefix
                self._add(r)
                expect += MINUTE_MS
        if self.cache and not self.fixed:
            self._rewrite_cache_if_short(path)
        self.version += 1

    def _rewrite_cache_if_short(self, path: Path) -> None:
        good = len(self.ts)
        lines = sum(1 for _ in open(path, "rb"))
        if lines != good:
            STO.atomic_write(path, b"".join(self._row_line(i) for i in range(good)))

    def _row_line(self, i: int) -> bytes:
        return (json.dumps({"timestamp_ms": self.ts[i], **{c: self.cols[c][i] for c in self.COLS}},
                           sort_keys=True) + "\n").encode()

    def poll(self, now_ms: int) -> bool:
        """Live mode: append newly closed bars, page by page. Returns True when bars were added."""
        if self.fixed:
            return False
        end = now_ms - now_ms % MINUTE_MS
        start = (self.ts[-1] + MINUTE_MS) if len(self.ts) else K.SERIES_START_MS
        if start >= end:
            return False
        before = len(self.ts)
        try:
            cursor = start
            while cursor < end:
                chunk_end = min(cursor + F.PAGE * MINUTE_MS, end)
                page = {int(r[0]): r for r in F._page(cursor, chunk_end)}
                rows = []
                for ts in range(cursor, chunk_end, MINUTE_MS):
                    r = page.get(ts)
                    if r is None or ts + MINUTE_MS > int(r[6]) + 1:
                        raise RuntimeError(f"bar {ts} missing or not closed")
                    rows.append({"timestamp_ms": ts, "open": float(r[1]), "high": float(r[2]),
                                 "low": float(r[3]), "close": float(r[4]),
                                 "volume": float(r[5]), "turnover": float(r[7])})
                first = len(self.ts)
                for r in rows:
                    self._add(r)
                if self.cache:
                    with open(self.cache, "ab") as handle:
                        handle.write(b"".join(self._row_line(i)
                                              for i in range(first, len(self.ts))))
                cursor = chunk_end
            self.last_error = None
        except Exception as exc:  # network, schema, gap: keep the complete pages
            self.last_error = f"{type(exc).__name__}: {exc}"
        self.last_fetch_ms = now_ms
        if len(self.ts) == before:
            return False
        self.version += 1
        return True

    @property
    def last_open(self) -> int | None:
        return self.ts[-1] if len(self.ts) else None

    def bars(self, upto_open: int | None = None) -> dict[str, Bars]:
        """`frames.bars_from_rows` on arrays (same float values, same ATR and folding)."""
        n = len(self.ts) if upto_open is None else \
            int(np.searchsorted(np.frombuffer(self.ts, dtype=np.int64), upto_open, "right"))
        ts = np.frombuffer(self.ts, dtype=np.int64)[:n].copy()
        if ts.size == 0 or ts[0] != K.SERIES_START_MS:
            raise RuntimeError("kline series must start at the contract's SERIES_START")
        a = {c: np.frombuffer(self.cols[c], dtype=float)[:n].copy() for c in self.COLS}
        b1 = Bars("1m", MINUTE_MS, ts, a["open"], a["high"], a["low"], a["close"], a["volume"],
                  a["turnover"], wilder_atr(a["high"], a["low"], a["close"]))
        return {"1m": b1, "5m": aggregate(b1, "5m", 5), "1h": aggregate(b1, "1h", 60)}


def window_minute_frame(book: KlineBook, row_from: int, row_to: int,
                        seeds: list[tuple[int, str, int, float]]) -> R0L.MinuteFrame:
    """R1 rows with `row_from <= ts <= row_to`, plus seed rows `(ts, side, level_id, price)`."""
    bars = book.bars(upto_open=row_to)
    b1 = bars["1m"]
    engine = ContextEngine(bars, EngineConfig(K.THETAS[0], K.THETAS[1], row_from + MINUTE_MS,
                                              row_to + MINUTE_MS))
    engine.run()
    seed_rows = []
    for ts, side, lid, price in sorted(seeds):
        r = {"ts": ts, "res_price": None, "res_distance_pct": None, "res_id": None,
             "sup_price": None, "sup_distance_pct": None, "sup_id": None}
        tag = "res" if side == "RES" else "sup"
        r.update({f"{tag}_price": price, f"{tag}_distance_pct": 0.0, f"{tag}_id": lid})
        seed_rows.append(r)
    rows = seed_rows + engine.rows
    nan = np.full(len(b1.ts), np.nan)
    index_of = {int(t): i for i, t in enumerate(b1.ts) if int(t) >= row_from - HOUR_MS}
    return R0L.MinuteFrame(rows=rows, ts=b1.ts, high=b1.high, low=b1.low, close=b1.close,
                           imb_1m=nan, imb_3m=nan, index_of=index_of)


# ----------------------------------------------------------------------------- records
def approach_key(question: str, side: str, level_id, level_price, anchor_ts: int) -> str:
    raw = "|".join(str(x) for x in (RECORDER_VERSION, question, side, level_id, level_price,
                                     anchor_ts))
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _direction(question: str) -> tuple[str, int]:
    """The price direction the question's pre-registered lean expects. Not a state, not a verdict."""
    return ("UP", 1) if K.QUESTION_LEAN[question] == "LONG" else ("DOWN", -1)


def event_row(rec: dict, sx: F.SecondFrameX, ms_index: dict[int, int],
              extras: list[ST.Sample]) -> dict:
    i, j = ms_index[rec["anchor_ms"]], ms_index[rec["decision_ms"]]
    wall_side = "ASK" if rec["side"] == "RES" else "BID"
    s_i = sx.frame.samples[i]
    w = (s_i.ask_bins if wall_side == "ASK" else s_i.bid_bins).get(rec["wall_bin_low"]) or {}
    if rec["wall_present_at_w"]:
        wall_state = "PRESENT_AT_W"
    elif rec["traded_through"]:
        wall_state = "GONE_AT_W_TRADED_THROUGH"
    else:
        wall_state = "GONE_AT_W_NOT_TRADED_THROUGH"
    word, sign = _direction(rec["question"])
    xj = extras[j]
    return {
        "key": rec["event_id"], "schema": RECORDER_VERSION, "contract_sha256": CONTRACT_SHA256,
        "event_id": rec["event_id"], "question": rec["question"],
        "direction": word, "direction_sign": sign,
        "event_ts": rec["anchor_ts"], "anchor_ts": rec["anchor_ms"],
        "decision_ts": rec["decision_ms"], "decision_known_ts": rec["decision_known_ms"],
        "collector_session_id": rec["session_id"],
        "structure_level_id": rec["level_id"], "structure_price": rec["level_price"],
        "structure_side": rec["side"],
        "wall_bin": rec["wall_bin_low"], "wall_side": wall_side, "wall_state": wall_state,
        "wall_persistence_ms_at_anchor": w.get("observed_persistence_ms"),
        "wall_coverage_at_anchor": w.get("coverage"),
        "flow_state": xj.flow_state,
        "coverage_state": {"band01_bid": xj.band01_bid_coverage,
                           "band01_ask": xj.band01_ask_coverage, "flow60": xj.flow60_coverage,
                           "book": xj.book_state},
        "generation": {"anchor": rec["generation_anchor"], "decision": rec["generation_decision"]},
        "reference_price": rec["p0_mid"],
        "validity": rec["validity"], "invalid_reason": rec["invalid_reason"],
        "fires": rec["fires"],
        "diagnostics": {"absorption_candidate": rec["diag"]["absorption_at_decision"],
                        "oi": rec["diag"]["oi"], "funding": rec["diag"]["funding"],
                        "basis": rec["diag"]["basis"], "c1": rec["diag"]["c1"],
                        "utc_session": rec["diag"]["utc_session"], "used_in_trigger": False},
        "detector": rec,
    }


def _missing_reason(sf: R0L.SecondFrame, i: int, horizon_s: int) -> str:
    """Why R0's `second_outcome` returned None, restating its own checks in order."""
    target = sf.ms[i] + horizon_s * 1000
    j = int(np.searchsorted(sf.ms, target, side="right")) - 1
    if j <= i or j >= len(sf.ms):
        return "NO_SAMPLE_AFTER_DECISION"
    if sf.session_of[j] != sf.session_of[i]:
        return "SESSION_BOUNDARY"
    if sf.ms[j] < target - 5_000:
        return "GAP_OVER_5S_AT_TARGET"
    if not np.isfinite(sf.mids[i]):
        return "P0_NOT_FINITE"
    path = sf.mids[i + 1:j + 1]
    if not np.isfinite(path).any():
        return "NO_FINITE_PATH"
    return "EXIT_NOT_FINITE"


def outcome_row(ev: dict, o: dict, sf: R0L.SecondFrame, j_dec: int) -> dict:
    rec = ev["detector"]
    h = o["horizon_min"]
    target = int(sf.ms[j_dec]) + h * MINUTE_MS
    det = dict(o, event_id=rec["event_id"], question=rec["question"], fires=rec["fires"])
    base = {"key": f'{rec["event_id"]}:{h}', "schema": RECORDER_VERSION,
            "event_id": rec["event_id"], "question": rec["question"], "horizon_min": h,
            "role": "PRIMARY_GRID" if h in K.TEST_GRID_HORIZONS_MIN else "DIAGNOSTIC",
            "fires": rec["fires"], "status": o["status"], "target_ts": target,
            "decision_ts": int(sf.ms[j_dec]), "cost_bp": K.RT_COST_BP,
            "est_fee_bp": K.EST_FEE_BP, "est_slippage_bp": K.EST_SLIPPAGE_BP, "detector": det}
    if o["status"] == "MEASURED":
        k = int(np.searchsorted(sf.ms, target, side="right")) - 1
        base.update({"reachable": o["reachable_bp"] > 0, "reachable_bp": o["reachable_bp"],
                     "observed_ts": int(sf.ms[k]), "reference_price": o["p0"],
                     "exit_price": o["close_h"], "raw_move_bp": o["raw_move_bp"],
                     "direction_normalized_bp": o["directional_move_bp"],
                     "gross_bp": o["directional_move_bp"], "net_bp": o["net_move_bp"],
                     "MFE_bp": o["mfe_bp"], "MAE_bp": o["mae_bp"],
                     "high_excursion_bp": o["high_excursion_bp"],
                     "low_excursion_bp": o["low_excursion_bp"], "missing_reason": None})
    else:
        base.update({"reachable": None, "reachable_bp": None, "observed_ts": None,
                     "reference_price": rec["p0_mid"], "exit_price": None, "raw_move_bp": None,
                     "direction_normalized_bp": None, "gross_bp": None, "net_bp": None,
                     "MFE_bp": None, "MAE_bp": None, "high_excursion_bp": None,
                     "low_excursion_bp": None,
                     "missing_reason": _missing_reason(sf, j_dec, h * 60)})
    return base


# ----------------------------------------------------------------------------- recorder
class Recorder:
    def __init__(self, root: Path, store_root: Path, runtime: Path, *,
                 klines_file: Path | None = None, fault=None, now=None):
        self.root = Path(root)
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        (self.runtime / "checkpoints").mkdir(exist_ok=True)
        self.fault = fault or Faults(os.environ.get(FAULT_ENV))
        self.now = now or (lambda: int(time.time() * 1000))
        self.store = STO.Store.open(store_root, fault=self.fault, create_meta={
            "schema": RECORDER_VERSION, "detector_version": VERSION,
            "contract_sha256": CONTRACT_SHA256, "created_ms": self.now(),
            "retention": "NONE - kept until research end or user approval",
            "coverage_start_ms": None})
        self.book = KlineBook(None if klines_file else self.runtime / "klines.jsonl",
                              klines_file)
        self.book.load()
        self.cursors: dict[str, ST.SessionCursor] = {}
        self.unrecoverable: dict[str, str] = {}
        self.buffer: list[ST.Sample] = []
        self.last_ckpt_ms: int | None = None
        self.row_from: int | None = None
        self.counters = {"cycles": 0, "samples_read": 0, "approaches_new": 0, "events_new": 0,
                         "outcomes_new": 0, "existing_skipped": 0, "conflicts": 0,
                         "stranded_outcomes": 0, "unresolvable_approaches": 0,
                         "out_of_order_samples": 0}
        self.restored_from: str | None = None
        self.quality: dict[str, dict] = {}
        self._restore()

    # ---------------------------------------------------------------- restore / checkpoint
    def _needed_from(self) -> int | None:
        """Earliest sample time any unfinished fact may still need, from the store alone."""
        pend = []
        for ev in self.store.rows[STO.EVENTS].values():
            if ev["validity"] != "VALID":
                continue
            if any(not self.store.has(STO.OUTCOMES, f'{ev["event_id"]}:{h}')
                   for h in K.HORIZONS_MIN):
                pend.append(ev["decision_ts"])
        anchors = [a["anchor_ts"] for a in self.store.rows[STO.APPROACHES].values()]
        frontier = (max(anchors) - ROW_MARGIN_MS) if anchors else None
        cands = [x for x in pend] + ([frontier] if frontier is not None else [])
        return (min(cands) - SAMPLE_MARGIN_MS) if cands else None

    def _restore(self) -> None:
        anchors = [a["anchor_ts"] for a in self.store.rows[STO.APPROACHES].values()]
        self.row_from = max(K.PUBLISH_FROM_MS, max(anchors) - ROW_MARGIN_MS) if anchors \
            else K.PUBLISH_FROM_MS
        need = self._needed_from()
        best = None
        for path in sorted((self.runtime / "checkpoints").glob("ckpt-*.json")):
            try:
                body = json.loads(path.read_text())
                digest = body.pop("sha256")
                if hashlib.sha256(STO.canonical(body).encode()).hexdigest() != digest:
                    continue
                if body.get("schema") != RECORDER_VERSION:
                    continue
            except (ValueError, KeyError, OSError):
                continue
            if need is None:
                break                    # an empty store is rebuilt from the session starts
            if body["sample_ms"] <= need:
                if best is None or body["sample_ms"] > best[0]["sample_ms"]:
                    best = (body, path.name)
        if best is not None:
            body, name = best
            self.cursors = {s8: ST.SessionCursor.from_json(c) for s8, c in body["cursors"].items()}
            self.unrecoverable = dict(body.get("unrecoverable") or {})
            self.quality = dict(body.get("quality") or {})
            self.last_ckpt_ms = body["sample_ms"]
            self.restored_from = name
        else:
            self.restored_from = "SESSION_START"
        self._skip_before = need

    def _checkpoint(self) -> None:
        if not self.buffer:
            return
        tail = self.buffer[-1].s.ms
        if self.last_ckpt_ms is not None and tail - self.last_ckpt_ms < CHECKPOINT_EVERY_MS:
            return
        body = {"schema": RECORDER_VERSION, "sample_ms": tail,
                "cursors": {s8: c.to_json() for s8, c in self.cursors.items()},
                "unrecoverable": self.unrecoverable, "quality": self.quality}
        body["sha256"] = hashlib.sha256(STO.canonical(body).encode()).hexdigest()
        STO.atomic_write(self.runtime / "checkpoints" / f"ckpt-{tail:015d}.json",
                         STO.canonical(body).encode())
        self.last_ckpt_ms = tail
        need = self._needed_from()
        keep_from = (need if need is not None else tail) - CHECKPOINT_KEEP_MS
        ckpts = sorted((self.runtime / "checkpoints").glob("ckpt-*.json"))
        older = [p for p in ckpts if int(p.stem.split("-")[1]) < keep_from]
        for p in older[:-1]:                 # always keep the newest one below the bound
            p.unlink(missing_ok=True)

    # ---------------------------------------------------------------- ingest
    def _ingest(self) -> int:
        if len(self.buffer) >= MAX_BUFFER_SAMPLES:
            return 0                                    # backpressure: wait for klines / trim
        order = ST.sessions_in_order(self.root, self.cursors)
        got = 0
        for idx, s8 in enumerate(order):
            if s8 in self.unrecoverable:
                continue
            c = self.cursors.setdefault(s8, ST.SessionCursor(s8=s8))
            if c.done:
                continue
            reader = ST.SessionReader(self.root, c, compact=True)
            over = idx < len(order) - 1
            try:
                samples, progressed = reader.step(session_over=over, limit=INGEST_PER_STEP)
            except ST.UnrecoverableStart as exc:
                self.unrecoverable[s8] = str(exc)
                continue
            for s in samples:
                self._count_quality(s)
                if self._skip_before is not None and s.s.ms < self._skip_before:
                    continue
                if self.buffer and s.s.ms < self.buffer[-1].s.ms:
                    self.counters["out_of_order_samples"] += 1
                self.buffer.append(s)
                got += 1
            if progressed or not c.done:
                break          # one session per step; the live one blocks later ones anyway
        self._flush_quality()
        if got and self.store.meta.get("coverage_start_ms") is None:
            self.store.set_meta(coverage_start_ms=self.buffer[0].s.ms)
        self.counters["samples_read"] += got
        return got

    # ---------------------------------------------------------------- quality ledger
    QUALITY_FIELDS = ("collector_state", "book_state", "liquidity_state", "flow_state",
                      "band01_bid_coverage", "flow60_coverage", "absorption")

    def _count_quality(self, s: ST.Sample) -> None:
        hour = s.s.ms - s.s.ms % HOUR_MS
        key = f"{s.session_id[:8]}:{hour}"
        q = self.quality.get(key)
        if q is None:
            q = self.quality[key] = {"key": key, "schema": RECORDER_VERSION,
                                     "session_id": s.session_id, "hour_start_ms": hour,
                                     "samples": 0, "r0_ok": 0, "first_ms": s.s.ms,
                                     **{f: {} for f in self.QUALITY_FIELDS}}
        q["samples"] += 1
        q["r0_ok"] += int(bool(s.s.ok))
        q["last_ms"] = s.s.ms
        for f in self.QUALITY_FIELDS:
            v = str(getattr(s, f))
            q[f][v] = q[f].get(v, 0) + 1

    def _flush_quality(self) -> None:
        """An hour is final once a later sample exists or its session has been read to the end."""
        tail = max((c.last_sample_ms or 0) for c in self.cursors.values()) if self.cursors else 0
        done8 = {s8 for s8, c in self.cursors.items() if c.done}
        final = [k for k, q in self.quality.items()
                 if q["hour_start_ms"] + HOUR_MS <= tail or k.split(":")[0] in done8]
        if not final:
            return
        rows = sorted((self.quality[k] for k in final), key=lambda q: (q["hour_start_ms"], q["key"]))
        self.store.append(STO.QUALITY, rows)
        for k in final:
            del self.quality[k]

    # ---------------------------------------------------------------- frames
    def _second_frame(self) -> tuple[F.SecondFrameX, dict[int, int]]:
        buf = self.buffer
        if self.counters["out_of_order_samples"]:
            buf.sort(key=lambda s: (s.s.ms, s.session_id, s.seq))
        sess: dict[str, int] = {}
        for s in buf:
            sess.setdefault(s.session_id, len(sess))
        frame = R0L.SecondFrame(
            samples=[s.s for s in buf],
            mids=np.asarray([s.s.mid if s.s.mid is not None else np.nan for s in buf]),
            ms=np.asarray([s.s.ms for s in buf], dtype=np.int64),
            session_of=np.asarray([sess[s.session_id] for s in buf], dtype=np.int32))
        sx = F.SecondFrameX(frame=frame, generation=[s.generation for s in buf],
                            session_id=[s.session_id for s in buf],
                            absorption=[s.absorption for s in buf],
                            liquidity_state=[s.liquidity_state for s in buf],
                            flow_state=[s.flow_state for s in buf])
        return sx, {int(m): k for k, m in enumerate(frame.ms)}

    def _seeds(self, row_from: int) -> list[tuple[int, str, int, float]]:
        last: dict[tuple, tuple[int, float]] = {}
        for a in self.store.rows[STO.APPROACHES].values():
            if a["anchor_ts"] >= row_from:
                continue
            k = (a["level_id"], a["side"])
            if k not in last or a["anchor_ts"] > last[k][0]:
                last[k] = (a["anchor_ts"], a["level_price"])
        return [(ts, side, lid, price) for (lid, side), (ts, price) in last.items()
                if ts > row_from - R0F.T3_GAP_MS]

    # ---------------------------------------------------------------- finalize
    def _finalize(self) -> dict:
        rep = {"approaches": 0, "events": 0, "outcomes": 0}
        coverage = self.store.meta.get("coverage_start_ms")
        if self.book.last_open is None or coverage is None or not self.buffer:
            return rep
        tail = self.buffer[-1].s.ms
        row_from = max(K.PUBLISH_FROM_MS, self.row_from or K.PUBLISH_FROM_MS)
        row_to = min(self.book.last_open, row_from + ROW_CHUNK_MS)
        sx, ms_index = self._second_frame()
        sf = sx.frame
        new_events, new_approaches = [], []
        pending_anchors = []
        if row_to >= row_from:
            seeds = self._seeds(row_from)
            mkey = (row_from, row_to, self.book.version, tuple(sorted(seeds)))
            if getattr(self, "_mf_key", None) != mkey:
                self._mf = window_minute_frame(self.book, row_from, row_to, seeds)
                self._mf_key = mkey
            mf = self._mf
            records, _ = D.detect(mf, sx)
            by_ep = {(r["question"], r["anchor_ts"], r["level_id"], r["side"]): r for r in records}
            for e in R0F.minute_episodes(mf):
                if e.anchor_ts < row_from:
                    continue                                   # a seed or an earlier window
                info = e.anchor_ts + MINUTE_MS
                for q, fam in K.QUESTIONS.items():
                    if K.QUESTION_SIDE[q] != e.side:
                        continue
                    key = approach_key(q, e.side, e.level_id, e.level_price, e.anchor_ts)
                    if info < coverage:
                        status = "OUTSIDE_JOURNAL"
                    elif tail < info + FINAL_AFTER_CLOSE_MS:
                        pending_anchors.append(e.anchor_ts)
                        continue
                    elif sf.ms[0] > info:
                        if self.store.has(STO.APPROACHES, key):
                            continue                   # final and stored; its seconds are trimmed
                        self.counters["unresolvable_approaches"] += 1
                        pending_anchors.append(e.anchor_ts)
                        continue
                    else:
                        spec = R0F.joint_family(mf, sf, [e], fam, K.VARIANT)
                        if spec["episodes"]:
                            status = "ADMITTED"
                        else:
                            status = next(k for k, v in spec["excluded"].items() if v)
                    row = {"key": key, "schema": RECORDER_VERSION, "question": q,
                           "r0_family": fam, "side": e.side, "level_id": e.level_id,
                           "level_price": e.level_price, "anchor_ts": e.anchor_ts,
                           "status": status, "event_id": None}
                    if status == "ADMITTED":
                        rec = by_ep[(q, e.anchor_ts, e.level_id, e.side)]
                        row["event_id"] = rec["event_id"]
                        row["validity"] = rec["validity"]
                        row["invalid_reason"] = rec["invalid_reason"]
                        row["fires"] = rec["fires"]
                        new_events.append(event_row(rec, sx, ms_index, self.buffer))
                    new_approaches.append(row)
            new_events.sort(key=lambda r: (r["event_ts"], r["question"], r["event_id"]))
            new_approaches.sort(key=lambda r: (r["anchor_ts"], r["question"], r["key"]))
            r1 = self.store.append(STO.EVENTS, new_events)
            r2 = self.store.append(STO.APPROACHES, new_approaches)
            rep["events"], rep["approaches"] = r1["new"], r2["new"]
            self.counters["existing_skipped"] += r1["existing"] + r2["existing"]
            self.counters["conflicts"] += r1["conflict"] + r2["conflict"]
            self.counters["events_new"] += r1["new"]
            self.counters["approaches_new"] += r2["new"]
            nxt = min(pending_anchors) if pending_anchors else row_to + MINUTE_MS
            self.row_from = max(K.PUBLISH_FROM_MS, nxt - ROW_MARGIN_MS)
            self._approach_need = (min(pending_anchors) + MINUTE_MS) if pending_anchors \
                else row_to + 2 * MINUTE_MS
        # outcomes
        outs, need = [], []
        stranded = 0
        for ev in self.store.rows[STO.EVENTS].values():
            if ev["validity"] != "VALID":
                continue
            missing = [h for h in K.HORIZONS_MIN
                       if not self.store.has(STO.OUTCOMES, f'{ev["event_id"]}:{h}')]
            if not missing:
                continue
            j = ms_index.get(ev["decision_ts"])
            if j is None:
                if ev["decision_ts"] < int(sf.ms[0]):
                    stranded += 1                # its seconds are gone: never estimated
                else:
                    need.append(ev["decision_ts"])
                continue
            for h in missing:
                o = O.outcome(sf, ev["detector"]["lean"], ev["structure_price"],
                              ev["structure_side"], h, decision_index=j)
                if o["status"] == "PENDING":
                    need.append(ev["decision_ts"])
                    continue
                outs.append(outcome_row(ev, o, sf, j))
        outs.sort(key=lambda r: (r["decision_ts"], r["horizon_min"], r["event_id"]))
        r3 = self.store.append(STO.OUTCOMES, outs)
        rep["outcomes"] = r3["new"]
        self.counters["outcomes_new"] += r3["new"]
        self.counters["existing_skipped"] += r3["existing"]
        self.counters["conflicts"] += r3["conflict"]
        self.counters["stranded_outcomes"] = stranded
        self._outcome_need = min(need) if need else None
        return rep

    def _trim(self) -> None:
        if not self.buffer:
            return
        bounds = [b for b in (getattr(self, "_approach_need", None),
                              getattr(self, "_outcome_need", None)) if b is not None]
        if not bounds:
            return
        cut = min(bounds) - SAMPLE_MARGIN_MS
        ms = [s.s.ms for s in self.buffer]
        k = int(np.searchsorted(np.asarray(ms, dtype=np.int64), cut, "left")) - 1
        if k > 0:
            del self.buffer[:k]                         # keep one sample before the cut

    # ---------------------------------------------------------------- cycle
    def cycle(self, *, poll_klines: bool = True) -> dict:
        self.counters["cycles"] += 1
        if poll_klines:
            self.book.poll(self.now())
        got = self._ingest()
        rep = self._finalize()
        self._trim()
        self._checkpoint()
        rep["samples"] = got
        self.write_status(rep)
        return rep

    def status(self) -> dict:
        tail = self.buffer[-1].s.ms if self.buffer else None
        now = self.now()
        lag = None if tail is None else now - tail
        if self.store.conflicts or self.unrecoverable:
            state = "DEGRADED"
        elif tail is None or lag > 120_000:
            state = "WAITING_FOR_DATA"
        elif self.book.last_error and self.book.last_open is not None and \
                now - (self.book.last_open + MINUTE_MS) > 5 * MINUTE_MS:
            state = "DEGRADED"
        else:
            state = "RUNNING"
        ru = resource.getrusage(resource.RUSAGE_SELF)
        return {"schema": RECORDER_VERSION, "state": state, "now_ms": now,
                "tail_sample_ms": tail, "lag_ms": lag, "buffer_samples": len(self.buffer),
                "buffer_from_ms": self.buffer[0].s.ms if self.buffer else None,
                "kline_last_open_ms": self.book.last_open, "kline_error": self.book.last_error,
                "row_from_ms": self.row_from, "restored_from": self.restored_from,
                "unrecoverable_sessions": self.unrecoverable, "store_repaired": self.store.repaired,
                "store_conflicts": self.store.conflicts,
                "store_counts": {n: len(self.store.keys[n]) for n in STO.FILES},
                "counters": self.counters, "maxrss_mb": round(ru.ru_maxrss / 1024, 1),
                "cpu_s": round(ru.ru_utime + ru.ru_stime, 2),
                "sessions": {s8: {"done": c.done, "samples": c.samples,
                                  "last_sample_ms": c.last_sample_ms, "pos": c.pos}
                             for s8, c in self.cursors.items()}}

    def write_status(self, rep: dict | None = None) -> None:
        body = self.status()
        body["last_cycle"] = rep
        STO.atomic_write(self.runtime / "status.json",
                         (json.dumps(body, indent=1, sort_keys=True) + "\n").encode())


# ----------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.crypto.research.q123_forward_v1.recorder")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--root", required=True, help="Context Collector journal root (read only)")
    r.add_argument("--store", required=True)
    r.add_argument("--runtime", required=True)
    r.add_argument("--klines-file", help="offline: a gap-free kline file (no network)")
    r.add_argument("--poll-s", type=float, default=15.0)
    r.add_argument("--until-idle", action="store_true", help="replay: stop when nothing is left")
    r.add_argument("--max-cycles", type=int, default=0)
    s = sub.add_parser("status")
    s.add_argument("--runtime", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("--store", required=True)
    a.add_argument("--out")
    m = sub.add_parser("summary24h")
    m.add_argument("--store", required=True)
    m.add_argument("--runtime")
    m.add_argument("--from-ms", type=int)
    m.add_argument("--to-ms", type=int)
    args = p.parse_args(argv)
    if args.cmd == "status":
        print((Path(args.runtime) / "status.json").read_text())
        return 0
    if args.cmd == "analyze":
        from . import analyze
        out = analyze.analyze(Path(args.store))
        text = json.dumps(out, indent=1, sort_keys=True, default=str)
        if args.out:
            Path(args.out).write_text(text + "\n")
        print(text)
        return 0
    if args.cmd == "summary24h":
        from . import analyze
        print(json.dumps(analyze.summary_window(Path(args.store), args.from_ms, args.to_ms,
                                                runtime=Path(args.runtime) if args.runtime
                                                else None), indent=1, sort_keys=True))
        return 0
    stop = {"flag": False}

    def _term(signum, frame):  # graceful: finish the cycle, checkpoint, exit 0
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    try:
        rec = Recorder(Path(args.root), Path(args.store), Path(args.runtime),
                       klines_file=Path(args.klines_file) if args.klines_file else None)
    except STO.StoreCorrupt as exc:
        # A store that does not parse is never "repaired" by a restart loop: stop and say why.
        print(json.dumps({"state": "REFUSED", "reason": f"StoreCorrupt: {exc}"}), file=sys.stderr)
        return EXIT_STORE_CORRUPT
    idle = 0
    n = 0
    while not stop["flag"]:
        rep = rec.cycle(poll_klines=not args.klines_file)
        n += 1
        busy = rep["samples"] or rep["approaches"] or rep["events"] or rep["outcomes"]
        if args.max_cycles and n >= args.max_cycles:
            break
        if busy:
            idle = 0
            continue
        idle += 1
        if args.until_idle and idle >= 2:
            break
        time.sleep(args.poll_s if not args.until_idle else 0)
    rec.last_ckpt_ms = None
    rec._checkpoint()
    rec.write_status({"exit": "graceful"})
    print(json.dumps({k: rec.status()[k] for k in ("state", "store_counts", "counters",
                                                    "maxrss_mb", "cpu_s", "restored_from")},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
