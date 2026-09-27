"""The frozen A/E paper evaluation gate (docs/operations/AE_PAPER_EVALUATION_GATE_V1.md), evaluated.

The thresholds live in the contract JSON, which is checksum-verified on every load; this module
holds no threshold of its own. It reads the metrics ``app.strategies.performance`` produced and
returns PASS / INCONCLUSIVE / FAIL with one row per condition, so a screen can show which
condition holds the verdict back.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from app.strategies.ledger import ACCOUNTING_V0, ACCOUNTING_V1

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = REPO_ROOT / "docs/operations/ae_paper_evaluation_gate_v1.json"
CHECKSUM = REPO_ROOT / "docs/operations/ae_paper_evaluation_gate_v1.sha256"

PASS, INCONCLUSIVE, FAIL, PENDING = "PASS", "INCONCLUSIVE", "FAIL", "PENDING"
PNL_CONDITIONS = ("net_pnl", "expectancy", "pf", "mdd", "divergence")


class ContractError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def contract() -> dict[str, Any]:
    body = json.loads(CONTRACT.read_text(encoding="utf-8"))
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = CHECKSUM.read_text(encoding="utf-8").strip()
    if hashlib.sha256(canonical.encode()).hexdigest() != expected:
        raise ContractError("AE paper gate contract does not match its frozen checksum")
    return body


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _row(name: str, value: Any, threshold: str, status: str, note: str | None = None) -> dict[str, Any]:
    if isinstance(value, Decimal):
        value = f"{value.quantize(Decimal('0.000001')):f}"     # display only; the verdict used full precision
    return {"condition": name, "value": None if value is None else str(value), "threshold": threshold,
            "status": status, "note": note}


def evaluate(strategy_id: str, metrics: Mapping[str, Any], *, error_sessions: int | None,
             divergence: Decimal | None) -> dict[str, Any]:
    body = contract()
    rules = body["strategies"].get(strategy_id)
    if rules is None:
        return {"strategy_id": strategy_id, "verdict": None, "reason": "NOT_UNDER_THIS_GATE"}
    common = body["common"]
    sessions = int(metrics.get("operating_sessions") or 0)
    trades = int(metrics.get("trades") or 0)
    rows: list[dict[str, Any]] = []

    sample_sessions = sessions >= rules["min_operating_sessions"]
    sample_trades = trades >= rules["min_closed_trades"]
    rows.append(_row("operating_sessions", sessions, f">= {rules['min_operating_sessions']}",
                     PASS if sample_sessions else PENDING))
    rows.append(_row("closed_trades", trades, f">= {rules['min_closed_trades']}", PASS if sample_trades else PENDING))

    def judge(name: str, value: Decimal | None, ok: bool | None, threshold: str) -> None:
        if value is None or ok is None:
            rows.append(_row(name, value, threshold, PENDING, (metrics.get("na") or {}).get(name)))
        else:
            rows.append(_row(name, value, threshold, PASS if ok else FAIL))

    net, exp, pf, mdd = (_dec(metrics.get(k)) for k in ("net_pnl", "expectancy", "pf", "mdd"))
    judge("net_pnl", net, None if net is None else net > rules["net_pnl_gt"], f"> {rules['net_pnl_gt']}")
    judge("expectancy", exp, None if exp is None else exp > rules["expectancy_gt"], f"> {rules['expectancy_gt']}")
    judge("pf", pf, None if pf is None else pf >= Decimal(str(rules["pf_min"])), f">= {rules['pf_min']}")
    judge("mdd", mdd, None if mdd is None else mdd >= Decimal(str(rules["mdd_floor"])), f">= {rules['mdd_floor']}")
    bound = rules["divergence"]
    if "max" in bound:
        judge("divergence", divergence, None if divergence is None else divergence <= Decimal(str(bound["max"])),
              f"<= {bound['max']}")
    else:
        judge("divergence", divergence, None if divergence is None else divergence >= Decimal(str(bound["min"])),
              f">= {bound['min']}")

    limits = common["operational_error_rate"]
    if error_sessions is None or sessions == 0:
        rows.append(_row("operational_error_rate", None, f"<= {limits['pass_max']}", PENDING,
                         "error sessions not recorded" if error_sessions is None else "no session"))
        error_rate = None
    else:
        error_rate = Decimal(error_sessions) / sessions
        hard = sessions >= limits["fail_needs_min_sessions"] and error_rate > Decimal(str(limits["fail_above"]))
        rows.append(_row("operational_error_rate", round(error_rate, 4), f"<= {limits['pass_max']}",
                         FAIL if hard else (PASS if error_rate <= Decimal(str(limits["pass_max"])) else PENDING)))

    accounting = list(metrics.get("accounting_mix") or [])
    reasons: list[str] = []
    if len(accounting) > 1:
        reasons.append("ACCOUNTING_MIXED")
    failed = [r["condition"] for r in rows if r["status"] == FAIL]
    pending = [r["condition"] for r in rows if r["status"] == PENDING]
    v0 = accounting == [ACCOUNTING_V0]

    # Hard stops that do not wait for the sample: a drawdown past the floor and an unreliable runtime.
    hard_stop = [c for c in failed if c in ("mdd", "operational_error_rate")]
    if hard_stop and not (v0 and set(hard_stop) <= set(PNL_CONDITIONS)):
        verdict = FAIL
    elif not (sample_sessions and sample_trades):
        verdict = INCONCLUSIVE
        reasons.append("SAMPLE_NOT_REACHED")
    elif "ACCOUNTING_MIXED" in reasons:
        verdict = INCONCLUSIVE
    elif failed:
        if v0 and set(failed) <= set(PNL_CONDITIONS):
            verdict = INCONCLUSIVE
            reasons.append("ACCOUNTING_V0_OVERCHARGED")
        else:
            verdict = FAIL
    elif pending:
        verdict = INCONCLUSIVE
        reasons.append("CONDITION_PENDING")
    else:
        verdict = PASS
    if v0 and verdict != FAIL:
        reasons.append("V0_ROWS_ARE_CONSERVATIVE")
    return {"strategy_id": strategy_id, "contract_id": body["declaration"]["contract_id"], "verdict": verdict,
            "reasons": reasons, "failed": failed, "pending": pending, "conditions": rows,
            "accounting": accounting, "error_rate": None if error_rate is None else str(round(error_rate, 4)),
            # A PnL-based FAIL is final only on cash-true rows; an operational FAIL is final on any.
            "final": verdict == PASS or (verdict == FAIL and (accounting == [ACCOUNTING_V1]
                                                               or "operational_error_rate" in failed))}
