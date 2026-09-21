"""The 09:25 decision seal: what was decided, digested before the outcome existed.

A forward test is only forward if the decision cannot be adjusted once the outcome is known. The
seal is how that is made checkable rather than promised:

1. at the cutoff, the eligible universe, every H5 feature and the H5 mask are computed from bars
   starting at or before 09:24 ET and daily inputs from D-1;
2. those rows are written and hashed into ``decision_digest``;
3. only afterwards may a label file be written, and it must name the digest it belongs to.

The label writer refuses to run without a seal and refuses to replace one. A seal that already
exists is never rewritten, so a second run on the same session cannot quietly change the decision.

Two provenances are distinguished, because they carry different guarantees and conflating them
would overstate the evidence:

``LIVE``
    the seal was written while the session's 09:30 bars did not yet exist. Look-ahead is
    impossible in the strongest sense: the data was not in the world yet.
``RECONSTRUCTED``
    the seal was built from a tape fetched after the session closed. Look-ahead is prevented by
    the code's cutoff and by the PIT audit, not by the passage of time. Sessions 2026-09-17 and
    2026-09-18 fall here, because they closed before this infrastructure existed.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from app.backtest.strategy_e1_forward.layout import ForwardViolation, require_forward_session
from app.backtest.strategy_e1_premarket import evaluate as e1_evaluate

LIVE = "LIVE"
RECONSTRUCTED = "RECONSTRUCTED"
SEAL_FORMAT = "e1-h5-forward-seal-v1"
#: Columns sealed for every eligible row.
#:
#: Three groups, and the reason each is here matters. H5's own four inputs; the two extra features
#: E1's shared mask function touches while serving its other hypotheses (sealing them is what lets
#: this package call that function **unmodified**, which is the anti-drift guarantee); and the
#: variables the matched control needs at analysis time. Sealing more than H5 strictly reads costs
#: nothing and keeps the hypothesis one import away from its declaration.
H5_INPUTS = ("premarket_gap", "premarket_rvol", "position_in_premarket_range",
             "return_0900_0925")
MASK_COMPANIONS = ("return_last30m", "relative_strength_vs_spy")
CONTROL_VARIABLES = ("premarket_dollar_volume", "previous_day_dollar_volume", "close_price",
                     "spy_premarket_return", "pm_bars")
SEALED_FEATURES = H5_INPUTS + MASK_COMPANIONS + CONTROL_VARIABLES


def _row_digest(symbols: Sequence[str], features: Mapping[str, np.ndarray],
                mask: np.ndarray) -> str:
    """sha256 over the sealed rows at full float precision, in a fixed column order."""
    digest = hashlib.sha256()
    digest.update(f"{SEAL_FORMAT}\n".encode())
    order = np.argsort(np.array(symbols, dtype=object), kind="stable")
    for i in order:
        cells = [str(symbols[i])] + [f"{float(features[name][i]):.17g}" for name in SEALED_FEATURES]
        cells.append("1" if bool(mask[i]) else "0")
        digest.update(("\t".join(cells) + "\n").encode())
    return digest.hexdigest()


def decision_digest(symbols: Sequence[str], features: Mapping[str, np.ndarray],
                    mask: np.ndarray) -> str:
    """Return the frozen forward-seal digest for an already evaluated H5 decision.

    Trading uses this public boundary instead of copying the serialization scheme. The private
    implementation remains unchanged so existing forward seals retain byte-for-byte identity.
    """
    return _row_digest(symbols, features, mask)


@dataclass(frozen=True)
class DecisionSeal:
    session: date
    provenance: str
    eligible_rows: int
    h5_rows: int
    decision_digest: str
    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return self.payload


def build(session: date, symbols: Sequence[str], features: Mapping[str, np.ndarray],
          *, provenance: str, sources: Mapping[str, str], rules_digest: str,
          created_at: datetime | None = None) -> DecisionSeal:
    """Seal one session's decision. H5 comes from E1's own mask, never re-implemented here."""
    require_forward_session(session)
    if provenance not in (LIVE, RECONSTRUCTED):
        raise ForwardViolation(f"provenance {provenance!r} is not LIVE or RECONSTRUCTED")
    missing = [name for name in SEALED_FEATURES if name not in features]
    if missing:
        raise ForwardViolation(f"seal is missing declared feature columns: {missing}")
    mask = e1_evaluate.mask("H5", features)
    digest = decision_digest(symbols, features, mask)
    moment = created_at or datetime.now(timezone.utc)
    payload = {
        "format": SEAL_FORMAT,
        "session": session.isoformat(),
        "decision_time_et": "09:25",
        "provenance": provenance,
        "eligible_rows": int(len(symbols)),
        "h5_rows": int(mask.sum()),
        "decision_digest": digest,
        "rules_digest": rules_digest,
        "sources": dict(sources),
        "created_at": moment.isoformat(),
        "rows": [
            {"symbol": str(symbols[i]), "h5": bool(mask[i]),
             **{name: float(features[name][i]) for name in SEALED_FEATURES}}
            for i in range(len(symbols))
        ],
    }
    return DecisionSeal(session, provenance, len(symbols), int(mask.sum()), digest, payload)


def write(path: Path, seal: DecisionSeal) -> Path:
    """Write once. An existing seal is never replaced; that is the whole point of a seal."""
    if path.exists():
        raise ForwardViolation(
            f"a decision seal already exists at {path}; a sealed decision is not rewritten. "
            "Delete it deliberately and record why, or use a different session.")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(seal.to_json(), indent=1, sort_keys=True), encoding="utf-8")
    partial.replace(path)
    return path


def read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ForwardViolation(f"no decision seal at {path}; label without a seal is not a forward test")
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = [row["symbol"] for row in payload["rows"]]
    features = {name: np.array([row[name] for row in payload["rows"]], dtype=float)
                for name in SEALED_FEATURES}
    mask = np.array([bool(row["h5"]) for row in payload["rows"]])
    found = decision_digest(symbols, features, mask)
    if found != payload["decision_digest"]:
        raise ForwardViolation(
            f"seal {path} does not hash to its own decision_digest ({found} != "
            f"{payload['decision_digest']}); the sealed rows have been edited")
    return payload
