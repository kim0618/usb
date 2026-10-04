"""H-V2-D5-D2R: the recent-regime window-selection repair, replayed offline over the stored corpus.

Offline, deterministic, 0 model calls, $0. One finding is being fixed and nothing else: D6 reused
D5-D2's valuation on thirteen issuers D4 had graded, and VRRM exposed a hole in the window rule.

The rule shortens the observation window when FULL_2Y is `TRENDING_STRONG`, and the trend test is a
rank correlation, which measures monotonicity. VRRM's EV/EBIT rose across the first half of the panel
(rho +0.830) and collapsed across the second (-0.918); the two cancelled to -0.522, the rule concluded
the panel had not drifted, and TP1 published at +404.7% from the median of a complete round trip.

**What changed, and the three things that did not.** The window set is unchanged (FULL_2Y,
RECENT_12M, RECENT_6M). The `TRENDING_STRONG` threshold is unchanged. Which window governs once a
regime change is established is unchanged - the shortest contract-eligible one. What changed is which
windows are asked whether a regime changed: V1 asked FULL_2Y and nothing else, D2R asks every
contract-eligible window, and when the strong drift is in a shorter window rather than in FULL_2Y it
additionally requires the two windows' observed Base multiples to disagree past the pre-registered
`VALUATION_CONFLICT_RATIO` before the shorter one governs. See `fair_value`'s
`RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL` and `RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT`.

**This is an A/B from one code path, not a rewrite.** Both contracts are selectable on
`select_contract_window`, so every issuer here is valued twice from the identical panel and the
identical arithmetic, and the only difference between the two rows is the selected window. The
historical D5-D2 and D6 reports are untouched and still reproduce: both runners pin
`WindowSelectionContract.D5_D2_V1`, which is what they ran under.

**No forward returns, no new issuers, no live calls.** The decision engine is re-run on the repaired
valuations to report whether any D6 decision would move, and D6's published report is not edited.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from app.backtest.strategy_h_v2.decision.d6_contract import (
    Decision,
    Eligibility,
    audit_decisions,
    decide,
)
from app.backtest.strategy_h_v2.valuation.fair_value import (
    RECENT_REGIME_CONFLICT_RATIO,
    RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL,
    RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT,
    PanelWindow,
    ValuationStatus,
    WindowSelectionContract,
    base_multiple_ratio,
)
from app.dev.run_strategy_h_v2_d5_d2 import (
    DECISION_SESSION,
    METHOD_JUDGEMENTS as D5_D2_JUDGEMENTS,
    PILOT_ISSUERS as D5_D2_ISSUERS,
    audit as d5_audit,
    build_panel,
    value_issuer,
)
from app.dev.run_strategy_h_v2_d4_1 import load_price_panel
from app.dev.run_strategy_h_v2_d6 import (
    D4_UNIVERSE,
    METHOD_JUDGEMENTS as D6_JUDGEMENTS,
    load_legs,
    project_d5,
)

D2R_CONTRACT = "h_v2_d5_d2r_recent_regime_window_repair_v1"
OUTPUT_DIR = Path("data/runtime/strategy_h_v2/d5_d2r")

OLD = WindowSelectionContract.D5_D2_V1
NEW = WindowSelectionContract.D5_D2R_V1

#: Every issuer with a stored valuation in this repository, and which step's declared judgement table
#: values it. The two sets are disjoint by construction - D5-D2's seven came from a seeded hash over
#: the package universe, D6's thirteen are D4's coverage - so the replay spans both samples rather
#: than re-running the one the rule was written on.
CORPUS: tuple[tuple[str, str], ...] = (
    tuple((t, "D5_D2") for t in D5_D2_ISSUERS) + tuple((t, "D6") for t in D4_UNIVERSE))

JUDGEMENTS_FOR: Mapping[str, Mapping[str, object]] = {
    "D5_D2": D5_D2_JUDGEMENTS, "D6": D6_JUDGEMENTS}

HISTORICAL_IS_IMMUTABLE = (
    "No published D5-D2 or D6 figure is edited, recomputed in place, or reclassified. VRRM's "
    "historical TP1 of +404.7% and its historical WATCH stand as what the contract in force at the "
    "time produced, and this step's replay is a separate artifact with its own contract label. That "
    "is not bookkeeping fussiness: a repository that silently improves its own past results cannot "
    "later tell which of its numbers were ever actually produced, and the D6 document's +404.7% is "
    "the evidence that the defect was real."
)

SELECTION_IS_NOT_UPSIDE_OPTIMIZATION = (
    "No window is selected by what it does to a fair value, a target price or an upside. The rule "
    "reads three things - whether a window is contract-eligible, its `TrendClass`, and the ratio "
    "between two windows' observed Base multiples - and all three are properties of the observed "
    "panel that exist before any scenario is built. The conflict test is symmetric in the ratio, so "
    "a recent regime twice as EXPENSIVE as the full panel overrides it on identical terms to one "
    "twice as cheap; a mirrored test fixture asserts it. The direction TP1 moves is a consequence of "
    "the selection, never an input to it."
)

NO_FORWARD_RETURNS = (
    "No price after the decision session is read by anything here, and no window is checked against "
    "what the issuer subsequently did. The repair is justified by what the panel shows about its own "
    "regimes, not by which window would have been right - a window chosen because it predicted "
    "better would make D7's forward shadow a test of this step's hindsight."
)


# -------------------------------------------------------------------------------------------------
# Replay
# -------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayRow:
    """One issuer valued twice from one panel, differing only in the selected window."""

    ticker: str
    sample: str
    status: str
    old_status: str
    method: str | None
    old_method: str | None
    old_window: str | None
    new_window: str | None
    changed: bool
    old_base_fv: float | None
    new_base_fv: float | None
    old_tp1: float | None
    new_tp1: float | None
    old_tp2: float | None
    new_tp2: float | None
    old_tp1_upside: float | None
    new_tp1_upside: float | None
    old_tp2_upside: float | None
    new_tp2_upside: float | None
    old_confidence: str
    new_confidence: str
    reason_window_changed: str
    window_trends: Mapping[str, str]
    base_multiple_ratio_full_vs_shortest: float | None

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker, "sample": self.sample, "status": self.status,
            "old_status": self.old_status, "method": self.method, "old_method": self.old_method,
            "old_window": self.old_window, "new_window": self.new_window, "changed": self.changed,
            "old_base_fv": self.old_base_fv, "new_base_fv": self.new_base_fv,
            "old_TP1": self.old_tp1, "new_TP1": self.new_tp1,
            "old_TP2": self.old_tp2, "new_TP2": self.new_tp2,
            "old_TP1_upside": self.old_tp1_upside, "new_TP1_upside": self.new_tp1_upside,
            "old_TP2_upside": self.old_tp2_upside, "new_TP2_upside": self.new_tp2_upside,
            "old_confidence": self.old_confidence, "new_confidence": self.new_confidence,
            "reason_window_changed": self.reason_window_changed,
            "window_trends": dict(self.window_trends),
            "base_multiple_ratio_full_vs_shortest": self.base_multiple_ratio_full_vs_shortest,
        }


def _targets(row: dict) -> dict:
    return row.get("target_prices") or {}


def _window_diagnostics(row: dict) -> tuple[dict[str, str], float | None]:
    """Each window's trend as the row published it, and the ratio the conflict test reads.

    Recomputed from the row's own `windows` block rather than from the panel, so what is reported is
    what the rule saw. The ratio is only meaningful between FULL_2Y and the shortest eligible window,
    which is the pair the override compares.
    """
    windows = row.get("windows") or {}
    trends = {name: stats["trend"] for name, stats in windows.items()}

    class _View:
        """Just enough of `WindowStats` for `base_multiple_ratio`, built from the published dict."""

        def __init__(self, stats: dict) -> None:
            base = stats.get("base_p50")
            self.base = type("_P", (), {"multiple": base["multiple"]})() if base else None

    eligible = {name: stats for name, stats in windows.items() if stats["contract_eligible"]}
    if PanelWindow.FULL_2Y.value not in eligible or not eligible:
        return trends, None
    shortest_name = min(eligible, key=lambda name: eligible[name]["n"])
    return trends, base_multiple_ratio(_View(eligible[PanelWindow.FULL_2Y.value]),
                                       _View(eligible[shortest_name]))


def replay() -> dict:
    """Value every stored-corpus issuer under both contracts, from one panel and one evaluator."""
    tickers = tuple(t for t, _ in CORPUS)
    closes = load_price_panel(tickers)

    rows_old: dict[str, dict] = {}
    rows_new: dict[str, dict] = {}
    panels: dict[str, dict] = {}
    decision_rows: dict[str, object] = {}
    coverage: list[dict] = []
    replayed: list[ReplayRow] = []

    for ticker, sample in CORPUS:
        panel, decision, cover = build_panel(ticker, closes[ticker], DECISION_SESSION)
        cover["sample"] = sample
        coverage.append(cover)
        if decision is None:
            continue
        panels[ticker] = panel
        decision_rows[ticker] = decision
        judgements = JUDGEMENTS_FOR[sample]
        old = value_issuer(ticker, panel, decision, judgements=judgements, window_contract=OLD)
        new = value_issuer(ticker, panel, decision, judgements=judgements, window_contract=NEW)
        rows_old[ticker], rows_new[ticker] = old, new

        old_w, new_w = old.get("contract_window"), new.get("contract_window")
        trends, ratio = _window_diagnostics(new)
        replayed.append(ReplayRow(
            ticker=ticker, sample=sample, status=new["status"], old_status=old["status"],
            method=new.get("primary_method"), old_method=old.get("primary_method"),
            old_window=old_w, new_window=new_w, changed=old_w != new_w,
            old_base_fv=(old.get("fair_value") or {}).get("base", {}).get("fair_value_per_share"),
            new_base_fv=(new.get("fair_value") or {}).get("base", {}).get("fair_value_per_share"),
            old_tp1=_targets(old).get("TP1"), new_tp1=_targets(new).get("TP1"),
            old_tp2=_targets(old).get("TP2"), new_tp2=_targets(new).get("TP2"),
            old_tp1_upside=_targets(old).get("upside_to_TP1"),
            new_tp1_upside=_targets(new).get("upside_to_TP1"),
            old_tp2_upside=_targets(old).get("upside_to_TP2"),
            new_tp2_upside=_targets(new).get("upside_to_TP2"),
            old_confidence=old["confidence"]["confidence"],
            new_confidence=new["confidence"]["confidence"],
            reason_window_changed=new.get("contract_window_reason") or "no window selected",
            window_trends=trends, base_multiple_ratio_full_vs_shortest=ratio))

    return {"rows_old": rows_old, "rows_new": rows_new, "panels": panels,
            "decision_rows": decision_rows, "coverage": coverage, "replayed": replayed}


# -------------------------------------------------------------------------------------------------
# D6 counterfactual
# -------------------------------------------------------------------------------------------------

def d6_counterfactual(rows_old: Mapping[str, dict], rows_new: Mapping[str, dict]) -> dict:
    """The D6 decision engine on the repaired valuations, with no decision rule changed.

    The engine is imported and called, not reimplemented, and the D3/D4 legs are the identical stored
    artifacts. So the only thing that can move a decision here is the window the valuation selected,
    which is exactly the question this step is asking.
    """
    legs = load_legs()
    changes: list[dict] = []
    old_counts = {d.value: 0 for d in Decision}
    new_counts = {d.value: 0 for d in Decision}
    records_new = []
    d5_new: dict[str, object] = {}
    published_new: dict[str, dict] = {}

    for ticker in D4_UNIVERSE:
        old_row, new_row = rows_old.get(ticker), rows_new.get(ticker)
        if old_row is None or new_row is None:
            continue
        d5_old, d5_fixed = project_d5(old_row), project_d5(new_row)
        before = decide(legs[ticker].d3, legs[ticker].d4, d5_old)
        after = decide(legs[ticker].d3, legs[ticker].d4, d5_fixed)
        records_new.append(after)
        d5_new[ticker] = d5_fixed
        published_new[ticker] = dict(_targets(new_row))
        published_new[ticker]["current_price"] = new_row.get("current_price")

        if before.decision is not None:
            old_counts[before.decision.value] += 1
        if after.decision is not None:
            new_counts[after.decision.value] += 1
        if before.decision is not after.decision or before.approve_blockers != after.approve_blockers:
            changes.append({
                "ticker": ticker,
                "old_decision": None if before.decision is None else before.decision.value,
                "new_decision": None if after.decision is None else after.decision.value,
                "old_blockers": list(before.approve_blockers),
                "new_blockers": list(after.approve_blockers),
                "old_reject_fired": list(before.reject_fired),
                "new_reject_fired": list(after.reject_fired),
            })

    eligible = [r.ticker for r in records_new
                if r.eligibility.eligibility is Eligibility.DECISION_ELIGIBLE]
    return {
        "old_decision_counts": old_counts,
        "new_decision_counts": new_counts,
        "decision_eligible": eligible,
        "decision_changes": changes,
        "decisions": [r.to_dict() for r in records_new],
        "d6_defects": audit_decisions(records_new, d5_new, published_new,
                                      {t: legs[t].d4.chain_break(legs[t].d3) for t in D4_UNIVERSE
                                       if legs[t].d4.chain_break(legs[t].d3)}),
    }


# -------------------------------------------------------------------------------------------------
# Acceptance (§14), each clause evaluated rather than asserted
# -------------------------------------------------------------------------------------------------

def acceptance(replayed: Sequence[ReplayRow], rows_old: Mapping[str, dict],
               rows_new: Mapping[str, dict], panels: Mapping[str, dict],
               decision_rows: Mapping[str, object]) -> dict:
    """§14's six conditions, fixed before the replay ran, each with the offenders that violate it."""
    valued = [r for r in replayed if r.status == ValuationStatus.VALUED.value]

    # (A) a VRRM-type recent reversal is detectable at all.
    reversals = [r.ticker for r in valued
                 if r.changed and r.window_trends.get(PanelWindow.FULL_2Y.value)
                 != "TRENDING_STRONG"]

    # (B) an ADBE-type dead regime is still rejected: no issuer whose FULL_2Y trends strongly may
    #     come back to FULL_2Y.
    dead_regime_readmitted = [
        r.ticker for r in valued
        if r.window_trends.get(PanelWindow.FULL_2Y.value) == "TRENDING_STRONG"
        and r.new_window == PanelWindow.FULL_2Y.value]

    # (C) no existing recent-window selection regresses back to a longer window.
    order = {PanelWindow.RECENT_6M.value: 0, PanelWindow.RECENT_12M.value: 1,
             PanelWindow.FULL_2Y.value: 2}
    lengthened = [f"{r.ticker}: {r.old_window} -> {r.new_window}" for r in valued
                  if r.old_window is not None and r.new_window is not None
                  and order[r.new_window] > order[r.old_window]]

    # (D) every window change is explained by trend and ratio, never by a fair value. Checked
    #     structurally: a changed row must have a strong non-FULL_2Y trend AND a conflicting ratio,
    #     or a strong FULL_2Y trend (V1's own branch).
    unexplained = []
    for r in valued:
        if not r.changed:
            continue
        strong = [w for w, t in r.window_trends.items() if t == "TRENDING_STRONG"]
        if PanelWindow.FULL_2Y.value in strong:
            continue
        if not strong:
            unexplained.append(f"{r.ticker}: window changed with no strongly trending window")
        elif (r.base_multiple_ratio_full_vs_shortest is None
              or r.base_multiple_ratio_full_vs_shortest <= RECENT_REGIME_CONFLICT_RATIO):
            unexplained.append(
                f"{r.ticker}: window changed on ratio {r.base_multiple_ratio_full_vs_shortest}")

    # (E) determinism: the same panel selected under the same contract twice gives the same window.
    nondeterministic = []
    for ticker, panel in panels.items():
        sample = next(s for t, s in CORPUS if t == ticker)
        again = value_issuer(ticker, panel, decision_rows[ticker],
                             judgements=JUDGEMENTS_FOR[sample], window_contract=NEW)
        if again.get("contract_window") != rows_new[ticker].get("contract_window"):
            nondeterministic.append(ticker)
        if json.dumps(again, sort_keys=True, default=str) != json.dumps(
                rows_new[ticker], sort_keys=True, default=str):
            nondeterministic.append(f"{ticker}: full row differs on re-evaluation")

    # (F) the arithmetic and provenance are untouched: D5-D2's own audit over the repaired rows.
    arithmetic = d5_audit(list(rows_new.values()), panels, decision_rows)

    # (G) the repair moves the WINDOW and nothing upstream of it. `contract_eligible` does not depend
    #     on the contract, so the preference-order walk that picks the primary method sees the same
    #     answers under both rules and must therefore reach the same method and the same status. A
    #     violation here would mean the repair changed which method values an issuer, which is a far
    #     larger change than this step claims to be making.
    method_or_status_moved = [
        f"{r.ticker}: method {r.old_method} -> {r.method}, status {r.old_status} -> {r.status}"
        for r in replayed if r.old_method != r.method or r.old_status != r.status]

    # §14 F says "D5 arithmetic/provenance unchanged", and it was implemented as "no class of
    # `d5_audit` is non-empty". Those are not the same condition and the replay is what showed it:
    # nine of the audit's ten classes are correctness - the identity invariant, operand
    # recomputation, the upside formula, observation provenance, future observations, refusals
    # carrying values - and the tenth, `fair_value_range_missing_a_leg`, is a COMPLETENESS report
    # whose own comment in D5-D2 calls an incomplete range "a weaker output than a range" rather
    # than a wrong one.
    #
    # Both readings are computed and both are reported. Splitting them is a post-hoc distinction and
    # saying so is the point: `verdict_as_preregistered` is §14 F read literally over all ten
    # classes, `verdict_on_correctness` is the nine, and a reader who disagrees with the split can
    # see exactly which number moved.
    correctness = {k: v for k, v in arithmetic.items() if v and k != "fair_value_range_missing_a_leg"}
    completeness = arithmetic.get("fair_value_range_missing_a_leg") or []
    shared = (bool(reversals) and not dead_regime_readmitted and not lengthened
              and not unexplained and not nondeterministic and not method_or_status_moved)

    return {
        "A_recent_reversal_detectable": reversals,
        "B_dead_regime_readmitted_to_full_panel": dead_regime_readmitted,
        "C_recent_window_selection_regressed": lengthened,
        "D_window_change_unexplained_by_trend_and_ratio": unexplained,
        "E_selection_nondeterministic": nondeterministic,
        "F_arithmetic_or_provenance_defects": correctness,
        "F2_range_completeness_findings": completeness,
        "G_method_or_status_moved_by_the_window_contract": method_or_status_moved,
        "all_ten_audit_classes": {k: v for k, v in arithmetic.items() if v},
        "verdict_as_preregistered": ("PASS" if shared and not any(arithmetic.values())
                                     else "NEEDS REVISION"),
        "verdict_on_correctness": "PASS" if shared and not correctness else "NEEDS REVISION",
    }


# -------------------------------------------------------------------------------------------------
# Run
# -------------------------------------------------------------------------------------------------

def run() -> dict:
    out = replay()
    replayed, rows_old, rows_new = out["replayed"], out["rows_old"], out["rows_new"]
    counterfactual = d6_counterfactual(rows_old, rows_new)
    checks = acceptance(replayed, rows_old, rows_new, out["panels"], out["decision_rows"])
    valued = [r for r in replayed if r.status == ValuationStatus.VALUED.value]

    return {
        "contract": D2R_CONTRACT,
        "old_window_contract": OLD.value,
        "new_window_contract": NEW.value,
        "conflict_ratio": RECENT_REGIME_CONFLICT_RATIO,
        "decision_session": DECISION_SESSION.isoformat(),
        "corpus": [{"ticker": t, "sample": s} for t, s in CORPUS],
        "coverage": out["coverage"],
        "replay": [r.to_dict() for r in replayed],
        "valued_cases": len(valued),
        "window_changed": [r.ticker for r in valued if r.changed],
        "window_unchanged": [r.ticker for r in valued if not r.changed],
        "d6_counterfactual": counterfactual,
        "acceptance": checks,
        "rows_old": out["rows_old"],
        "rows_new": rows_new,
        "model_calls": 0,
        "cost_usd": 0.0,
        "historical_is_immutable": HISTORICAL_IS_IMMUTABLE,
        "selection_is_not_upside_optimization": SELECTION_IS_NOT_UPSIDE_OPTIMIZATION,
        "no_forward_returns": NO_FORWARD_RETURNS,
        "recent_regime_evidence": RECENT_REGIME_EVIDENCE_IS_NOT_ONLY_THE_FULL_PANEL,
        "override_requires_a_conflict": RECENT_REGIME_OVERRIDE_REQUIRES_A_CONFLICT,
    }


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1%}"


def _num(x: float | None, nd: int = 2) -> str:
    return "-" if x is None else f"{x:,.{nd}f}"


def main(argv: list[str]) -> None:
    report = run()
    if "--json" in argv:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUTPUT_DIR / f"D5_D2R-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str))
        print(f"wrote {out}")

    print(f"\nH-V2-D5-D2R  {report['old_window_contract']} -> {report['new_window_contract']}  "
          f"conflict ratio {report['conflict_ratio']}  model calls {report['model_calls']}  "
          f"cost ${report['cost_usd']:.2f}")
    print(f"valued cases replayed {report['valued_cases']}  "
          f"window changed {len(report['window_changed'])}: {', '.join(report['window_changed'])}  "
          f"unchanged {len(report['window_unchanged'])}")

    print("\ntick  smpl  method    old window   new window   oldBaseFV  newBaseFV    oldTP1up"
          "   newTP1up    oldTP2up   newTP2up  ratio  conf")
    for r in report["replay"]:
        if r["status"] != "VALUED":
            print(f"{r['ticker']:<5} {r['sample']:<5} VALUATION_NOT_READY")
            continue
        mark = "*" if r["changed"] else " "
        print(f"{r['ticker']:<5} {r['sample']:<5} {str(r['method']):<9} "
              f"{str(r['old_window']):<12} {mark}{str(r['new_window']):<11} "
              f"{_num(r['old_base_fv']):>9} {_num(r['new_base_fv']):>10} "
              f"{_pct(r['old_TP1_upside']):>10} {_pct(r['new_TP1_upside']):>10} "
              f"{_pct(r['old_TP2_upside']):>11} {_pct(r['new_TP2_upside']):>10} "
              f"{_num(r['base_multiple_ratio_full_vs_shortest'], 3):>6} "
              f"{r['old_confidence']}->{r['new_confidence']}")

    print("\nwindow changes, with the reason the rule gave:")
    for r in report["replay"]:
        if r["changed"]:
            print(f"  {r['ticker']}: {r['old_window']} -> {r['new_window']}")
            print(f"      trends {r['window_trends']}")
            print(f"      {r['reason_window_changed']}")

    cf = report["d6_counterfactual"]
    print(f"\nD6 counterfactual  old {cf['old_decision_counts']}  new {cf['new_decision_counts']}")
    print(f"  eligible {len(cf['decision_eligible'])}: {', '.join(cf['decision_eligible'])}")
    if cf["decision_changes"]:
        for change in cf["decision_changes"]:
            print(f"  {change['ticker']}: {change['old_decision']} -> {change['new_decision']}  "
                  f"blockers {change['old_blockers']} -> {change['new_blockers']}")
    else:
        print("  decision changes: 0")
    print("  D6 defects:", {k: v for k, v in cf["d6_defects"].items() if v} or 0)

    print("\nacceptance (§14):")
    for name, value in report["acceptance"].items():
        if name.startswith("verdict") or name == "all_ten_audit_classes":
            continue
        print(f"  {name:<48} {value if value else 0}")
    checks = report["acceptance"]
    print(f"\n  verdict on correctness (9 audit classes)   {checks['verdict_on_correctness']}")
    print(f"  verdict as preregistered (all 10 classes)  {checks['verdict_as_preregistered']}")
    if checks["F2_range_completeness_findings"]:
        print("  the difference between the two is entirely:")
        for item in checks["F2_range_completeness_findings"]:
            print(f"    {item}")


if __name__ == "__main__":
    main(sys.argv[1:])
