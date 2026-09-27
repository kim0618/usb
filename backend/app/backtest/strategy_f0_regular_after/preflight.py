"""Static validation of the F0 declaration before (and after) the freeze. Reads no market data.

Every check reads the declaration only. The checks restate the frozen contract as assertions,
so a later edit that drops or loosens a declared element is caught even before the checksum is.
"""

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any

from app.backtest.strategy_f0_regular_after.config import RULES_PATH, canonical_checksum

REGULAR_START, REGULAR_END = 570, 960  # 09:30 inclusive, 16:00 exclusive (minutes, ET)
ENTRY_MINUTE = 965                      # 16:05
FEATURES = ("day_return", "regular_RVOL", "regular_dollar_volume", "close_vs_VWAP",
            "position_in_day_range", "distance_to_day_high", "return_1500_1600",
            "return_1530_1600", "return_1545_1600", "volume_1500_1600", "last30m_volume_share",
            "last15m_return", "last15m_volume_share", "relative_strength_vs_SPY")
HYPOTHESES = {
    "H1": [["day_return", ">=", 0.03], ["position_in_day_range", ">=", 0.8]],
    "H2": [["return_1530_1600", ">=", 0.01], ["close_vs_VWAP", ">=", 0.005]],
    "H3": [["regular_RVOL", ">=", 2.0], ["position_in_day_range", ">=", 0.8]],
    "H4": [["relative_strength_vs_SPY", ">=", 0.02], ["return_1530_1600", ">=", 0.005],
           ["regular_dollar_volume", ">=", 20000000.0]],
    "H5": [["day_return", ">=", 0.05], ["regular_RVOL", ">=", 2.0],
           ["position_in_day_range", ">=", 0.9], ["return_1530_1600", ">=", 0.01]],
}
#: Tokens that would mean a feature reads after-hours, the 16:00 bar, the outcome or D's daily row.
FORBIDDEN_INPUT = re.compile(r"post|after|ah:|16:0|1605|m=96|m>=96|exit|entry|mfe|mae|return_label"
                             r"|daily:D:|daily:D\+|official", re.IGNORECASE)
BRACKET = re.compile(r"\[(\d+),(\d+)\)")


def _check(results: list, name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def validate(payload: Mapping[str, Any]) -> list[dict]:
    r: list[dict] = []
    first = canonical_checksum(payload)
    again = canonical_checksum(json.loads(json.dumps(payload, ensure_ascii=False)))
    _check(r, "canonical_checksum_deterministic", first == again == canonical_checksum(payload), first)

    decl = payload.get("declaration", {})
    _check(r, "frozen_before_declared", str(decl.get("declared_before", "")).startswith("F0_RESULT_EXECUTION"))

    window = payload.get("research_window", {})
    sessions = window.get("primary_sessions", [])
    _check(r, "research_window_104_sessions",
           len(sessions) == 104 == window.get("primary_session_count") and sessions == sorted(set(sessions))
           and sessions[:1] == ["2026-04-20"] and sessions[-1:] == ["2026-09-16"])
    _check(r, "long_history_diagnostic_only",
           window.get("long_history_sample", {}).get("can_change_verdict") is False)

    uni = payload.get("universe", {})
    crit = uni.get("criteria_as_frozen_in_contract", {})
    _check(r, "universe_contract_unchanged",
           uni.get("contract") == "STRATEGY_E_TRADING_UNIVERSE_V1_1" and uni.get("thresholds_changed_for_f") is False
           and crit.get("min_close_D_minus_1") == 5.0 and crit.get("min_median_dollar_volume") == 5_000_000.0
           and crit.get("daily_window_sessions") == 20 and crit.get("min_present_sessions") == 15)

    cut = payload.get("information_cutoff", {})
    _check(r, "feature_cutoff_155959", cut.get("feature_cutoff_et") == "15:59:59"
           and "POSTMARKET" in cut.get("bar_1600_classification", ""))

    defs = payload.get("features", {}).get("definitions", {})
    _check(r, "feature_set_complete", sorted(defs) == sorted(FEATURES) and len(defs) == 14, ",".join(defs))
    for name, spec in defs.items():
        lo, hi = spec.get("window", [None, None])
        inputs = spec.get("inputs", [])
        bad = [i for i in inputs if FORBIDDEN_INPUT.search(i)]
        spans = [(int(a), int(b)) for i in inputs for a, b in BRACKET.findall(i)]
        ok = (isinstance(lo, int) and isinstance(hi, int) and REGULAR_START <= lo < hi <= REGULAR_END
              and not bad and all(REGULAR_START <= a < b <= REGULAR_END for a, b in spans)
              and not FORBIDDEN_INPUT.search(spec.get("formula", "").replace("ALIAS", "")))
        _check(r, f"no_future_field:{name}", ok, f"window={lo},{hi} bad={bad}")

    hyps = payload.get("hypotheses", {})
    for key, expected in HYPOTHESES.items():
        got = hyps.get(key, {}).get("all")
        _check(r, f"hypothesis_thresholds:{key}", got == expected
               and all(term[0] in defs for term in got or []), json.dumps(got))

    ex = payload.get("execution", {})
    entry = ex.get("primary_entry", {})
    exit_ = ex.get("primary_exit", {})
    _check(r, "primary_entry_defined", "m == 965" in entry.get("eligibility", "")
           and entry.get("price") == "open of the 16:05 bar" and entry.get("if_missing") == "NO_TRADE")
    _check(r, "primary_exit_defined", exit_.get("target_minute") == 1020 and exit_.get("lookback_minutes") == 10
           and exit_.get("window_minutes") == [1010, 1020] and "UNRESOLVED_EXIT" in ex.get("exit_rule", "")
           and "exit_age_seconds" in ex)
    sec = ex.get("secondary_horizons", {})
    _check(r, "secondary_horizons_defined", {k: v.get("target_minute") for k, v in sec.items()}
           == {"H1630": 990, "H1800": 1080, "H2000": 1200} and ex.get("secondary_can_change_verdict") is False)

    g = payload.get("gates", {})
    s, st, t = g.get("sample", {}), g.get("statistical", {}), g.get("tail", {})
    _check(r, "sample_gate", (s.get("min_trades"), s.get("min_unique_symbols"), s.get("min_unique_sessions")) == (300, 100, 60))
    _check(r, "bootstrap_seed_and_iterations", isinstance(st.get("seed"), int) and not isinstance(st.get("seed"), bool)
           and st.get("iterations") == 5000 and "session-cluster" in st.get("bootstrap", "")
           and "99%" in st.get("pass_condition", ""), str(st.get("seed")))
    _check(r, "tail_gate", (t.get("min_absolute_lift"), t.get("min_relative_lift")) == (0.02, 1.5)
           and t.get("primary") == "P(MFE>=0.03)")
    mm, dn = g.get("mean_median", {}), g.get("downside", {})
    _check(r, "mean_median_gate", mm.get("mean_lift_gross") == "> 0" and mm.get("min_median_gross_return") == -0.0025)
    _check(r, "downside_gate", (dn.get("fail_if_absolute_worsening_at_least"), dn.get("fail_if_ratio_above")) == (0.02, 1.5))
    er = g.get("extreme_removal", {})
    _check(r, "extreme_removal_gate", er.get("removals") == ["top1", "top5", "top1pct"] and "after top1pct" in er.get("pass_condition", ""))
    c = g.get("concentration", {})
    _check(r, "concentration_gate", (c.get("max_top1_share"), c.get("max_top5_share")) == (0.15, 0.4) and "leave_top10_tickers" in c)
    tc = g.get("time_consistency", {})
    _check(r, "time_consistency_gate", (tc.get("blocks"), tc.get("min_blocks_mean_lift_positive"), tc.get("min_blocks_tail_lift_positive")) == (4, 3, 3))
    cost = g.get("cost", {})
    _check(r, "cost_grid", cost.get("round_trip_bp_grid") == [10, 20, 30, 50, 75, 100] and cost.get("primary_bp") == 50
           and "break_even_cost_bp" in cost and "cost stress assumption" in cost.get("label", ""))
    _check(r, "pit_audits_declared", g.get("pit", {}).get("required") == ["PIT-1", "PIT-2", "PIT-3"]
           and all(k in payload.get("pit_audits", {}) for k in ("PIT-1", "PIT-2", "PIT-3")))

    v = payload.get("verdict", {})
    _check(r, "verdict_contract", v.get("vocabulary") == ["PASS", "INCONCLUSIVE", "FAIL"]
           and v.get("borderline") == "NOT USED" and all(k in v for k in ("PASS", "INCONCLUSIVE", "FAIL")))
    _check(r, "event_not_in_signal", cut.get("sec_news_in_signal") is False
           and "UNKNOWN_EVENT_STATUS" in payload.get("event_diagnostic", {}).get("unknown", ""))
    return r


def validate_file(path: Path = RULES_PATH) -> list[dict]:
    return validate(json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    results = validate_file()
    for item in results:
        print(("PASS " if item["ok"] else "FAIL ") + item["check"] + (f"  {item['detail']}" if item["detail"] else ""))
    failed = [i for i in results if not i["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} passed")
    raise SystemExit(1 if failed else 0)
