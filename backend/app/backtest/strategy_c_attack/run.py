"""C-ATTACK-V0 run: frozen C-M0 -> verified M_ONLY -> TP/SL trades vs matched controls -> gate.

Hard stops, in order: the C-M0 candidate table must reproduce the frozen baseline digest; the
M_ONLY source must match the counts C-E0 published (else BLOCKED, nothing simulated); the sample
counts are decided before any trade is simulated (else INCONCLUSIVE). ``engineering_only`` runs
the simulator for the PIT/determinism checks and timing but writes no economics at all.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.backtest.engine.identity import code_digest, package_files, run_identity, source_provenance
from app.backtest.strategy_c_attack import cohort, metrics
from app.backtest.strategy_c_attack.rules import AttackRules, Pair
from app.backtest.strategy_c_attack.simulate import (AMBIGUOUS, STATE_NAMES, Prices, SL_FIRST, TP_FIRST,
                                                     simulate, simulate_one)
from app.backtest.strategy_c_e0 import stats
from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import load_panel
from app.backtest.strategy_c_selection.run import (RunInputs, candidate_table, dump_json, raw_digest,
                                                   table_digest, usable_sessions)
from app.backtest.strategy_c_selection.rules import REPO_ROOT, SelectionRules

IDENTITY_NAMESPACE = "strategy-c-attack-run"
RUN_ID_PREFIX = "catk0"
RESULT_SCHEMA = "strategy-c-attack-result-v1"
CODE_PACKAGE = Path(__file__).resolve().parent
PRIMARY_START = 251
BASE_VARIANT = "C-M0"
M_ONLY = "M_ONLY"
PIT_SAMPLE = 400
LEVELS = (0.95, 0.99)


class BaselineMismatch(RuntimeError):
    """The C-M0 candidate table does not reproduce the frozen baseline digest."""


@dataclass(frozen=True)
class AttackInputs:
    raw_root: Path
    sessions: tuple[date, ...]
    snapshot_dates: tuple[date, ...]
    split_range: tuple[date, date]
    status_path: Path | None
    status_source: str = "A"  # A = C-E0 byte copy, B = recomputed from an EDGAR store


# ---------------------------------------------------------------------------------------------
# Trades


def _trade_frame(prices: Prices, rows: pd.DataFrame, pair: Pair, horizon: int, rules: AttackRules, *,
                 conservative: bool = True, haircut: float | None = None, cost_scale: float = 1.0) -> pd.DataFrame:
    trades = simulate(prices, rows["date_idx"].to_numpy(), rows["ticker_idx"].to_numpy(), pair.tp, pair.sl,
                      horizon, conservative=conservative,
                      haircut=rules.delist_haircut if haircut is None else haircut)
    cost = rules.cost.round_trip(rows["price_bucket"].to_numpy(), rows["adv20_bucket"].to_numpy(),
                                 trades.stop_exit) * cost_scale
    out = rows[["date_idx", "ticker_idx", "ticker", "cell", "group", "status"]].copy()
    out["entry"], out["exit_price"], out["exit_idx"] = trades.entry, trades.exit_price, trades.exit_idx
    out["state"], out["gap"], out["stop_exit"] = trades.state, trades.gap, trades.stop_exit
    out["late_exit"], out["delisted"], out["missing_sessions"] = trades.late_exit, trades.delisted, \
        trades.missing_sessions
    out["gross"] = trades.gross
    out["cost"] = cost
    out["net"] = out["gross"] - cost
    return out


def _attach_base(trades: pd.DataFrame, min_controls: int) -> pd.DataFrame:
    """Candidate trades with their cell's control means (net and exit-state rates)."""
    controls = trades[trades["group"] == "control"]
    ctl = controls.assign(tp=(controls["state"] == TP_FIRST).astype(float),
                          sl=(controls["state"] == SL_FIRST).astype(float),
                          amb=(controls["state"] == AMBIGUOUS).astype(float))
    base = ctl.groupby("cell").agg(base_net=("net", "mean"), base_gross=("gross", "mean"),
                                   base_tp=("tp", "mean"), base_sl=("sl", "mean"), base_amb=("amb", "mean"),
                                   control_count=("net", "size"))
    cand = trades[trades["group"] == "candidate"].join(base, on="cell")
    cand["control_count"] = cand["control_count"].fillna(0).astype(int)
    cand["matched"] = cand["control_count"] >= min_controls
    return cand


def _h1(frame: pd.DataFrame, dates: Sequence[int], draws: np.ndarray | None) -> dict:
    if frame.empty:
        return {"n": 0, "point": float("nan")}
    grouped = frame.groupby("date_idx")["net"]
    value = grouped.sum().reindex(list(dates), fill_value=0.0).to_numpy(dtype=float)
    count = grouped.size().reindex(list(dates), fill_value=0).to_numpy(dtype=float)
    series = stats.Series(value, np.zeros_like(value), count)
    out = {"n": int(count.sum()), "point": series.point}
    if draws is not None:
        out.update(stats.percentiles(series.boot(draws), LEVELS))
    return out


def _h2(matched: pd.DataFrame, dates: Sequence[int], draws: np.ndarray | None) -> dict:
    out = stats.excess(matched, dates, "net", draws, LEVELS)
    return out


def _control_rates(matched: pd.DataFrame) -> dict:
    if matched.empty:
        return {}
    return {"n_candidates_matched": int(len(matched)),
            "control_net_mean": float(matched["base_net"].mean()),
            "control_gross_mean": float(matched["base_gross"].mean()),
            "control_tp_first_rate": float(matched["base_tp"].mean()),
            "control_sl_first_rate": float(matched["base_sl"].mean()),
            "control_ambiguous_rate": float(matched["base_amb"].mean()),
            "controls_per_candidate_median": float(matched["control_count"].median())}


def _blocks(cand: pd.DataFrame, blocks: Sequence[Sequence[int]], sleeves: int) -> list[dict]:
    out = []
    for number, block in enumerate(blocks, start=1):
        part = cand[cand["date_idx"].isin(list(block))]
        matched = part[part["matched"]]
        summary = metrics.summarize(part, sleeves) if len(part) else {"n": 0}
        out.append({"block": number, "dates": [int(block[0]), int(block[-1])], "n": int(len(part)),
                    "net_expectancy": summary.get("expectancy"), "profit_factor": summary.get("profit_factor"),
                    "tp_first_rate": summary.get("tp_first_rate"), "sl_first_rate": summary.get("sl_first_rate"),
                    "sleeve_max_drawdown": summary.get("sleeve_max_drawdown"),
                    "total_net_pnl_units": float(part["net"].sum()),
                    "n_matched": int(len(matched)),
                    "h2_excess": float((matched["net"] - matched["base_net"]).mean()) if len(matched) else None})
    return out


def _conditions(cand: pd.DataFrame, dates: Sequence[int], blocks: Sequence[Sequence[int]], rules: AttackRules,
                draws: np.ndarray | None) -> dict:
    """Return-based gate conditions 1-5, 8, 9 on one simulated candidate frame (with bases)."""
    matched = cand[cand["matched"]]
    h1 = _h1(cand, dates, draws)
    h2 = _h2(matched, dates, draws)
    summary = metrics.summarize(cand, rules.SLEEVES)
    block_rows = _blocks(cand, blocks, rules.SLEEVES)
    best = max(block_rows, key=lambda b: b["total_net_pnl_units"])
    without_best = cand[~cand["date_idx"].isin(list(blocks[best["block"] - 1]))]
    by_ticker = cand.groupby("ticker")["net"].sum().sort_values(ascending=False)
    top5_tickers = list(by_ticker.head(5).index)
    without_top5 = cand[~cand["ticker"].isin(top5_tickers)]
    top10 = metrics.top_contribution(cand["net"], 10)
    pf = summary["profit_factor"]
    mdd = summary["sleeve_max_drawdown"]
    ci = h2.get("ci9500")
    conditions = {
        "1_h1_net_expectancy_point": bool(h1["point"] > 0),
        "2_h2_excess_point": bool(h2["point"] > 0),
        "3_h2_excess_ci95_low": bool(ci is not None and ci[0] > 0),
        "4_h3_profit_factor": bool(pf is not None and pf > rules.PF_MIN),
        "5_h4_sleeve_mdd": bool(mdd is not None and mdd <= rules.MDD_MAX),
        "8_time_stability": bool(sum(1 for b in block_rows if (b["h2_excess"] or 0) > 0) >= 3
                                 and len(without_best) and without_best["net"].mean() > 0),
        "9_concentration": bool(top10 is not None and top10 <= rules.TOP10_SHARE_MAX
                                and len(without_top5) and without_top5["net"].mean() > 0),
    }
    return {"conditions": conditions, "h1": h1, "h2": h2, "summary": summary, "time_blocks": block_rows,
            "leave_best_block_out": {"block": best["block"], "n": int(len(without_best)),
                                     "net_expectancy": float(without_best["net"].mean())
                                     if len(without_best) else None},
            "leave_top5_tickers_out": {"tickers": [str(t) for t in top5_tickers],
                                       "pnl_units": [float(by_ticker[t]) for t in top5_tickers],
                                       "n": int(len(without_top5)),
                                       "net_expectancy": float(without_top5["net"].mean())
                                       if len(without_top5) else None},
            "top10_contribution": top10}


# ---------------------------------------------------------------------------------------------
# PIT / engineering


def pit_audit(prices: Prices, rows: pd.DataFrame, pair: Pair, horizon: int, rules: AttackRules,
              seed: int) -> dict:
    """Differential (vectorised == scalar reference on the D+1.. slice), future-after-exit
    mutation (must not change), and a positive control (a crash on the entry bar must change)."""
    rng = np.random.default_rng(seed)
    pick = rows.iloc[np.sort(rng.choice(len(rows), size=min(PIT_SAMPLE, len(rows)), replace=False))]
    vec = simulate(prices, pick["date_idx"].to_numpy(), pick["ticker_idx"].to_numpy(), pair.tp, pair.sl,
                   horizon, haircut=rules.delist_haircut)
    t_len = prices.close.shape[0]
    report = {"sample": int(len(pick)), "differential_mismatch": 0, "future_mutation_changed": 0,
              "positive_control_changed": 0, "positive_control_eligible": 0, "examples": []}
    for k, (i, j) in enumerate(zip(pick["date_idx"].to_numpy(), pick["ticker_idx"].to_numpy())):
        sl_ = slice(i + 1, t_len)
        o, h, lo, c = (prices.open[sl_, j].copy(), prices.high[sl_, j].copy(), prices.low[sl_, j].copy(),
                       prices.close[sl_, j].copy())
        ref = simulate_one(o, h, lo, c, pair.tp, pair.sl, horizon, haircut=rules.delist_haircut)
        same = (np.isclose(ref[0], vec.exit_price[k], rtol=0, atol=1e-12) and ref[1] == vec.state[k]
                and i + 1 + ref[2] == vec.exit_idx[k])
        if not same:
            report["differential_mismatch"] += 1
            if len(report["examples"]) < 5:
                report["examples"].append({"date_idx": int(i), "ticker_idx": int(j), "ref": [float(ref[0]), int(ref[1]),
                                           int(ref[2])], "vec": [float(vec.exit_price[k]), int(vec.state[k]),
                                                                 int(vec.exit_idx[k])]})
        cut = ref[2] + 1
        if cut < len(c):
            scale = rng.uniform(0.2, 5.0, size=len(c) - cut)
            o2, h2, l2, c2 = o.copy(), h.copy(), lo.copy(), c.copy()
            o2[cut:] *= scale
            h2[cut:] *= scale * 3
            l2[cut:] *= scale / 3
            c2[cut:] *= scale
            again = simulate_one(o2, h2, l2, c2, pair.tp, pair.sl, horizon, haircut=rules.delist_haircut)
            if again[:3] != ref[:3] and not (np.isclose(again[0], ref[0]) and again[1:3] == ref[1:3]):
                report["future_mutation_changed"] += 1
        if not (ref[2] == 0 and ref[1] in (SL_FIRST, AMBIGUOUS)):
            report["positive_control_eligible"] += 1
            l3 = lo.copy()
            l3[0] = o[0] * 0.01
            crash = simulate_one(o, h, l3, c, pair.tp, pair.sl, horizon, haircut=rules.delist_haircut)
            if crash[:3] != ref[:3]:
                report["positive_control_changed"] += 1
    report["positive_control_detected"] = report["positive_control_changed"] > 0 \
        and report["positive_control_changed"] == report["positive_control_eligible"]
    report["violations"] = report["differential_mismatch"] + report["future_mutation_changed"]
    return report


def _digest(frame: pd.DataFrame) -> str:
    cols = ["date_idx", "ticker", "exit_idx", "state", "exit_price", "net"]
    return table_digest(frame[cols].sort_values(["date_idx", "ticker"], kind="mergesort"))


# ---------------------------------------------------------------------------------------------
# Execute


def _decision(cons: dict, opt: dict | None, sample_ok: bool, pit_ok: bool, ambiguous_share: float,
              rules: AttackRules) -> tuple[str, dict]:
    conditions = dict(cons["conditions"])
    conditions["6_gap_aware"] = True
    conditions["7_conservative_ambiguity"] = True
    conditions["10_sample"] = sample_ok
    conditions["11_pit"] = pit_ok
    if not sample_ok or not pit_ok:
        return "INCONCLUSIVE", conditions
    if all(conditions.values()):
        return "PASS", conditions
    if opt is not None and ambiguous_share > rules.AMBIGUOUS_SHARE_INCONCLUSIVE and all(opt["conditions"].values()):
        return "INCONCLUSIVE", conditions
    return "FAIL", conditions


def execute(inputs: AttackInputs, rules: AttackRules, c_rules: SelectionRules, out_root: Path, *,
            engineering_only: bool = False, log=print) -> dict:
    started = time.perf_counter()
    sessions, _ = usable_sessions(inputs.raw_root, inputs.sessions)
    effective = RunInputs(inputs.raw_root, sessions, inputs.snapshot_dates, inputs.split_range)
    digest_raw = raw_digest(effective)
    panel = load_panel(inputs.raw_root, sessions, inputs.snapshot_dates, c_rules.allowed_exchanges,
                       inputs.split_range)
    features = compute(panel, c_rules)
    labels = compute_labels(panel, c_rules.horizons, c_rules.ca_ratio)
    t_len = len(sessions)
    primary = list(range(PRIMARY_START, t_len - rules.horizon))
    log(f"panel={panel.shape} primary={sessions[primary[0]]}..{sessions[primary[-1]]} ({len(primary)}) "
        f"load_s={time.perf_counter() - started:.1f}")

    table = candidate_table(panel, features, labels, c_rules, primary, list(c_rules.variants), "primary")
    table_d = table_digest(table)
    if table_d != rules.baseline_digest:
        raise BaselineMismatch(f"candidate digest {table_d} != frozen {rules.baseline_digest}")
    base_rows = table[table["variant"] == BASE_VARIANT][["signal_date", "ticker"]].reset_index(drop=True)
    log(f"baseline reproduced digest={table_d[:12]} {BASE_VARIANT}_rows={len(base_rows)}")
    del table

    verdict = cohort.verify(inputs.status_path, base_rows, rules.published_status_counts,
                           inputs.status_source, rules.c_e0_run_id if inputs.status_source == "A" else None)
    log(f"m_only source: {verdict.reason}")
    source_line = verdict.sha256 if verdict.accepted else "UNVERIFIED"
    identity = run_identity(IDENTITY_NAMESPACE, RUN_ID_PREFIX, [
        f"rules_checksum={rules.checksum}", f"c_m_rules_checksum={c_rules.checksum}",
        f"raw_digest={digest_raw}", f"c_m_candidates_digest={table_d}",
        f"m_only_source_sha256={source_line}",
        f"code_digest={code_digest(package_files(CODE_PACKAGE), root=REPO_ROOT)}",
        f"sessions={sessions[0].isoformat()}..{sessions[-1].isoformat()}:{t_len}",
        f"engineering_only={engineering_only}",
    ])
    summary: dict = {
        "run_id": identity.run_id, "run_identity": identity.digest, "identity_lines": list(identity.lines),
        "result_schema": RESULT_SCHEMA, "rules_checksum": rules.checksum, "c_m_rules_checksum": c_rules.checksum,
        "raw_digest": digest_raw, "c_m_candidates_digest": table_d, "c_m0_rows": int(len(base_rows)),
        "m_only_source": {"accepted": verdict.accepted, "reason": verdict.reason, "path": verdict.source,
                          "sha256": verdict.sha256, "counts": verdict.counts},
        "windows": {"primary": [sessions[primary[0]].isoformat(), sessions[primary[-1]].isoformat(), len(primary)]},
        "engineering_only": engineering_only,
        "provenance": source_provenance(REPO_ROOT, [CODE_PACKAGE]),
    }
    if not verdict.accepted and not engineering_only:
        summary["decision"] = f"GATE-C-ATTACK-V0 = NOT_REACHED ({cohort.BLOCKED})"
        summary["outcomes_computed"] = False
        return _write(out_root, identity.run_id, summary, None, started, log)

    variant = next(v for v in c_rules.variants if v.name == BASE_VARIANT)
    rows = evaluate.build_rows(variant, features, labels, primary, c_rules, panel.tickers)
    keep = ["date_idx", "ticker_idx", "ticker", "candidate", "close", "price_bucket", "atr_bucket",
            "adv20_bucket", "cell", "no_entry_bar", "label_ca_suspect"]
    rows = rows[keep].copy()
    rows["signal_date"] = [sessions[i].isoformat() for i in rows["date_idx"]]
    if verdict.accepted:
        status = verdict.status
    else:  # engineering only: every C-M0 candidate stands in; no economics are written
        status = base_rows.assign(status=M_ONLY)
    rows = rows.merge(status, on=["signal_date", "ticker"], how="left", validate="many_to_one")
    rows["group"] = np.where(rows["candidate"], "candidate", "control")
    rows["status"] = rows["status"].fillna("CONTROL")
    if rows.loc[rows["candidate"], "status"].eq("CONTROL").any():
        raise RuntimeError("a C-M0 candidate row has no C-E0 status")
    candidate_cells = set(rows.loc[rows["candidate"], "cell"])
    rows = rows[rows["candidate"] | rows["cell"].isin(candidate_cells)].reset_index(drop=True)
    rows["trade_valid"] = ~rows["no_entry_bar"] & ~rows["label_ca_suspect"]
    del features, labels

    cand_all = rows[rows["candidate"]]
    m_only = cand_all[cand_all["status"] == M_ONLY]
    censor = {g: {"rows": int(len(f)), "no_entry_bar": int(f["no_entry_bar"].sum()),
                  "label_ca_suspect": int((~f["no_entry_bar"] & f["label_ca_suspect"]).sum()),
                  "trade_valid": int(f["trade_valid"].sum())}
              for g, f in (("m_only", m_only), ("c_m0_all", cand_all), ("controls", rows[~rows["candidate"]]))}
    trade_rows = rows[rows["trade_valid"] & ((rows["group"] == "control") | (rows["status"] == M_ONLY))]
    control_valid = rows[rows["trade_valid"] & ~rows["candidate"]]
    cell_counts = control_valid.groupby("cell").size()
    m_valid = trade_rows[trade_rows["group"] == "candidate"]
    matched_n = int((m_valid["cell"].map(cell_counts).fillna(0) >= rules.min_controls).sum())
    sample = {"trade_valid_candidates": int(len(m_valid)), "matched_candidates": matched_n,
              "unique_tickers": int(m_valid["ticker"].nunique())}
    sample_ok = sample["trade_valid_candidates"] >= rules.MIN_TRADES and matched_n >= rules.MIN_MATCHED \
        and sample["unique_tickers"] >= rules.MIN_TICKERS
    summary.update({"censoring": censor, "sample": sample, "sample_ok": sample_ok})
    log(f"censoring={censor} sample={sample} ok={sample_ok}")

    prices = Prices.from_panel(panel)
    pair = rules.primary_pair
    all_trade_rows = rows[rows["trade_valid"]]
    pit = pit_audit(prices, all_trade_rows, pair, rules.horizon, rules, seed=rules.BOOTSTRAP[2])
    first = _trade_frame(prices, all_trade_rows, pair, rules.horizon, rules)
    second = _trade_frame(prices, all_trade_rows, pair, rules.horizon, rules)
    determinism = {"digest_1": _digest(first), "digest_2": _digest(second)}
    determinism["identical"] = determinism["digest_1"] == determinism["digest_2"]
    pit_violations = int(pit["violations"]) + (0 if pit["positive_control_detected"] else 1) \
        + (0 if determinism["identical"] else 1)
    summary.update({"pit_audit": pit, "determinism": determinism, "pit_violations_total": pit_violations})
    log(f"pit violations={pit_violations} differential={pit['differential_mismatch']} "
        f"future={pit['future_mutation_changed']} positive_control={pit['positive_control_detected']} "
        f"deterministic={determinism['identical']} elapsed_s={time.perf_counter() - started:.1f}")
    if engineering_only:
        summary["decision"] = "ENGINEERING_ONLY (no economics computed or written)"
        summary["outcomes_computed"] = False
        return _write(out_root, identity.run_id, summary, None, started, log)
    if not sample_ok:
        summary["decision"] = "GATE-C-ATTACK-V0 = INCONCLUSIVE (sample)"
        summary["outcomes_computed"] = False
        return _write(out_root, identity.run_id, summary, None, started, log)

    result, trades = outcomes(prices, rows, trade_rows, rules, primary, t_len, sample_ok,
                              pit_violations == 0, log=log)
    result["windows_time_blocks"] = [[sessions[b[0]].isoformat(), sessions[b[-1]].isoformat()]
                                     for b in stats.time_blocks(primary)]
    summary.update(result)
    return _write(out_root, identity.run_id, summary, trades, started, log)


def outcomes(prices: Prices, rows: pd.DataFrame, trade_rows: pd.DataFrame, rules: AttackRules,
             primary: Sequence[int], t_len: int, sample_ok: bool, pit_ok: bool, *, log=print) -> tuple[dict, pd.DataFrame]:
    """Every return-based number of the run. ``rows`` holds candidates (with C-E0 status) and the
    controls of candidate cells; ``trade_rows`` is its trade-valid M_ONLY + control subset."""
    pair = rules.primary_pair
    blocks = stats.time_blocks(primary)
    block_len, replicates, seed = rules.BOOTSTRAP
    draws = stats.block_indices(len(primary), replicates=replicates, seed=seed, block=block_len)

    def run_pair(p: Pair, conservative: bool, *, horizon: int | None = None, haircut: float | None = None,
                 cost_scale: float = 1.0, source: pd.DataFrame = trade_rows) -> pd.DataFrame:
        frame = _trade_frame(prices, source, p, horizon or rules.horizon, rules, conservative=conservative,
                             haircut=haircut, cost_scale=cost_scale)
        return _attach_base(frame, rules.min_controls)

    pairs: dict = {}
    keep_trades = []
    primary_cons = primary_opt = None
    for p in rules.pairs:
        for conservative in (True, False):
            cand = run_pair(p, conservative)
            result = _conditions(cand, primary, blocks, rules, draws)
            result["loss_tail"] = metrics.loss_tail(cand, p.sl)
            result["control"] = _control_rates(cand[cand["matched"]])
            result["unmatched_candidates"] = int((~cand["matched"]).sum())
            key = f"{p.name}:{'conservative' if conservative else 'optimistic'}"
            pairs[key] = {"tp": p.tp, "sl": p.sl, "primary": p.name == pair.name, **result}
            keep_trades.append(cand.assign(pair=p.name, ordering="conservative" if conservative else "optimistic"))
            if p.name == pair.name:
                if conservative:
                    primary_cons = (cand, result)
                else:
                    primary_opt = (cand, result)
            log(f"pair {key} n={result['summary']['n']} done")
    cand_c, cons = primary_cons
    _, opt = primary_opt
    ambiguous_share = float((cand_c["state"] == AMBIGUOUS).mean())

    sensitivity = {}
    for name, kwargs in (("gross", {"cost_scale": 0.0}), ("cost_2x", {"cost_scale": 2.0}),
                         ("delist_haircut_0", {"haircut": 0.0}), ("delist_haircut_100", {"haircut": 1.0})):
        cand = run_pair(pair, True, **kwargs)
        matched = cand[cand["matched"]]
        sensitivity[name] = {"h1": _h1(cand, primary, None)["point"],
                             "h2": _h2(matched, primary, draws),
                             "profit_factor": metrics.profit_factor(cand["net"])}
    ca_rows = rows[~rows["no_entry_bar"] & rows["label_ca_suspect"]
                   & ((rows["group"] == "control") | (rows["status"] == M_ONLY))]
    ca_cand = ca_rows[ca_rows["group"] == "candidate"]
    if len(ca_cand):
        ca_frame = _trade_frame(prices, ca_cand, pair, rules.horizon, rules)
        worst = -pair.sl - rules.cost.round_trip(ca_cand["price_bucket"].to_numpy(),
                                                 ca_cand["adv20_bucket"].to_numpy(), np.ones(len(ca_cand), bool))
        ca_frame["net"] = np.minimum(ca_frame["net"].to_numpy(), worst)
        stressed = pd.concat([cand_c[["date_idx", "net"]], ca_frame[["date_idx", "net"]]])
        sensitivity["ca_suspect_included_worst"] = {"n_added": int(len(ca_frame)),
                                                    "h1": float(stressed["net"].mean())}
    else:
        sensitivity["ca_suspect_included_worst"] = {"n_added": 0, "h1": cons["h1"]["point"]}
    horizons = {}
    for k in rules.secondary_horizons:
        cand = run_pair(pair, True, horizon=k)
        horizons[f"{k}D"] = {"summary": metrics.summarize(cand, rules.SLEEVES),
                             "h2": _h2(cand[cand["matched"]], primary, None)}

    secondary_rows = rows[rows["trade_valid"]]
    secondary = {}
    for p in rules.pairs:
        cand = run_pair(p, True, source=secondary_rows)
        secondary[p.name] = {"summary": metrics.summarize(cand, rules.SLEEVES),
                             "h2": _h2(cand[cand["matched"]], primary, draws if p.name == pair.name else None),
                             "by_status_expectancy": {str(s): {"n": int(len(g)), "net_mean": float(g["net"].mean())}
                                                      for s, g in cand.groupby("status")}}

    attack = None
    if cons["conditions"]["1_h1_net_expectancy_point"] and cons["conditions"]["3_h2_excess_ci95_low"]:
        attack = metrics.attack_portfolio(cand_c, pair.sl, rules.ATTACK_RISK)
    decision, conditions = _decision(cons, opt, sample_ok, pit_ok, ambiguous_share, rules)
    result = {
        "outcomes_computed": True, "primary_pair": pair.name, "pairs": pairs,
        "primary_ambiguous_share": ambiguous_share, "sensitivity": sensitivity,
        "secondary_horizons": horizons, "secondary_all_c_m0": secondary,
        "concurrency": metrics.concurrency(cand_c, t_len), "attack_portfolio": attack,
        "gate": {"conditions": conditions,
                 "optimistic_conditions": opt["conditions"],
                 "decision": decision},
        "decision": f"GATE-C-ATTACK-V0 = {decision}",
        "state_names": {int(k): v for k, v in STATE_NAMES.items()},
    }
    return result, pd.concat(keep_trades, ignore_index=True)


def _write(out_root: Path, run_id: str, summary: dict, trades: pd.DataFrame | None, started: float, log) -> dict:
    summary["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    out = out_root / run_id
    out.mkdir(parents=True, exist_ok=True)
    if trades is not None:
        trades.to_parquet(out / "candidate_trades.parquet", index=False)
    (out / "summary.json").write_bytes(dump_json(summary))
    (out / "rules.json").write_bytes((REPO_ROOT / "docs/backtest/strategy_c_attack/c_attack_rules_v1.json")
                                     .read_bytes())
    log(f"{summary.get('decision')} out={out} elapsed_s={summary['elapsed_seconds']}")
    return summary
