"""Run Manual Context & Exit R0.1 end to end and write the results file.

    python -m app.crypto.research.manual_context_exit_r0.run [--out PATH] [--tfs 15s,1m,...]

The output carries no wall-clock time, so two runs on the same inputs are byte-identical
(contract section 10).
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
from pathlib import Path

import numpy as np

from . import bars as B
from . import stats as ST
from . import structure as S
from . import study as Y
from . import walls as W

CONTRACT = "docs/crypto/manual_context_exit_r0/CONTRACT_MANUAL_CONTEXT_EXIT_R0_1.md"
CONTRACT_SHA256 = "10d6f7fc70df0b86b07b8baf0139e77e2124a70a97634986c2f32451f99659ee"
C1J_ROOT = Path.home() / "usb-q123-evidence/first-24h-20261011/journal"
K1C_PATH = Path.home() / ("usb-q123-evidence/first-24h-20261011/klines/"
                          "binance_kline_1m_1788998400000_1791723000000.jsonl")
LEDGER_COPY = Path.home() / "usb-manual-context-r0-evidence/paper_manual_ledger.jsonl"
LIVE_FILLS_COPY = Path.home() / "usb-manual-context-r0-evidence/binance_live_fills.json"
OUT = B.ROOT / "data/research/crypto/manual_context_exit_r0/results_r0_1.json"


def _dir_sha(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(glob.glob(str(root / "**" / "*"), recursive=True)):
        p = Path(f)
        if p.is_file() and not p.name.endswith(".lock"):
            h.update(str(p.relative_to(root)).encode())
            h.update(B.file_sha256(p).encode())
    return h.hexdigest()


def provenance() -> dict:
    return {
        "contract": CONTRACT, "contract_sha256": B.file_sha256(B.ROOT / CONTRACT),
        "K1": {"path": str(B.K1_PATH.relative_to(B.ROOT)), "sha256": B.file_sha256(B.K1_PATH),
               "end_ms_exclusive": B.K1_END_MS},
        "D1": {p.name: B.file_sha256(p) for p in (B.FUNDING_PATH, B.INDEX_PATH, B.OI_PATH)},
        "K15": {p.name: B.file_sha256(p) for p in B.k15_days()},
        "C1J_journal_sha256": _dir_sha(C1J_ROOT) if C1J_ROOT.exists() else None,
        "K1C_sha256": B.file_sha256(K1C_PATH) if K1C_PATH.exists() else None,
    }


# --------------------------------------------------------------------------- kline timeframes

def tf_study(tf: str, k1: B.Series, der: B.Derivatives) -> tuple[dict, dict]:
    """Everything of contract s3, s5-s7 for one timeframe. Returns (report, raw for Holm)."""
    s = B.series(tf, k1)
    run = S.run(s)
    eps = Y.episodes(run)
    pub_idx = np.flatnonzero(run.published)
    report: dict = {
        "bars": len(run), "published_bars": int(run.published.sum()),
        "span_utc": [_utc(int(run.ts[pub_idx[0]])), _utc(int(run.known_ms[-1]))] if len(pub_idx) else None,
        "r1_audit": {"violations": run.violations, "vanished": run.vanished},
        "level_coverage": {"resistance": ST.rate(np.isfinite(run.res[run.published])),
                           "support": ST.rate(np.isfinite(run.sup[run.published]))},
        "episodes": {f"{sd}|{k}": sum(1 for e in eps if e.side == sd and e.kind == k)
                     for sd in ("RES", "SUP") for k in ("REAL", "PLACEBO")},
        "reaction": Y.reaction_study(run, eps),
        "distance_bins": Y.distance_bins(run),
    }
    trades = Y.cohort_entries(run, eps)
    for t in trades:
        t.exits = Y.simulate(run, t, der)
    raw: dict = {"q1": {}, "q6": {}, "hold": {}, "entry": {}}
    for side in ("RES", "SUP"):
        raw["q1"][side] = report["reaction"][side][f"h{Y.PRIMARY_H}"]["real_minus_placebo_reaction"]
    cohorts: dict = {}
    for cohort in ("FADE", "BREAK"):
        tr = [t for t in trades if t.cohort == cohort]
        cell = {"n": len(tr), "gate": ST.gate(len(tr)),
                "by_side": {sd: sum(1 for t in tr if t.side == sd) for sd in ("LONG", "SHORT")},
                "families": Y.family_table(tr),
                "s3_minus_h60": Y.paired(tr, "S3", "H60", f"{tf}|{cohort}|S3-H60"),
                "s3_minus_i": Y.paired(tr, "S3", "I", f"{tf}|{cohort}|S3-I"),
                "exit_reasons_s3": _count([t.exits["S3"]["why"] for t in tr]),
                "median_target_dist_atr": ST.median(np.array([abs(t.target - t.price) / t.atr for t in tr])),
                "median_inval_dist_atr": ST.median(np.array([abs(t.price - t.inval) / t.atr for t in tr]))}
        raw["q6"][cohort] = {"n": len(tr), "S3-H60": cell["s3_minus_h60"], "S3-I": cell["s3_minus_i"]}
        cell["hold_states"], raw["hold"][cohort] = _hold_study(run, tr, der, f"{tf}|{cohort}")
        cell["entry_context"], raw["entry"][cohort] = _entry_study(run, tr, der, f"{tf}|{cohort}")
        cohorts[cohort] = cell
    report["cohorts"] = cohorts
    return report, raw


def _hold_study(run, trades, der, label) -> tuple[dict, dict]:
    rows, tid = [], []
    for k, t in enumerate(trades):
        for r in Y.hold_rows(run, t, der):
            rows.append(r)
            tid.append(k)
    tid = np.array(tid)
    out, raw = {}, {}
    for st in Y.HOLD_STATES:
        vals = np.array([r[st] for r in rows], dtype=object)
        known = np.array([v is not None for v in vals], bool)
        tr = np.array([v is True for v in vals], bool) & known
        fa = np.array([v is False for v in vals], bool) & known
        n_t = int(len(set(tid[tr].tolist()))) if len(tid) else 0
        n_f = int(len(set(tid[fa].tolist()))) if len(tid) else 0
        cell = {"trades_with_true": n_t, "trades_with_false": n_f, "gate": ST.gate(min(n_t, n_f))}
        for oc in ("fwd5_bp", "fwd_end_bp"):
            v = np.array([r[oc] for r in rows], float)
            cell[f"{oc}_true_minus_false"] = (ST.mean_diff(v, tr, fa, tid, f"{label}|hold|{st}|{oc}")
                                              if n_t and n_f else ST.summarize(float("nan"), np.array([])))
            raw[f"{st}|{oc}"] = {"n_min": min(n_t, n_f), **cell[f"{oc}_true_minus_false"]}
        out[st] = cell
    return out, raw


def _entry_study(run, trades, der, label) -> tuple[dict, dict]:
    feats = [Y.entry_features(run, t, der) for t in trades]
    net = np.array([t.exits["S3"]["net_bp"] for t in trades], float)
    blocks = np.array([t.entry // Y.HMAX for t in trades])
    out, raw = {}, {}
    for f in Y.ENTRY_FEATURES:
        v = [x.get(f) for x in feats]
        a = np.array([x is True for x in v], bool)
        b = np.array([x is False for x in v], bool)
        cell = {"n_true": int(a.sum()), "n_false": int(b.sum()), "gate": ST.gate(int(min(a.sum(), b.sum()))),
                "mean_s3_net_true": ST.mean(net[a]), "mean_s3_net_false": ST.mean(net[b]),
                "true_minus_false": (ST.mean_diff(net, a, b, blocks, f"{label}|entry|{f}")
                                     if a.any() and b.any() else ST.summarize(float("nan"), np.array([])))}
        out[f] = cell
        raw[f] = {"n_min": int(min(a.sum(), b.sum())), **cell["true_minus_false"]}
    dist = np.array([x.get("target_dist_atr") or np.nan for x in feats], float)
    out["target_distance_bins"] = [
        {"bin_atr": [lo, None if np.isinf(hi) else hi], "n": int(((dist >= lo) & (dist < hi)).sum()),
         "mean_s3_net": ST.mean(net[(dist >= lo) & (dist < hi)])}
        for lo, hi in zip(Y.DIST_BINS[:-1], Y.DIST_BINS[1:])]
    return out, raw


def _count(xs) -> dict:
    out: dict = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _utc(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# --------------------------------------------------------------------------- C1J (Binance)

def load_k1c() -> B.Series:
    rows = [json.loads(line) for line in open(K1C_PATH)]
    rows.sort(key=lambda r: r["timestamp_ms"])
    ts = np.array([r["timestamp_ms"] for r in rows], np.int64)
    if not np.array_equal(np.diff(ts), np.full(len(ts) - 1, B.MINUTE_MS)):
        raise RuntimeError("K1C grid has a gap")
    g = lambda k: np.array([float(r[k]) for r in rows])
    return B.Series("1m", B.MINUTE_MS, ts, g("open"), g("high"), g("low"), g("close"), g("volume"),
                    g("turnover"))


def c1j_study() -> dict:
    ctx = W.load_context(C1J_ROOT)
    walls = W.load_walls(C1J_ROOT)
    W.attach_end_states(walls, ctx)
    W.attach_touches(walls, ctx)
    t0, t1 = int(ctx.t[0]), int(ctx.t[-1])
    out: dict = {"window_utc": [_utc(t0), _utc(t1)], "hours": round((t1 - t0) / 3.6e6, 2),
                 "context_rows": len(ctx.t), "v2_walls": len(walls),
                 "lifecycle": W.lifecycle_table(walls),
                 "end_state_outcomes": W.end_state_outcomes(walls, ctx),
                 "interaction": W.interaction_classes(walls, ctx)}
    k1c = load_k1c()
    rel: dict = {"15s": {"status": "NOT_AVAILABLE",
                         "reason": "no Binance 15 s bars inside C1J (the collector stores no trades)"}}
    trades_out: dict = {}
    for tf in ("1m", "10m", "1h", "4h", "1d"):
        s = B.aggregate(k1c, tf) if tf != "1m" else k1c
        if len(s) < B.WARMUP_BARS + Y.HMAX + 60:
            # Contract s2 warm-up is 1 440 bars; K1C (Binance, ~31 days) cannot warm 1h and up.
            rel[tf] = {"status": "NOT_EVALUABLE", "reason": f"K1C has {len(s)} {tf} bars, fewer than "
                       f"the 1 440-bar warm-up plus a horizon", "gate": "INSUFFICIENT"}
            continue
        run = S.run(s)
        eps = [e for e in Y.episodes(run) if e.kind == "REAL"
               and t0 <= int(run.ts[e.bar]) and int(run.known_ms[e.bar]) <= t1]
        rows = []
        for e in eps:
            c = Y.classify(run, e, Y.PRIMARY_H)
            if c is None:
                continue
            side = "ASK" if e.side == "RES" else "BID"
            near = [w for w in W.open_walls_at(walls, side, int(run.ts[e.bar]))
                    if abs(w.price - e.level) / e.level * 1e4 <= W.APPROACH_BP]
            rows.append((int(run.ts[e.bar]) // W.BLOCK_MS, bool(near), float(c["hold"])))
        arr = np.array(rows, float) if rows else np.zeros((0, 3))
        n_w, n_n = int((arr[:, 1] == 1).sum()), int((arr[:, 1] == 0).sum())
        neff = int(len(set(arr[:, 0].tolist())))
        rel[tf] = {"episodes_in_window": len(rows), "with_near_wall": n_w, "without": n_n,
                   "n_eff_blocks": neff, "gate": ST.gate(neff),
                   "reaction_with_minus_without": (ST.mean_diff(arr[:, 2], arr[:, 1] == 1, arr[:, 1] == 0,
                                                                arr[:, 0], f"c1j|rel|{tf}")
                                                   if n_w and n_n else ST.summarize(float("nan"), np.array([])))}
        if tf == "1m":
            trades_out = _c1j_trades(run, eps, walls, ctx, t0, t1)
    out["timeframe_wall_relevance"] = rel
    out["trades_1m"] = trades_out
    return out


def _c1j_trades(run, eps, walls, ctx, t0, t1) -> dict:
    """C1J-only families B, D, E and hold states g, h on 1m Binance cohort trades."""
    trades = [t for t in Y.cohort_entries(run, eps)
              if t0 <= int(run.known_ms[t.entry]) and int(run.known_ms[t.entry + Y.HMAX]) <= t1]
    rows = []
    for t in trades:
        t.exits = Y.simulate(run, t, None)
        d = 1 if t.side == "LONG" else -1
        E = t.price
        last = t.entry + Y.HMAX
        km = run.known_ms
        # B: nearest opposite-side wall at entry as the target
        opp = W.open_walls_at(walls, "ASK" if d == 1 else "BID", int(km[t.entry]))
        opp = [w for w in opp if (w.price > E if d == 1 else w.price < E)]
        tgt = min(opp, key=lambda w: abs(w.price - E)).price if opp else None
        b = None
        if tgt is not None:
            for k in range(t.entry + 1, last + 1):
                if (run.high[k] >= tgt) if d == 1 else (run.low[k] <= tgt):
                    b = (k, tgt); break
        b = b or (last, float(run.close[last]))
        # D: own-side nearest wall ends CONSUMED_CANDIDATE while flow is against the position
        own = "BID" if d == 1 else "ASK"
        dd = None
        for w in sorted((w for w in walls if w.side == own and w.end_state == "CONSUMED_CANDIDATE"
                         and w.close_ms and km[t.entry] < w.close_ms <= km[last]), key=lambda w: w.close_ms):
            i = ctx.index_at(w.close_ms)
            if i < 0 or not np.isfinite(ctx.buy60[i]):
                continue
            against = ctx.buy60[i] < 0.5 if d == 1 else ctx.buy60[i] > 0.5
            if against:
                k = int(np.searchsorted(run.ts, w.close_ms, side="right")) - 1
                dd = (k, float(run.close[k])); break
        dd = dd or (last, float(run.close[last]))
        # E: 60 s flow side flips against the position (sampled at bar closes)
        ee = None
        prev_against = None
        for k in range(t.entry, last + 1):
            i = ctx.index_at(int(km[k]))
            f = ctx.buy60[i] if i >= 0 else np.nan
            against = None if not np.isfinite(f) or f == 0.5 else ((f < 0.5) if d == 1 else (f > 0.5))
            if k > t.entry and against and prev_against is False:
                ee = (k, float(run.close[k])); break
            if against is not None:
                prev_against = against
        ee = ee or (last, float(run.close[last]))
        for fam, (k, px) in (("B", b), ("D", dd), ("E", ee)):
            gross = d * (px - E) / E * 1e4
            t.exits[fam] = {"net_bp": gross - Y.COST_BP, "hold_bars": int(k - t.entry), "exit_bar": int(k)}
        rows.append(t)
    fams = ("A", "B", "C", "S3", "D", "E", "H60", "I")
    table = {}
    for fam in fams:
        net = np.array([t.exits[fam]["net_bp"] for t in rows], float)
        table[fam] = {"n": len(rows), "gate": ST.gate(len(rows)), "mean_net_bp": ST.mean(net),
                      "median_net_bp": ST.median(net), "win_rate": ST.rate(net > 0)}
    return {"trades": len(rows), "by_cohort_side": _count([f"{t.cohort}|{t.side}" for t in rows]),
            "families": table,
            "note": "hold states g/h and wall/flow entry features need n>=50 trades per arm; "
                    "this window cannot supply them, so they are not tested"}


# --------------------------------------------------------------------------- backfill (s13)

def backfill() -> dict:
    rows = []
    c1j = None
    if C1J_ROOT.exists():
        ctx_t = W.load_context(C1J_ROOT).t
        c1j = (int(ctx_t[0]), int(ctx_t[-1]))
    for line in open(LEDGER_COPY):
        r = json.loads(line)
        if r.get("event_type") == "POSITION_OPEN":
            rows.append(("PAPER_BTC_MANUAL_ACCOUNT", int(r["ts_ms"])))
    live = json.load(open(LIVE_FILLS_COPY))
    for f in live.get("fills") or []:
        rows.append(("BINANCE_LIVE_FILL", int(f.get("time") or f.get("time_ms"))))
    out = []
    for src, ms in sorted(rows, key=lambda x: x[1]):
        if c1j and c1j[0] <= ms <= c1j[1]:
            label = "CONTEXT_AVAILABLE"
        elif ms < B.K1_END_MS:
            label = "PARTIAL"
        else:
            label = "NOT_AVAILABLE"
        out.append({"source": src, "entry_utc": _utc(ms), "context": label})
    return {"rows": out, "counts": _count([r["context"] for r in out]),
            "note": "PARTIAL means K1 structure + D1 derivatives only (contract s13). Every BTC manual "
                    "entry on record is after K1 ends (2026-09-23) and outside C1J."}


# --------------------------------------------------------------------------- verdict

def holm_block(raw_all: dict) -> dict:
    """Holm across each pre-registered family of tests; returns the adjusted p per key."""
    out: dict = {}
    fams = {"q1": [], "q6": [], "hold": [], "entry": []}
    for tf, raw in raw_all.items():
        for side, r in raw["q1"].items():
            fams["q1"].append((f"{tf}|{side}", r.get("p_two_sided"), r, None))
        for cohort, r in raw["q6"].items():
            for k in ("S3-H60", "S3-I"):
                fams["q6"].append((f"{tf}|{cohort}|{k}", r[k].get("p_two_sided"), r[k], r["n"]))
        for cohort, hs in raw["hold"].items():
            for k, r in hs.items():
                fams["hold"].append((f"{tf}|{cohort}|{k}", r.get("p_two_sided"), r, r["n_min"]))
        for cohort, es in raw["entry"].items():
            for k, r in es.items():
                fams["entry"].append((f"{tf}|{cohort}|{k}", r.get("p_two_sided"), r, r["n_min"]))
    for name, items in fams.items():
        adj = ST.holm([p for _, p, _, _ in items])
        out[name] = {key: {"point": r.get("point"), "ci95": r.get("ci95"), "p": p, "p_holm": a, "n": n}
                     for (key, p, r, n), a in zip(items, adj)}
    return out


def verdict(tfs: dict, holm: dict) -> dict:
    sig = lambda c: c["p_holm"] is not None and c["p_holm"] < 0.05
    relevant = []
    for key, c in holm["q1"].items():
        tf, side = key.split("|")
        n = tfs[tf]["reaction"][side][f"h{Y.PRIMARY_H}"]["REAL"]["n"]
        if n >= 100 and sig(c) and c["point"] > 0:
            relevant.append(key)
    superior = []
    for tf, rep in tfs.items():
        for cohort, cell in rep["cohorts"].items():
            a = holm["q6"][f"{tf}|{cohort}|S3-H60"]
            b = holm["q6"][f"{tf}|{cohort}|S3-I"]
            if cell["n"] >= 100 and sig(a) and sig(b) and a["point"] > 0 and b["point"] > 0:
                superior.append(f"{tf}|{cohort}")
    hold = [k for k, c in holm["hold"].items() if c["n"] is not None and c["n"] >= 50 and sig(c)]
    entry = [k for k, c in holm["entry"].items() if c["n"] is not None and c["n"] >= 50 and sig(c)]
    reach50 = any(rep["cohorts"][c]["n"] >= 50 for rep in tfs.values() for c in rep["cohorts"])
    if not reach50:
        letter = "A. NOT_ENOUGH_DATA"
    elif superior or hold:
        letter = "D. HOLD_EXIT_CANDIDATES_FOUND"
    elif entry or relevant:
        letter = "C. ENTRY_CONTEXT_CANDIDATES_FOUND"
    else:
        letter = "B. MANUAL_CONTEXT_R0_PASS"
    return {"structure_relevant": relevant, "structure_exit_superior": superior,
            "hold_candidates": hold, "entry_context_candidates": entry,
            "verdict_before_preview": letter,
            "note": "E requires C or D plus a built preview; it is assigned outside this file."}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--tfs", default=",".join(B.TIMEFRAMES))
    ap.add_argument("--skip-c1j", action="store_true")
    a = ap.parse_args(argv)
    k1 = B.load_k1()
    der = B.load_derivatives()
    tfs, raws = {}, {}
    for tf in a.tfs.split(","):
        tfs[tf], raws[tf] = tf_study(tf, k1, der)
    holm = holm_block(raws)
    result = {"contract": CONTRACT, "provenance": provenance(), "timeframes": tfs, "holm": holm,
              "verdict": verdict(tfs, holm)}
    if not a.skip_c1j:
        result["c1j"] = c1j_study()
        result["backfill"] = backfill()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1, sort_keys=True, default=_json) + "\n")
    print(a.out, result["verdict"]["verdict_before_preview"])
    return 0


def _json(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, np.bool_):
        return bool(o)
    raise TypeError(type(o))


if __name__ == "__main__":
    raise SystemExit(main())
