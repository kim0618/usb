"""E-R3 replay steps: seal, execute through the frozen layers, attribute, and compare structurally.

Returns, costs, sizing, metrics and the verdict all come from frozen code:
``strategy_e_d6.replay.replay_session`` (E-D2..E-D5) and ``strategy_e_d6.run.evaluate``
(E-D6 metrics). What this module adds is the V1.1 seal around each session, the check that the
seal and the entry layer agree, and diagnostics that read no return.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e1_forward.seal import SEALED_FEATURES
from app.backtest.strategy_e1_premarket.evaluate import mask as research_mask
from app.backtest.strategy_e_d6 import run as R6
from app.backtest.strategy_e_d6.replay import SessionFrame, replay_session
from app.market.calendar import MarketCalendar
from app.strategy_e.execution import ENTRY_BAR_START_ET
from app.strategy_e_v1_1 import decision as D
from app.strategy_e_v1_1 import universe as U
from app.backtest.strategy_e_r3.build import ORIGINS, VARIANTS, SymbolRow

REPO_ROOT = Path(__file__).resolve().parents[4]
RESULT_VERSION = "STRATEGY_E_R3_RESULT_V1"
TRADE_COLUMNS = R6.TRADE_COLUMNS + ("universe_origin",)
#: E-D6 committed result (V1), read for comparison only.
E_D6_RESULT = REPO_ROOT / "docs/backtest/strategy_e_candidate/strategy_e_d6_result_v1.json"
E_D6_SELECTED = 501


class ReplayIntegrityError(RuntimeError):
    """A precheck or a seal/execution cross-check failed; the run is BLOCKED."""


def code_digest() -> str:
    digest = hashlib.sha256()
    roots = [REPO_ROOT / "backend/app/strategy_e", REPO_ROOT / "backend/app/strategy_e_v1_1",
             REPO_ROOT / "backend/app/backtest/strategy_e_d6",
             REPO_ROOT / "backend/app/backtest/strategy_e_r3"]
    files = [p for root in roots for p in sorted(root.glob("*.py"))]
    files.append(REPO_ROOT / "backend/app/dev/run_strategy_e_r3.py")
    for path in files:
        digest.update(f"{path.relative_to(REPO_ROOT).as_posix()}\n".encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


# -- attribution ---------------------------------------------------------------------------------

def _same(a: float, b: float) -> bool:
    return (a == b) or (np.isnan(a) and np.isnan(b))


def attribute(v1_rows, frames: Mapping[date, U.DecisionFrame],
              origins: Mapping[date, Mapping[str, str]]) -> dict[str, Any]:
    """V1 (E1 dataset, reproduced) must equal the V1.1 rows of origin V1, value for value."""
    v1_index = {}
    for i in range(len(v1_rows)):
        v1_index[(v1_rows.sessions[i], str(v1_rows.tickers[i]))] = i
    v1_mask = research_mask("H5", v1_rows.features)
    found, mismatched, h5_mismatch = set(), [], 0
    for session, frame in frames.items():
        mask = research_mask("H5", frame.features)
        for k, symbol in enumerate(frame.symbols):
            if origins[session][symbol] != "V1":
                continue
            key = (session, symbol)
            found.add(key)
            i = v1_index.get(key)
            if i is None:
                continue
            if not all(_same(float(frame.features[n][k]), float(v1_rows.features[n][i]))
                       for n in SEALED_FEATURES):
                mismatched.append(f"{session.isoformat()}:{symbol}")
            if bool(mask[k]) != bool(v1_mask[i]):
                h5_mismatch += 1
    expected = set(v1_index)
    missing = sorted(f"{s.isoformat()}:{t}" for s, t in expected - found)
    extra = sorted(f"{s.isoformat()}:{t}" for s, t in found - expected)
    ok = not missing and not extra and not mismatched and h5_mismatch == 0
    return {"v1_rows_reproduced": len(expected), "v1_1_rows_of_origin_v1": len(found),
            "v1_rows_missing_from_v1_1": len(missing), "origin_v1_rows_not_in_v1": len(extra),
            "feature_mismatches": len(mismatched), "h5_flag_mismatches": h5_mismatch,
            "examples": {"missing": missing[:5], "extra": extra[:5], "mismatched": mismatched[:5]},
            "pass": ok}


# -- seal, execute ---------------------------------------------------------------------------------

def session_frame(frame: U.DecisionFrame, rows: Mapping[str, Mapping[date, SymbolRow]]) -> SessionFrame:
    descriptors = {}
    for k, symbol in enumerate(frame.symbols):
        labels = rows[symbol][frame.session].labels
        descriptors[symbol] = {"close_price": float(frame.features["close_price"][k]),
                               "previous_day_dollar_volume":
                                   float(frame.features["previous_day_dollar_volume"][k]),
                               "R_5m": labels["R_5m"], "R_5m_strict": labels["R_5m_strict"]}
    return SessionFrame(frame.session, frame.symbols, frame.features, descriptors)


def replay_sealed(sealed: D.DecisionSeal, frame: U.DecisionFrame,
                  rows: Mapping[str, Mapping[date, SymbolRow]], origins: Mapping[str, str],
                  calendar: MarketCalendar) -> dict[str, Any]:
    """Frozen E-D2..E-D5 replay of one sealed session, cross-checked against ``decision.execute``."""
    session = frame.session
    bars = {symbol: rows[symbol][session].bars for symbol in sealed.signal.candidate_symbols}
    result = replay_session(sealed.signal, session_frame(frame, rows), bars, calendar)
    entry_bars = [bar for symbol in sealed.signal.candidate_symbols
                  for bar in bars[symbol].get(ENTRY_BAR_START_ET, ())]
    batch = D.execute(sealed, entry_bars, calendar=calendar)
    if batch.execution_digest != result["digests"]["execution"]:
        raise ReplayIntegrityError(f"{session}: sealed execution differs from the replayed one")
    for record in result["records"]:
        record["universe_origin"] = origins[record["symbol"]]
    result["digests"]["seal"] = sealed.seal_digest
    return result


# -- structural delta (no returns) ------------------------------------------------------------

def structural_delta(frames: Mapping[date, U.DecisionFrame],
                     origins: Mapping[date, Mapping[str, str]], limit: int = 3) -> dict[str, Any]:
    per_variant: dict[str, dict[str, Any]] = {}
    selected_by: dict[str, dict[date, tuple[str, ...]]] = {}
    added = {o: {"rows": 0, "h5": 0} for o in ORIGINS}
    for name, allowed in VARIANTS.items():
        rows = candidates = selected = sessions = 0
        chosen: dict[date, tuple[str, ...]] = {}
        for session, frame in frames.items():
            mask = research_mask("H5", frame.features)
            members = [k for k, s in enumerate(frame.symbols) if origins[session][s] in allowed]
            rows += len(members)
            cands = sorted(frame.symbols[k] for k in members if mask[k])
            candidates += len(cands)
            chosen[session] = tuple(cands[:limit])
            selected += len(chosen[session])
            sessions += bool(chosen[session])
        selected_by[name] = chosen
        per_variant[name] = {"universe_rows": rows, "h5_candidates": candidates,
                             "selected_candidates": selected, "selected_sessions": sessions}
    for session, frame in frames.items():
        mask = research_mask("H5", frame.features)
        for k, symbol in enumerate(frame.symbols):
            o = origins[session][symbol]
            added[o]["rows"] += 1
            added[o]["h5"] += int(bool(mask[k]))
    base = selected_by["V1"]
    for name in VARIANTS:
        per_variant[name]["selection_changed_sessions_vs_v1"] = sum(
            selected_by[name][s] != base[s] for s in frames)
    return {"variants": per_variant,
            "rows_by_origin": {o: added[o]["rows"] for o in ORIGINS},
            "h5_candidates_by_origin": {o: added[o]["h5"] for o in ORIGINS},
            "origin_legend": {"V1": "admitted by V1", "A": "needed only correction A (session-D daily open)",
                              "B": "needed only correction B (09:30 bar)",
                              "AB": "needed both corrections"},
            "role": "DIAGNOSTIC; no return is read"}


def entry_invalid_diagnostics(replayed: Sequence[Mapping[str, Any]],
                              seals: Mapping[str, D.DecisionSeal]) -> dict[str, Any]:
    invalid, sessions, affected = Counter(), set(), set()
    selected = 0
    for session in replayed:
        sealed = seals[session["session"]]
        for record in session["records"]:
            if not record["selected"]:
                continue
            selected += 1
            if record["entry_status"] != "EXECUTED_PROXY":
                invalid[record["entry_status"]] += 1
                sessions.add(session["session"])
                if sealed.not_selected:
                    affected.add(session["session"])
    count = sum(invalid.values())
    return {"entry_invalid": count, "entry_invalid_reasons": dict(sorted(invalid.items())),
            "entry_invalid_sessions": len(sessions),
            "entry_invalid_rate_of_selected": (count / selected) if selected else None,
            "no_backfill_affected_sessions": len(affected),
            "no_backfill_definition": "a selected entry was invalid while a not-selected H5 candidate existed; the slot stayed empty",
            "role": "DIAGNOSTIC; not a gate (coverage denominator is valid entries, as frozen)"}


# -- comparisons ---------------------------------------------------------------------------------

def v1_comparison(evaluation: Mapping[str, Any], delta: Mapping[str, Any]) -> dict[str, Any]:
    import json
    v1 = json.loads(E_D6_RESULT.read_text(encoding="utf-8"))

    def pick(result: Mapping[str, Any]) -> dict[str, Any]:
        gate, primary = result["primary_gate"], result["scenarios"]["COST_10BP"]
        conc = result["concentration"]["cost_10bp"]
        return {"standard_trades": result["funnel"]["standard_pnl_trades"]["count"],
                "selected": result["funnel"]["selected_candidates"]["count"],
                "h5_candidates": result["funnel"]["H5_candidates"]["count"],
                "active_sessions": primary["active_sessions"],
                "mean_session_10bp": gate["all_session_mean"],
                "profit_factor_10bp": gate["profit_factor"],
                "bootstrap_ci": [gate["bootstrap"]["ci_low"], gate["bootstrap"]["ci_high"]],
                "block_means": [b["mean"] for b in gate["blocks"]],
                "positive_blocks": gate["positive_blocks"],
                "coverage": result["funnel"]["standard_pnl_coverage"],
                "top1": conc["top1"], "top5_share": conc["top5"]["share_of_total"],
                "top10_share": conc["top10"]["share_of_total"],
                "hhi_trade_count": conc["hhi_trade_count"], "unique_symbols": conc["unique_symbols"],
                "verdict": gate["verdict"]}

    return {"trading_v1_e_d6": pick(v1), "trading_v1_1_e_r3": pick(evaluation),
            "selected_sessions": {"v1": delta["variants"]["V1"]["selected_sessions"],
                                  "v1_1": delta["variants"]["V1_1"]["selected_sessions"]},
            "note": "V1 figures are read from the committed E-D6 result; V1 was not re-run. The two "
                    "are separate evidence layers and are never pooled."}


def evidence_layers(evaluation: Mapping[str, Any], label: str) -> dict[str, Any]:
    return {
        "A_research_h5": {"measure": "matched-control lift, observational, not a return",
                          "value_bp": 17.01, "source": "E1_H5_CONFIRMATION.md section F"},
        "B_trading_v1_e_d6": {"status": "forward-incompatible historical result, preserved",
                              "verdict": "E-D6 INCONCLUSIVE — STATISTICAL",
                              "result_file_sha256": "f70f9196894b2a428c039fdd833cdd8ba4284dd9594778ceed6c02e9d5bf7eef"},
        "C_trading_v1_1_e_r3": {"status": "PIT-corrected development replay",
                                "verdict": label},
        "rule": "never pooled, chained or treated as one sample",
    }
