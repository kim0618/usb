"""EQM-V0 cohorts: the C-E0 statuses, split once more by measurable event magnitude.

Nothing here re-decides a C-E0 status. EQ1/EQ2/EQ3 are strictly nested subsets of EM, and EM_REST
is EM minus EQ2 - the declared comparator of H2, because comparing a subset with the set that
contains it is not a comparison. `EQ_MATERIAL_RISK` is kept out of every primary cohort and
reported on its own, as the declaration requires.
"""

from collections.abc import Mapping
from typing import Any

import pandas as pd

M_ONLY = "M_ONLY"
EM = "EM"
EM_NEGATIVE_RISK = "EM_NEGATIVE_RISK"
EQ1 = "EQ1"
EQ2 = "EQ2"
EQ3 = "EQ3"
EM_REST = "EM_REST"
EQ_MATERIAL_RISK = "EQ_MATERIAL_RISK"
OBSERVABLE = "OBSERVABLE"


def assign(status: pd.DataFrame, quality: pd.DataFrame, *, material_cut: float) -> pd.DataFrame:
    """One row per C-M0 candidate with every cohort membership flag the gate reads."""
    keep = ["date_idx", "ticker", "quality_status", "revenue_growth_yoy", "period_label",
            "recent_dilution_20", "financing_event_60d", "accession", "accession_form",
            "same_class_count_90d", "same_class_count_180d"]
    frame = status[["date_idx", "ticker", "cik", "signal_date", "status"]].merge(
        quality[keep], on=["date_idx", "ticker"], how="left", validate="one_to_one")
    frame["observable"] = frame["quality_status"].eq(OBSERVABLE)
    frame["risk_flag"] = (flag_column(frame["recent_dilution_20"])
                          | flag_column(frame["financing_event_60d"]))
    frame["material"] = frame["observable"] & (frame["revenue_growth_yoy"] >= material_cut)
    frame["is_m_only"] = frame["status"].eq(M_ONLY)
    frame["is_em"] = frame["status"].eq(EM)
    frame["is_eq1"] = frame["is_em"] & frame["observable"]
    frame["is_eq2"] = frame["is_em"] & frame["material"]
    frame["is_eq3"] = frame["is_eq2"] & ~frame["risk_flag"]
    frame["is_em_rest"] = frame["is_em"] & ~frame["is_eq2"]
    frame["is_material_risk"] = ((frame["is_eq2"] & frame["risk_flag"])
                                 | (frame["status"].eq(EM_NEGATIVE_RISK) & frame["material"]))
    return frame


def flag_column(column: pd.Series) -> pd.Series:
    """A missing flag is False: a row with no quality row simply has no risk evidence."""
    return column.astype("object").where(column.notna(), False).astype(bool)


def bucket_of(growth: float | None, ladder: tuple[tuple[str, Any, Any], ...]) -> str | None:
    if growth is None or pd.isna(growth):
        return None
    for label, low, high in ladder:
        if (low is None or growth >= low) and (high is None or growth < high):
            return str(label)
    return None


def counts(frame: pd.DataFrame) -> Mapping[str, int]:
    return {
        M_ONLY: int(frame["is_m_only"].sum()),
        EM: int(frame["is_em"].sum()),
        EQ1: int(frame["is_eq1"].sum()),
        EQ2: int(frame["is_eq2"].sum()),
        EQ3: int(frame["is_eq3"].sum()),
        EM_REST: int(frame["is_em_rest"].sum()),
        EQ_MATERIAL_RISK: int(frame["is_material_risk"].sum()),
    }
