"""PHASE 2: DEV reaction terciles, computed once and frozen. HOLDOUT rows are refused."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import numpy as np

from app.backtest.strategy_h_n1.common import (
    CONTRACT_CANONICAL, DEV_END, DEV_START, HN1Stop, canonical_digest,
)


def tercile_population(events: list[dict]) -> list[dict]:
    return [e for e in events if e.get("split") == "DEV" and e.get("exclusion") is None]


def dev_quantiles(rows: list[dict]) -> tuple[float, float]:
    """q33/q67 of DEV reactions. Any non-DEV or non-eligible row is a stop condition."""
    for r in rows:
        if r.get("split") != "DEV" or r.get("exclusion") is not None or not r.get("fundamental_valid"):
            raise HN1Stop(f"non-DEV or ineligible row passed to the quantile function: {r.get('accession')}")
        if not (DEV_START <= r["signal_session"] <= DEV_END):
            raise HN1Stop("signal session outside DEV passed to the quantile function")
    v = np.asarray([r["reaction"] for r in rows], dtype=np.float64)
    if v.size == 0 or not np.isfinite(v).all():
        raise HN1Stop("empty or non-finite DEV reaction population")
    q33, q67 = np.quantile(v, [1 / 3, 2 / 3], method="linear")
    return float(q33), float(q67)


def bucket(r: float, q33: float, q67: float) -> str:
    if r <= q33:
        return "WEAK"
    return "MIDDLE" if r <= q67 else "STRONG"


def freeze(events: list[dict], path: Path, event_table_sha: str) -> dict:
    if path.exists():
        raise HN1Stop(f"cutoff file already exists; recomputation prohibited: {path}")
    pop = tercile_population(events)
    q33, q67 = dev_quantiles(pop)
    body = {"artifact": "h_n1_dev_reaction_cutoff_v1",
            "development_period": f"signal-formation session {DEV_START} .. {DEV_END}",
            "eligible_reaction_n": len(pop),
            "n_f_plus": sum(1 for e in pop if e["f_plus"]), "n_f_minus": sum(1 for e in pop if e["f_minus"]),
            "q33": q33, "q67": q67,
            "method": "numpy.quantile(values, [1/3, 2/3], method='linear'), float64",
            "tie_handling": "WEAK = r <= q33; MIDDLE = q33 < r <= q67; STRONG = r > q67",
            "missing_handling": "REACTION_UNAVAILABLE and every earlier funnel exclusion are outside the population",
            "population": "DEV 10-Q events passing PIT, universe, price>=5, ADV>=5M, FUNDAMENTAL_VALID (F+ and F-), reaction available",
            "holdout_rows_passed": 0,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "contract_canonical": CONTRACT_CANONICAL,
            "event_table_sha256": event_table_sha}
    body["canonical_digest"] = canonical_digest(body)
    path.write_text(json.dumps(body, indent=1) + "\n")
    return body


def load_frozen(path: Path) -> dict:
    body = json.loads(path.read_text())
    dig = body.pop("canonical_digest")
    if canonical_digest(body) != dig or body["contract_canonical"] != CONTRACT_CANONICAL:
        raise HN1Stop("cutoff file digest or contract checksum mismatch")
    body["canonical_digest"] = dig
    return body
