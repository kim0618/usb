"""Every number F0 runs on, parsed from the frozen declaration and cross-checked.

The declaration states some values in prose (``"m == 965"``, ``"P(900)"``, ``"t-10 <= m < t"``).
They are parsed here, once, and each parsed value is checked against the structured fields that
state the same thing. Any disagreement raises ``F0HardFail``: the code never falls back to a
constant of its own.
"""

from dataclasses import dataclass
import math
import re
from typing import Any, Mapping

from app.backtest.strategy_f0_regular_after.config import F0HardFail, F0Rules

FEATURE_NAMES = ("day_return", "regular_RVOL", "regular_dollar_volume", "close_vs_VWAP",
                 "position_in_day_range", "distance_to_day_high", "return_1500_1600",
                 "return_1530_1600", "return_1545_1600", "volume_1500_1600",
                 "last30m_volume_share", "last15m_return", "last15m_volume_share",
                 "relative_strength_vs_SPY")


def _one(pattern: str, text: str, what: str) -> tuple[str, ...]:
    found = re.findall(pattern, text)
    if len(found) != 1:
        raise F0HardFail(f"cannot parse {what} from declaration text {text!r}")
    value = found[0]
    return value if isinstance(value, tuple) else (value,)


@dataclass(frozen=True)
class Params:
    sessions: tuple[str, ...]
    reg_start: int
    reg_end: int
    close_minute: int
    prevailing_lookback: int
    anchors: Mapping[str, int]            # feature -> anchor minute t of P(t)
    dv_windows: Mapping[str, tuple[int, int]]
    rvol_window: int
    rvol_minimum: int
    entry_minute: int
    exit_target: int
    exit_lookback: int
    secondary_targets: Mapping[str, int]
    diag_entry_minute: int
    hypotheses: Mapping[str, tuple[tuple[str, str, float], ...]]
    positive_tails: tuple[float, ...]
    negative_tails: tuple[float, ...]
    mfe_thresholds: tuple[float, ...]
    primary_mfe: float
    sample: Mapping[str, int]
    bootstrap_iterations: int
    bootstrap_seed: int
    tail_abs: float
    tail_rel: float
    median_floor: float
    downside_threshold: float
    downside_abs: float
    downside_ratio: float
    extreme_pct: float
    conc_top1: float
    conc_top5: float
    conc_leave: int
    blocks: int
    blocks_mean: int
    blocks_tail: int
    cost_grid: tuple[int, ...]
    cost_primary: int
    break_even_min: float
    underpowered_half_width: float
    min_resolved_sessions: int
    quantiles: tuple[float, ...]
    match_edges: Mapping[str, tuple[float | None, ...]]
    pit1_seed: int
    pit2_seed: int
    pit2_count: int
    alias: Mapping[str, str]


def from_rules(rules: F0Rules) -> Params:
    raw: Mapping[str, Any] = rules.raw
    rs = raw["regular_session"]
    reg_start, reg_end = (int(x) for x in rs["feature_window_minutes"])
    close_minute = int(_one(r"m == (\d+)", rs["regular_close_proxy"], "regular close minute")[0])
    if close_minute != reg_end - 1:
        raise F0HardFail("regular close proxy is not the last regular minute")
    defs = raw["features"]["definitions"]
    if tuple(sorted(defs)) != tuple(sorted(FEATURE_NAMES)):
        raise F0HardFail("declared feature set differs from the implemented one")
    notation = raw["features"]["notation"]
    lookback = int(_one(r"t-(\d+) <= m < t\b", notation, "prevailing lookback")[0])
    anchors = {}
    for name in ("return_1500_1600", "return_1530_1600", "return_1545_1600"):
        t = int(_one(r"P\((\d+)\)", defs[name]["formula"], name)[0])
        lo, hi = defs[name]["window"]
        if lo != t - lookback or hi != reg_end:
            raise F0HardFail(f"{name}: window {lo},{hi} disagrees with P({t}) and lookback {lookback}")
        anchors[name] = t
    dv_windows = {}
    for name in ("volume_1500_1600", "last30m_volume_share", "last15m_volume_share"):
        a, b = _one(r"(\d+) <= m < (\d+)", defs[name]["formula"], name)
        dv_windows[name] = (int(a), int(b))
    if tuple(defs["volume_1500_1600"]["window"]) != dv_windows["volume_1500_1600"]:
        raise F0HardFail("volume_1500_1600 window disagrees with its formula")
    rvol = defs["regular_RVOL"]
    if rvol["denominator_floor_session"] != raw["research_window"]["primary_first_session"]:
        raise F0HardFail("RVOL floor session is not the first primary session")
    alias = {"last15m_return": defs["last15m_return"]["alias_of"]}

    ex = raw["execution"]
    entry = int(_one(r"m == (\d+)", ex["primary_entry"]["eligibility"], "entry minute")[0])
    pe = ex["primary_exit"]
    target, lookback_exit = int(pe["target_minute"]), int(pe["lookback_minutes"])
    if list(pe["window_minutes"]) != [target - lookback_exit, target]:
        raise F0HardFail("primary exit window disagrees with target and lookback")
    secondary = {}
    for key, spec in ex["secondary_horizons"].items():
        t = int(spec["target_minute"])
        if list(spec["window_minutes"]) != [t - lookback_exit, t]:
            raise F0HardFail(f"secondary {key} window disagrees with the exit rule")
        secondary[key] = t
    diag_entry = int(_one(r"m == (\d+)", ex["secondary_decision_diagnostic"]["entry"], "16:15 entry")[0])

    hyps = {}
    for key, spec in raw["hypotheses"].items():
        if key.startswith("H") and isinstance(spec, Mapping):
            terms = []
            for feature, op, value in spec["all"]:
                if op != ">=" or feature not in defs:
                    raise F0HardFail(f"{key}: unsupported term {feature} {op}")
                terms.append((feature, op, float(value)))
            hyps[key] = tuple(terms)

    om = raw["outcome_metrics"]
    primary_mfe = float(_one(r"P\(MFE >= \+([\d.]+)\)", om["primary_tail_endpoint"], "primary endpoint")[0])
    g = raw["gates"]
    st = g["statistical"]
    if "session-cluster" not in st["bootstrap"]:
        raise F0HardFail("bootstrap is not the declared session-cluster bootstrap")
    tail = g["tail"]
    if float(_one(r"MFE>=([\d.]+)", tail["primary"], "tail primary")[0]) != primary_mfe:
        raise F0HardFail("tail gate endpoint differs from the primary endpoint")
    dn = g["downside"]
    downside_threshold = -float(_one(r"<= -([\d.]+)\)", dn["metric"], "downside metric")[0])
    er = g["extreme_removal"]
    pct = float(_one(r"ceil\(([\d.]+) \* H trades\)", er["top1pct_count"], "top1pct")[0])
    c = g["concentration"]
    leave = int(_one(r"drop the (\d+) tickers", c["leave_top10_tickers"], "leave-top-N")[0])
    tc = g["time_consistency"]
    cost = g["cost"]
    be_min = float(_one(r"break_even_cost_bp >= (\d+)", cost["pass_condition"], "break-even")[0])
    v = raw["verdict"]
    under = next(x for x in v["per_hypothesis_order"] if x.startswith("UNDERPOWERED"))
    half = float(_one(r"half-width of tail_lift is >= ([\d.]+)", under, "underpowered half-width")[0])
    min_sessions = int(_one(r"fewer than (\d+) sessions", v["INCONCLUSIVE"], "resolved sessions")[0])
    q = tuple(float(x) for x in _one(r"\[([\d., ]+)\]", raw["single_feature_analysis"]["buckets"],
                                     "quantiles")[0].split(","))
    edges = {k: tuple(None if e is None else float(e) for e in spec["edges"])
             for k, spec in raw["matched_control"]["cell_variables"].items()}
    pit = raw["pit_audits"]
    pit1 = int(_one(r"seed (\d+)", pit["PIT-1"]["action"], "PIT-1 seed")[0])
    pit2 = int(_one(r"seed (\d+)", pit["PIT-2"]["action"], "PIT-2 seed")[0])
    lin = _one(r"linspace\((\d+), (\d+), (\d+)\)", pit["PIT-2"]["audit_sessions"], "PIT-2 sessions")
    sessions = tuple(raw["research_window"]["primary_sessions"])
    if int(lin[0]) != 0 or int(lin[1]) != len(sessions) - 1:
        raise F0HardFail("PIT-2 session span disagrees with the primary session list")
    if raw["research_window"]["early_close_sessions_in_window"]:
        raise F0HardFail("early-close exclusion is not implemented because none was declared")

    return Params(
        sessions=sessions, reg_start=reg_start, reg_end=reg_end, close_minute=close_minute,
        prevailing_lookback=lookback, anchors=anchors, dv_windows=dv_windows,
        rvol_window=int(rvol["denominator_window"]), rvol_minimum=int(rvol["denominator_minimum"]),
        entry_minute=entry, exit_target=target, exit_lookback=lookback_exit,
        secondary_targets=secondary, diag_entry_minute=diag_entry, hypotheses=hyps,
        positive_tails=tuple(float(x) for x in om["positive_tail_thresholds"]),
        negative_tails=tuple(float(x) for x in om["negative_tail_thresholds"]),
        mfe_thresholds=tuple(float(x) for x in om["mfe_thresholds"]), primary_mfe=primary_mfe,
        sample={"trades": int(g["sample"]["min_trades"]), "symbols": int(g["sample"]["min_unique_symbols"]),
                "sessions": int(g["sample"]["min_unique_sessions"])},
        bootstrap_iterations=int(st["iterations"]), bootstrap_seed=int(st["seed"]),
        tail_abs=float(tail["min_absolute_lift"]), tail_rel=float(tail["min_relative_lift"]),
        median_floor=float(g["mean_median"]["min_median_gross_return"]),
        downside_threshold=downside_threshold, downside_abs=float(dn["fail_if_absolute_worsening_at_least"]),
        downside_ratio=float(dn["fail_if_ratio_above"]), extreme_pct=pct,
        conc_top1=float(c["max_top1_share"]), conc_top5=float(c["max_top5_share"]), conc_leave=leave,
        blocks=int(tc["blocks"]), blocks_mean=int(tc["min_blocks_mean_lift_positive"]),
        blocks_tail=int(tc["min_blocks_tail_lift_positive"]),
        cost_grid=tuple(int(x) for x in cost["round_trip_bp_grid"]), cost_primary=int(cost["primary_bp"]),
        break_even_min=be_min, underpowered_half_width=half, min_resolved_sessions=min_sessions,
        quantiles=q, match_edges=edges, pit1_seed=pit1, pit2_seed=pit2, pit2_count=int(lin[2]),
        alias=alias)


def pit2_indices(p: Params) -> tuple[int, ...]:
    """``round(linspace(0, n-1, k))`` with numpy's round-half-to-even, as declared."""
    import numpy as np
    return tuple(int(x) for x in np.round(np.linspace(0, len(p.sessions) - 1, p.pit2_count)))


def top_pct_count(p: Params, n: int) -> int:
    return int(math.ceil(p.extreme_pct * n))
