"""The engineering and point-in-time checks that must pass before a single return is read.

Every check is a count that has to be zero, or a pair of digests that have to be equal. They are
written to the run directory before the evaluation stage runs, and the evaluation stage refuses
to start when any of them fails.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
import hashlib

import numpy as np


@dataclass
class AuditLog:
    counts: dict[str, int] = field(default_factory=dict)
    notes: dict[str, object] = field(default_factory=dict)

    def add(self, code: str, amount: int = 1) -> None:
        self.counts[code] = self.counts.get(code, 0) + int(amount)

    def note(self, code: str, value: object) -> None:
        self.notes[code] = value

    def get(self, code: str) -> int:
        return int(self.counts.get(code, 0))


def array_digest(arrays: Sequence[np.ndarray]) -> str:
    digest = hashlib.sha256()
    for array in arrays:
        contiguous = np.ascontiguousarray(array)
        digest.update(str(contiguous.dtype).encode())
        digest.update(str(contiguous.shape).encode())
        digest.update(contiguous.tobytes())
    return digest.hexdigest()


def check_neighbors(log: AuditLog, *, query_date: int, embargo: int, query_ticker: int,
                    query_figi: int, neighbor_dates: np.ndarray, neighbor_tickers: np.ndarray,
                    neighbor_figis: np.ndarray, ticker_cap: int, date_cap: int,
                    max_horizon: int) -> None:
    if neighbor_dates.size == 0:
        return
    log.add("E1_future_analog", int((neighbor_dates > query_date).sum()))
    log.add("E2_embargo_violation", int((neighbor_dates > query_date - embargo).sum()))
    log.add("E3_entity_cap_violation", int((neighbor_tickers == query_ticker).sum()))
    _, ticker_counts = np.unique(neighbor_tickers, return_counts=True)
    log.add("E3_entity_cap_violation", int((ticker_counts > ticker_cap).sum()))
    known = neighbor_figis[neighbor_figis >= 0]
    if known.size:
        _, figi_counts = np.unique(known, return_counts=True)
        log.add("E3_entity_cap_violation", int((figi_counts > ticker_cap).sum()))
        if query_figi >= 0:
            log.add("E3_entity_cap_violation", int((known == query_figi).sum()))
    _, date_counts = np.unique(neighbor_dates, return_counts=True)
    log.add("E4_date_cap_violation", int((date_counts > date_cap).sum()))
    log.add("E6_label_leakage", int((neighbor_dates + max_horizon >= query_date + 1).sum()))


def engineering_pass(log: AuditLog) -> bool:
    codes = ("E1_future_analog", "E2_embargo_violation", "E3_entity_cap_violation",
             "E4_date_cap_violation", "E5_normalization_pit", "E6_label_leakage")
    if any(log.get(code) for code in codes):
        return False
    if not (log.notes.get("E7_determinism") is True
            and log.notes.get("E8_candidate_match") is True):
        return False
    reproduction = log.notes.get("E9_c_e0_cohort_reproduction")
    return not isinstance(reproduction, dict) or reproduction.get("identical") is not False


def gate_report(log: AuditLog) -> dict[str, object]:
    return {
        "E1_future_analog_count": log.get("E1_future_analog"),
        "E2_embargo_violation": log.get("E2_embargo_violation"),
        "E3_entity_cap_violation": log.get("E3_entity_cap_violation"),
        "E4_date_cap_violation": log.get("E4_date_cap_violation"),
        "E5_normalization_pit_violation": log.get("E5_normalization_pit"),
        "E6_label_leakage": log.get("E6_label_leakage"),
        "E7_deterministic_digest_twice": log.notes.get("E7_determinism"),
        "E8_frozen_c_m_candidate_reproduction": log.notes.get("E8_candidate_match"),
        "E9_frozen_c_e0_cohort_reproduction": log.notes.get("E9_c_e0_cohort_reproduction"),
        "notes": {k: v for k, v in log.notes.items()
                  if k not in ("E7_determinism", "E8_candidate_match",
                               "E9_c_e0_cohort_reproduction")},
        "pass": engineering_pass(log),
    }
