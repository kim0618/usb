"""D-AGG-1 point-in-time audits of the excursion geometry, on the real frozen panel.

Each audit date ``D`` is chosen by position before anything is computed (evenly spaced over the
evaluation grid). Every audit compares the geometry of session ``D`` - for every ticker of the
panel, not only the queries - before and after one mutation, and states what must hold:

    AFTER_WINDOW     rows >= D+6 scrambled, a split planted at D+6
                     -> entry, MFE, MAE, UP10, DN10 identical wherever the row stays valid;
                        the only allowed validity change is LABEL_CA_SUSPECT (the frozen
                        V2-A CA rule reads D..D+10)
    AFTER_CA_WINDOW  rows >= D+11 scrambled, a split planted at D+11
                     -> every geometry field identical, validity and status included
    HIGH_CONTROL     H(D+3) x 3      -> MFE rises somewhere, never falls; MAE and validity fixed
    LOW_CONTROL      L(D+4) x 0.3    -> MAE falls somewhere, never rises; MFE and validity fixed
    HORIZON_CONTROL  H(D+5) x 3      -> MFE rises somewhere (D+5 is inside the window)
    ENTRY_CONTROL    O(D+1) x 1.07   -> MFE and MAE both move somewhere
    BEFORE_WINDOW    rows <= D-1 scrambled, H(D) and L(D) scrambled
                     -> every geometry field identical (the window starts at D+1; the CA rule
                        reads close(D), which is not touched)
    SIGNAL_FIREWALL  rows > D scrambled (V2-A's own ``mutate_future``); A(q) re-derived from the
                     D2 neighbours and labels of the mutated panel
                     -> A(q) bit-identical to the D3 artifact, and the geometry of D moves

Audits run on a row slice ``[D-5, D+20]`` of the panel. ``slice_equivalence`` proves first that
the slice gives the full panel's geometry at ``D`` bit for bit, which is also a truncation audit:
nothing after D+20 can reach session D. The descriptive ``missing_subclass`` reads past the
window by design and is excluded from every invariance comparison.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any
import warnings

import numpy as np

from app.backtest.strategy_c_selection.panel import Panel, SplitEvent, with_changes
from app.backtest.strategy_d_agg import excursions
from app.backtest.strategy_d_agg.models import STATUS_CODE, HardFail
from app.backtest.strategy_d_analog import universe as v1_universe
from app.backtest.strategy_d_analog.label_extension import compute_validity
from app.backtest.strategy_d_analog.source import DailyHistory
from app.backtest.strategy_d_v2 import analog_signal, evaluation_labels
from app.backtest.strategy_d_v2.d2 import mutate_future

SLICE_BEFORE = 5
SLICE_AFTER = 20
FIELDS = ("entry_open", "mfe", "mae", "up", "down", "valid", "status", "window_bars")


def audit_dates(eval_start: int, eval_end: int, count: int) -> tuple[int, ...]:
    """``count`` evenly spaced evaluation sessions, fixed by position only."""
    span = np.linspace(eval_start, eval_end, count)
    return tuple(int(round(v)) for v in span)


def row_slice(panel: Panel, lo: int, hi: int) -> Panel:
    """Rows ``lo..hi`` inclusive. Splits are kept whole: ``F(t)`` is cumulative over executions."""
    cut = slice(lo, hi + 1)
    return with_changes(panel, sessions=panel.sessions[cut], open=panel.open[cut],
                        high=panel.high[cut], low=panel.low[cut], close=panel.close[cut],
                        volume=panel.volume[cut])


def _row(panel: Panel, local_row: int, geometry: Mapping[str, Any]) -> dict[str, np.ndarray]:
    ex = excursions.compute(panel, **geometry)
    return {name: getattr(ex, name)[local_row].copy() for name in FIELDS}


def _same(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    if a.dtype.kind == "f":
        return (np.isnan(a) & np.isnan(b)) | (a == b)
    return a == b


def _scale_rows(panel: Panel, rows: slice, *, seed: int) -> Panel:
    rng = np.random.default_rng(seed)
    out = {}
    for name in ("open", "high", "low", "close", "volume"):
        array = getattr(panel, name).copy()
        shape = array[rows].shape
        array[rows] = array[rows] * rng.uniform(0.2, 5.0, shape)
        out[name] = array
    # keep L <= O, C <= H on the scrambled rows so the mutated panel is still a valid bar set
    stacked = np.stack([out["open"][rows], out["close"][rows], out["high"][rows], out["low"][rows]])
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.filterwarnings("ignore", "All-NaN slice encountered", RuntimeWarning)
        out["high"][rows] = np.nanmax(stacked, axis=0)
        out["low"][rows] = np.nanmin(stacked, axis=0)
    return with_changes(panel, **out)


def _plant_split(panel: Panel, session_row: int, tickers: Sequence[str]) -> Panel:
    when = panel.sessions[session_row]
    planted = tuple(SplitEvent(t, when, 1.0, 2.0) for t in tickers)
    return with_changes(panel, splits=panel.splits + planted)


def _scale_cell(panel: Panel, field: str, row: int, factor: float) -> Panel:
    array = getattr(panel, field).copy()
    array[row] = array[row] * factor
    return with_changes(panel, **{field: array})


def _scale_open_keeping_bar(panel: Panel, row: int, factor: float) -> Panel:
    """O(row) x factor, with H(row) raised to the new open if needed so L <= O <= H still holds."""
    open_, high = panel.open.copy(), panel.high.copy()
    open_[row] = open_[row] * factor
    high[row] = np.fmax(high[row], open_[row])
    return with_changes(panel, open=open_, high=high)


def audit_one(panel: Panel, date_idx: int, geometry: Mapping[str, Any],
              audit_tickers: Sequence[str]) -> dict[str, Any]:
    """All geometry audits of one date. Returns findings (violations) and witnesses."""
    lo, hi = date_idx - SLICE_BEFORE, min(date_idx + SLICE_AFTER, panel.close.shape[0] - 1)
    local = row_slice(panel, lo, hi)
    d = date_idx - lo
    base = _row(local, d, geometry)
    findings: list[str] = []
    witness: dict[str, int] = {}
    valid = base["valid"]

    def compare(name: str, mutated: dict[str, np.ndarray], *, fields=FIELDS,
                allow_ca_only: bool = False) -> None:
        both = valid & mutated["valid"]
        for field in fields:
            where = both if field in ("mfe", "mae", "up", "down") else np.ones_like(valid)
            if field in ("valid", "status") and allow_ca_only:
                changed = ~_same(base[field], mutated[field])
                bad = changed & ~(mutated["status"] == STATUS_CODE["LABEL_CA_SUSPECT"])
                if bad.any():
                    findings.append(f"{name}:{field}_changed_not_ca:{int(bad.sum())}")
                witness[f"{name}_validity_changed_by_ca_window"] = int((changed & valid).sum())
                continue
            if not _same(base[field], mutated[field])[where].all():
                findings.append(f"{name}:{field}_changed")

    after = _plant_split(_scale_rows(local, slice(d + 6, None), seed=date_idx), d + 6, audit_tickers)
    compare("AFTER_WINDOW", _row(after, d, geometry), allow_ca_only=True)
    after_ca = _plant_split(_scale_rows(local, slice(d + 11, None), seed=date_idx + 1),
                            d + 11, audit_tickers)
    compare("AFTER_CA_WINDOW", _row(after_ca, d, geometry))

    before = _scale_rows(local, slice(0, d), seed=date_idx + 2)
    before = _scale_cell(_scale_cell(before, "high", d, 1.9), "low", d, 0.4)
    compare("BEFORE_WINDOW", _row(before, d, geometry))

    for name, field, row, factor, moved, fixed, direction in (
            ("HIGH_CONTROL", "high", d + 3, 3.0, "mfe", "mae", 1),
            ("HORIZON_CONTROL", "high", d + 5, 3.0, "mfe", "mae", 1),
            ("LOW_CONTROL", "low", d + 4, 0.3, "mae", "mfe", -1)):
        mutated = _row(_scale_cell(local, field, row, factor), d, geometry)
        if not _same(base["valid"], mutated["valid"]).all():
            findings.append(f"{name}:validity_changed")
        delta = (mutated[moved] - base[moved])[valid]
        witness[name] = int((delta * direction > 0).sum())
        if witness[name] == 0:
            findings.append(f"{name}:no_witness")
        if (delta * direction < 0).any():
            findings.append(f"{name}:{moved}_moved_wrong_way")
        if not _same(base[fixed], mutated[fixed])[valid].all():
            findings.append(f"{name}:{fixed}_changed")

    entry = _row(_scale_open_keeping_bar(local, d + 1, 1.07), d, geometry)
    both = valid & entry["valid"]
    witness["ENTRY_CONTROL_mfe"] = int((~_same(base["mfe"], entry["mfe"]) & both).sum())
    witness["ENTRY_CONTROL_mae"] = int((~_same(base["mae"], entry["mae"]) & both).sum())
    if witness["ENTRY_CONTROL_mfe"] == 0 or witness["ENTRY_CONTROL_mae"] == 0:
        findings.append("ENTRY_CONTROL:no_witness")
    return {"date_idx": int(date_idx), "valid_rows": int(valid.sum()),
            "findings": findings, "witness": witness}


def slice_equivalence(panel: Panel, full: excursions.Excursions, date_idx: int,
                      geometry: Mapping[str, Any]) -> bool:
    lo, hi = date_idx - SLICE_BEFORE, min(date_idx + SLICE_AFTER, panel.close.shape[0] - 1)
    local = _row(row_slice(panel, lo, hi), date_idx - lo, geometry)
    return all(_same(getattr(full, name)[date_idx], local[name]).all() for name in FIELDS)


def signal_firewall(history: DailyHistory, eligible: np.ndarray, date_idx: int, *,
                    geometry: Mapping[str, Any], positions: np.ndarray, artifact_analog: np.ndarray,
                    neighbor_end: np.ndarray, neighbor_col: np.ndarray, top_k: int,
                    query_cols: np.ndarray, base_mfe: np.ndarray) -> dict[str, Any]:
    """Scramble everything after D; A(q) of D's queries must not move, their geometry must."""
    panel = mutate_future(history.panel, date_idx)
    horizon = geometry["horizon"]
    validity = compute_validity(panel, (horizon,), geometry["ca_ratio"])
    excess = evaluation_labels.forward_excess(panel, validity, eligible, horizon)
    rows = (positions[:, None] * top_k + np.arange(top_k)[None, :]).ravel()
    labels = excess.gather(neighbor_end[rows].astype(np.int64), neighbor_col[rows].astype(np.int64))
    recomputed = analog_signal.median_by_group(labels, top_k).values
    signal_unchanged = bool(np.array_equal(recomputed, artifact_analog))
    lo, hi = date_idx - SLICE_BEFORE, min(date_idx + SLICE_AFTER, panel.close.shape[0] - 1)
    mutated = _row(row_slice(panel, lo, hi), date_idx - lo, geometry)
    moved = ~_same(base_mfe, mutated["mfe"][query_cols])
    return {"date_idx": int(date_idx), "queries": int(positions.size),
            "signal_unchanged": signal_unchanged, "geometry_moved": int(moved.sum()),
            "findings": ([] if signal_unchanged else ["analog_signal_changed"])
                        + ([] if moved.any() else ["geometry_did_not_move"])}


def universe_as_of(history: DailyHistory, rules, date_idx: int, reference: np.ndarray) -> bool:
    """Eligibility of session t with every later bar scrambled equals the recorded eligibility."""
    mutated = replace(history, panel=mutate_future(history.panel, date_idx), _cache={})
    result = v1_universe.evaluate(mutated.panel_view(date_idx), mutated.membership(date_idx), rules)
    return bool(np.array_equal(result.eligible, reference))


def run(history: DailyHistory, full: excursions.Excursions, *, dates: Sequence[int],
        geometry: Mapping[str, Any], log: Callable[[str], None] = lambda _: None) -> dict[str, Any]:
    tickers = history.panel.tickers
    out = []
    for index in dates:
        if not slice_equivalence(history.panel, full, index, geometry):
            raise HardFail("P1", f"row slice at {index} does not reproduce the full geometry")
        out.append(audit_one(history.panel, index, geometry, tickers))
    findings = [f for item in out for f in item["findings"]]
    log(f"geometry PIT: {len(dates)} dates, {len(findings)} findings")
    return {"dates": list(dates), "per_date": out, "violations": len(findings),
            "slice_equivalence": True}
