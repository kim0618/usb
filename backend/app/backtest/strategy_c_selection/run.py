"""C-P3/C-P4 run: raw cache -> panel -> PIT features -> candidates -> labels -> statistics -> gate.

Artifacts go to a local run directory named by the run identity (rules checksum, raw input
digest, code digest, windows). Publishing to the shared workspace is a separate, explicit step.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
import gzip
import hashlib
import io
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from app.backtest.engine.identity import code_digest, package_files, run_identity, source_provenance
from app.backtest.strategy_c_selection import evaluate
from app.backtest.strategy_c_selection.features import FEATURE_COLUMNS, compute
from app.backtest.strategy_c_selection.labels import compute_labels
from app.backtest.strategy_c_selection.panel import BENCHMARK, Panel, load_panel
from app.backtest.strategy_c_selection.pit_audit import audit, audit_dates
from app.backtest.strategy_c_selection.raw_fetch import grouped_path, splits_path, tickers_path
from app.backtest.strategy_c_selection.rules import REPO_ROOT, SelectionRules, Variant

IDENTITY_NAMESPACE = "strategy-c-selection-run"
RUN_ID_PREFIX = "cmsel1"
RESULT_SCHEMA = "strategy-c-selection-result-v1"
CODE_PACKAGE = Path(__file__).resolve().parent
PRIMARY_START = 251
SECONDARY_START = 60
FORWARD = 10
PIT_AUDIT_DATES = 12


@dataclass(frozen=True)
class RunInputs:
    raw_root: Path
    sessions: tuple[date, ...]
    snapshot_dates: tuple[date, ...]
    split_range: tuple[date, date]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw_digest(inputs: RunInputs) -> str:
    digest = hashlib.sha256()
    files = [grouped_path(inputs.raw_root, s) for s in inputs.sessions]
    files += [tickers_path(inputs.raw_root, d) for d in inputs.snapshot_dates]
    files.append(splits_path(inputs.raw_root, *inputs.split_range))
    for path in files:
        digest.update(f"{path.relative_to(inputs.raw_root)}\t{_sha256(path)}\n".encode())
    return digest.hexdigest()


def usable_sessions(root: Path, sessions: Sequence[date]) -> tuple[tuple[date, ...], list[str]]:
    """Leading sessions outside the plan window are dropped; an internal gap is refused."""
    status = []
    for session in sessions:
        with gzip.open(grouped_path(root, session), "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        status.append((session, "body" in payload, payload.get("error")))
    first = next(i for i, (_, ok, _) in enumerate(status) if ok)
    dropped = [f"{s.isoformat()}:{err}" for s, ok, err in status[:first]]
    tail = status[first:]
    gaps = [s.isoformat() for s, ok, _ in tail if not ok]
    if gaps:
        raise RuntimeError(f"grouped daily missing inside the grid: {gaps}")
    return tuple(s for s, _, _ in tail), dropped


def data_audit(panel: Panel, dropped: list[str]) -> dict:
    close, high, low, open_, volume = panel.close, panel.high, panel.low, panel.open, panel.volume
    present = ~np.isnan(close)
    with np.errstate(invalid="ignore"):
        ohlc_bad = present & ((high < low) | (high < np.fmax(open_, close) - 1e-9)
                              | (low > np.fmin(open_, close) + 1e-9))
    spy = panel.column(BENCHMARK)
    reverse = sum(1 for e in panel.splits if e.is_reverse)
    in_panel = set(panel.tickers)
    return {
        "sessions": len(panel.sessions), "first_session": panel.sessions[0].isoformat(),
        "last_session": panel.sessions[-1].isoformat(), "dropped_leading_sessions": dropped,
        "tickers_in_panel": len(panel.tickers),
        "ticker_rows_per_session": {"min": int(present.sum(axis=1).min()),
                                    "median": float(np.median(present.sum(axis=1))),
                                    "max": int(present.sum(axis=1).max())},
        "ohlc_inconsistent_rows": int(ohlc_bad.sum()),
        "zero_volume_rows": int((present & (volume <= 0)).sum()),
        "spy_sessions_present": int(present[:, spy].sum()),
        "snapshots": {d.isoformat(): len(v) for d, v in sorted(panel.snapshots.items())},
        "splits_total": len(panel.splits), "splits_reverse": reverse,
        "splits_on_panel_tickers": sum(1 for e in panel.splits if e.ticker in in_panel),
    }


def single_condition_variants(rules: SelectionRules) -> list[Variant]:
    """Descriptive only (interpretation Q3/Q4): each declared condition alone, same thresholds."""
    seen: dict[tuple, Variant] = {}
    for variant in rules.variants:
        for condition in variant.conditions:
            history = "m2" if condition.feature == "distance_52w_high" else "base"
            key = (condition.feature, condition.operator, condition.threshold, history)
            seen.setdefault(key, Variant(f"single:{condition.feature}{condition.operator}{condition.threshold}",
                                         "descriptive single condition", history, (condition,)))
    return list(seen.values())


def _json_default(value: object) -> object:
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    raise TypeError(type(value))


def _clean(obj: object) -> object:
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def dump_json(payload: object) -> bytes:
    return (json.dumps(_clean(payload), ensure_ascii=False, indent=2, sort_keys=True,
                       default=_json_default, allow_nan=False) + "\n").encode("utf-8")


def candidate_table(panel: Panel, features, labels, rules: SelectionRules, window: Sequence[int],
                    variants: Sequence[Variant], window_name: str) -> pd.DataFrame:
    frames = []
    for variant in variants:
        cand = features.candidates(variant)
        in_window = np.zeros(cand.shape[0], dtype=bool)
        in_window[list(window)] = True
        ii, jj = np.nonzero(cand & in_window[:, None])
        frame = pd.DataFrame({"variant": variant.name, "window": window_name,
                              "signal_date": [panel.sessions[i].isoformat() for i in ii],
                              "available_at": [f"{panel.sessions[i + 1].isoformat()}T04:00 America/New_York"
                                               if i + 1 < len(panel.sessions) else None for i in ii],
                              "ticker": [panel.tickers[j] for j in jj]})
        for name in FEATURE_COLUMNS:
            frame[name] = features.values[name][ii, jj]
        frame["reason_codes"] = ["|".join(f"{c.feature}{c.operator}{c.threshold}" for c in variant.conditions)] \
            * len(ii)
        frame["label_no_entry_bar"] = labels.no_entry_bar[ii, jj]
        frame["label_ca_suspect"] = labels.label_ca_suspect[ii, jj]
        for k in labels.horizons:
            frame[f"label_valid_{k}"] = labels.valid(k)[ii, jj]
            frame[f"mfe_{k}"] = labels.mfe[k][ii, jj]
            frame[f"mae_{k}"] = labels.mae[k][ii, jj]
            frame[f"close_return_{k}"] = labels.close_return[k][ii, jj]
            frame[f"close_return_{k}_from_d_close"] = labels.close_return_from_d_close[k][ii, jj]
            frame[f"time_to_mfe_{k}"] = labels.time_to_mfe[k][ii, jj]
            frame[f"time_to_mae_{k}"] = labels.time_to_mae[k][ii, jj]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True).sort_values(["variant", "signal_date", "ticker"],
                                                            ignore_index=True)


def feature_table(panel: Panel, features, window: Sequence[int]) -> pd.DataFrame:
    """Raw features of every base-eligible ticker-date (hard filter + base history, CA not excluded)."""
    mask = features.member & features.has_bar & features.base_history & features.hard_filter
    in_window = np.zeros(mask.shape[0], dtype=bool)
    in_window[list(window)] = True
    ii, jj = np.nonzero(mask & in_window[:, None])
    frame = pd.DataFrame({"signal_date": [panel.sessions[i].isoformat() for i in ii],
                          "ticker": [panel.tickers[j] for j in jj]})
    for name in FEATURE_COLUMNS:
        frame[name] = features.values[name][ii, jj]
    frame["m2_history"] = features.m2_history[ii, jj]
    frame["ca_excluded"] = features.ca_excluded[ii, jj]
    return frame.sort_values(["signal_date", "ticker"], ignore_index=True)


def table_digest(frame: pd.DataFrame) -> str:
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False, float_format="%.17g", lineterminator="\n")
    return hashlib.sha256(buffer.getvalue().encode("utf-8")).hexdigest()


def execute(inputs: RunInputs, rules: SelectionRules, out_root: Path, *, log=print) -> dict:
    started = time.perf_counter()
    sessions, dropped = usable_sessions(inputs.raw_root, inputs.sessions)
    effective = RunInputs(inputs.raw_root, sessions, inputs.snapshot_dates, inputs.split_range)
    identity = run_identity(IDENTITY_NAMESPACE, RUN_ID_PREFIX, [
        f"rules_checksum={rules.checksum}",
        f"raw_digest={raw_digest(effective)}",
        f"code_digest={code_digest(package_files(CODE_PACKAGE), root=REPO_ROOT)}",
        f"sessions={sessions[0].isoformat()}..{sessions[-1].isoformat()}:{len(sessions)}",
        f"primary_start_idx={PRIMARY_START}", f"secondary_start_idx={SECONDARY_START}", f"forward={FORWARD}",
        f"bootstrap={evaluate.BLOCK_LENGTH}:{evaluate.REPLICATES}:{evaluate.SEED}",
    ])
    log(f"run_id={identity.run_id} sessions={len(sessions)} dropped={dropped}")
    panel = load_panel(inputs.raw_root, sessions, inputs.snapshot_dates, rules.allowed_exchanges,
                       inputs.split_range)
    log(f"panel shape={panel.shape} splits={len(panel.splits)} load_s={time.perf_counter() - started:.1f}")
    audit_data = data_audit(panel, dropped)
    features = compute(panel, rules)
    features_again = compute(panel, rules)
    labels = compute_labels(panel, rules.horizons, rules.ca_ratio)
    t = len(sessions)
    primary = list(range(PRIMARY_START, t - FORWARD))
    secondary = list(range(SECONDARY_START, t - FORWARD))
    log(f"windows primary={sessions[primary[0]]}..{sessions[primary[-1]]} ({len(primary)}) "
        f"secondary={sessions[secondary[0]]}..{sessions[secondary[-1]]} ({len(secondary)})")

    pit = audit(panel, features, rules, audit_dates(secondary, PIT_AUDIT_DATES), seed=evaluate.SEED)
    log(f"pit violations={pit['violations']} positive_control={pit['positive_control_detected']} "
        f"elapsed_s={time.perf_counter() - started:.1f}")

    variants = list(rules.variants)
    candidates_primary = candidate_table(panel, features, labels, rules, primary, variants, "primary")
    candidates_secondary = candidate_table(panel, features, labels, rules, secondary,
                                           [v for v in variants if v.history == "base"], "secondary")
    candidates_again = candidate_table(panel, features_again, labels, rules, primary, variants, "primary")
    digest_primary = table_digest(candidates_primary)
    determinism = {"candidate_digest_run1": digest_primary,
                   "candidate_digest_run2": table_digest(candidates_again),
                   "identical": digest_primary == table_digest(candidates_again)}
    pit_violations = int(pit["violations"]) + (0 if determinism["identical"] else 1) \
        + (0 if pit["positive_control_detected"] else 1)

    draws_primary = evaluate.block_indices(len(primary))
    draws_secondary = evaluate.block_indices(len(secondary))
    results: dict = {"primary": {}, "secondary": {}, "descriptive_single_conditions": {}}
    gates: dict = {}
    for variant in variants:
        rows = evaluate.build_rows(variant, features, labels, primary, rules, panel.tickers)
        summary = evaluate.summarize(rows, rules, primary, draws_primary)
        results["primary"][variant.name] = summary
        gates[variant.name] = evaluate.gate(summary, pit_violations)
        log(f"{variant.name} primary candidates={summary['candidates_total']} "
            f"matched={summary['matched_candidates_10']} lift={summary['hits'][evaluate.PRIMARY_HIT]['lift']}")
        if variant.history == "base":
            rows2 = evaluate.build_rows(variant, features, labels, secondary, rules, panel.tickers)
            results["secondary"][variant.name] = evaluate.summarize(rows2, rules, secondary, draws_secondary)
    for variant in single_condition_variants(rules):
        rows = evaluate.build_rows(variant, features, labels, primary, rules, panel.tickers)
        summary = evaluate.summarize(rows, rules, primary, draws_primary, levels=(0.95,))
        results["descriptive_single_conditions"][variant.name] = {
            "candidates_total": summary["candidates_total"],
            "matched_candidates_10": summary["matched_candidates_10"],
            "unique_tickers_10": summary["unique_tickers_10"],
            "primary_hit": summary["hits"][evaluate.PRIMARY_HIT],
            "close_10": summary["means"]["close_10"], "mae_10": summary["means"]["mae_10"],
            "mfe_10": summary["means"]["mfe_10"],
            "time_blocks_lift": [b["lift"] for b in summary["time_blocks"]]}
    decision = evaluate.overall_decision(gates)

    block_dates = [[sessions[int(b[0])].isoformat(), sessions[int(b[-1])].isoformat()]
                   for b in np.array_split(np.asarray(primary), evaluate.N_TIME_BLOCKS)]
    by_date = candidates_primary.groupby(["variant", "signal_date"]).size().rename("candidates").reset_index()
    by_ticker = candidates_primary.groupby(["variant", "ticker"]).size().rename("candidates").reset_index() \
        .sort_values(["variant", "candidates", "ticker"], ascending=[True, False, True])
    features_frame = feature_table(panel, features, secondary)
    summary = {
        "run_id": identity.run_id, "run_identity": identity.digest, "identity_lines": list(identity.lines),
        "result_schema": RESULT_SCHEMA, "rules_checksum": rules.checksum,
        "not_implemented_variants": rules.not_implemented, "data_audit": audit_data,
        "windows": {"primary": [sessions[primary[0]].isoformat(), sessions[primary[-1]].isoformat(), len(primary)],
                    "secondary": [sessions[secondary[0]].isoformat(), sessions[secondary[-1]].isoformat(),
                                  len(secondary)],
                    "primary_time_blocks": block_dates},
        "determinism": determinism, "pit_violations_total": pit_violations,
        "results": results, "gates": gates, "decision": f"GATE-C2 = {decision}",
        "digests": {"candidates_primary": digest_primary,
                    "candidates_secondary": table_digest(candidates_secondary),
                    "features": table_digest(features_frame)},
        "provenance": source_provenance(REPO_ROOT, [CODE_PACKAGE]),
        "elapsed_seconds": round(time.perf_counter() - started, 1),
    }
    out = out_root / identity.run_id
    out.mkdir(parents=True, exist_ok=True)
    candidates_primary.to_parquet(out / "candidates_primary.parquet", index=False)
    candidates_secondary.to_parquet(out / "candidates_secondary.parquet", index=False)
    features_frame.to_parquet(out / "features_base_eligible.parquet", index=False)
    by_date.to_csv(out / "candidate_count_by_date.csv", index=False)
    by_ticker.to_csv(out / "candidate_count_by_ticker.csv", index=False)
    (out / "pit_audit.json").write_bytes(dump_json(pit))
    (out / "summary.json").write_bytes(dump_json(summary))
    (out / "rules.json").write_bytes((REPO_ROOT / "docs/backtest/strategy_c/c_m_selection_rules_v1.json").read_bytes())
    log(f"decision=GATE-C2 = {decision} out={out} elapsed_s={summary['elapsed_seconds']}")
    return summary

