"""H-V2 Hierarchical Industry Intake R0: pure functions for the OFFLINE feasibility replay.

Not a strategy component and not wired into production. It asks one question about the new-candidate
intake only: can a cheap price-only Industry Radar, plus a cheap filing-event bypass and an optional
rotation, choose a subset of issuers for the expensive companyfacts deep scan and still find the issuers
the full scan finds (E3 P1/P2)? D1/E1/E2/E3, D3-D6, paper and forward are untouched and only read.

Everything below was fixed before the replay ran (``H_V2_HIERARCHICAL_INDUSTRY_INTAKE_R0.md`` §2):

* industry = SEC SIC code (4 digits) from the issuer's submissions document;
* representatives = the 3 market-passing issuers of an industry with the largest E1 trailing-21-session
  average dollar volume (an existing E1 field), fixed per calendar quarter at its first replay session;
* radar inputs = the representatives' closes only (equal-weight basket, 21-session return minus SPY's
  21-session return: D1 ``price_context.relative_strength_1m``); no companyfacts, no SEC call;
* radar variants (a trade-off table for the operator, none adopted): RS_SIGN (basket RS > 0),
  RANK_TOP50 / RANK_TOP25 (basket RS in the top half / quarter of industries that session);
* event bypass = E3's own recency window (``days_since_latest_filing <= 14``) applied to periodic reports
  only (EB_PERIODIC: 10-K/10-Q and amendments) or to any filing (EB_ANY, exactly E3's input);
* rotation = industries ordered by SIC, the session index modulo N selects 1/N of them (N = 5, 10).

No forward return, no paper P&L and no outcome is read anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Iterable, Mapping, Sequence

E3_RECENT_DAYS = 14                    # research_priority.compute_priority: filing_is_recent
REPRESENTATIVES = 3
PERIODIC_FORMS = frozenset({"10-K", "10-Q", "10-K/A", "10-Q/A"})
RADAR_VARIANTS = ("RS_SIGN", "RANK_TOP50", "RANK_TOP25")
ROTATIONS = (None, 5, 10)
ACTIVE, DORMANT, UNKNOWN = "ACTIVE", "DORMANT", "UNKNOWN"


def industry_of(sic: str | int | None) -> str | None:
    text = str(sic or "").strip()
    return text.zfill(4) if text.isdigit() and int(text) > 0 else None


def quarter_key(day: date) -> str:
    return f"{day.year}Q{(day.month - 1) // 3 + 1}"


def representatives(members: Mapping[str, float | None], k: int = REPRESENTATIVES) -> list[str]:
    """Top-k tickers by E1 trailing average dollar volume, ties by ticker. ``None`` ADV is not eligible."""
    ranked = sorted(((v, t) for t, v in members.items() if v is not None), key=lambda x: (-x[0], x[1]))
    return [t for _, t in ranked[:k]]


def window_return(series: Mapping[date, float], days: Sequence[date], session: date, lookback: int = 21) -> float | None:
    """Close-to-close return over ``lookback`` sessions of the reference calendar ``days`` ending at
    ``session``; None when either close is missing."""
    if session not in days:
        return None
    i = days.index(session)
    if i < lookback:
        return None
    a, b = series.get(days[i - lookback]), series.get(session)
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


def basket_rs(reps: Sequence[str], closes: Mapping[str, Mapping[date, float]], days: Sequence[date],
              session: date, spy: float | None) -> float | None:
    rets = [r for r in (window_return(closes.get(t, {}), days, session) for t in reps) if r is not None]
    if not rets or spy is None:
        return None
    return sum(rets) / len(rets) - spy


def radar_states(rs_by_industry: Mapping[str, float | None], variant: str) -> dict[str, str]:
    known = {k: v for k, v in rs_by_industry.items() if v is not None}
    out = {k: UNKNOWN for k, v in rs_by_industry.items() if v is None}
    if variant == "RS_SIGN":
        out |= {k: ACTIVE if v > 0 else DORMANT for k, v in known.items()}
        return out
    share = {"RANK_TOP50": 0.5, "RANK_TOP25": 0.25}[variant]
    ordered = sorted(known, key=lambda k: (-known[k], k))
    cut = int(round(len(ordered) * share))
    top = set(ordered[:cut])
    out |= {k: ACTIVE if k in top else DORMANT for k in known}
    return out


def rotation_industries(industries: Sequence[str], session_index: int, n: int | None) -> set[str]:
    if not n:
        return set()
    ordered = sorted(industries)
    return {ind for i, ind in enumerate(ordered) if i % n == session_index % n}


def recent(days_since: int | None) -> bool:
    return days_since is not None and days_since <= E3_RECENT_DAYS


@dataclass(frozen=True)
class Arm:
    """One intake structure: a radar variant, an event bypass kind (None / PERIODIC / ANY), a rotation."""
    radar: str
    bypass: str | None
    rotation: int | None

    @property
    def name(self) -> str:
        return f"{self.radar}|EB={self.bypass or 'none'}|ROT={self.rotation or 'none'}"


def deep_scan_set(*, pool: Iterable[str], industry: Mapping[str, str | None], states: Mapping[str, str],
                  rotation: set[str], bypass: Mapping[str, bool]) -> set[str]:
    """Issuers of the full-scan pool (market-passing) that the hierarchical intake would deep scan."""
    out = set()
    for t in pool:
        ind = industry.get(t)
        if bypass.get(t) or (ind is not None and (states.get(ind) == ACTIVE or ind in rotation)):
            out.add(t)
    return out


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    from app.backtest.strategy_h_v2.valuation.fair_value import spearman_rho
    return spearman_rho(list(xs), list(ys)) if len(xs) >= 3 else None


def quantiles(values: Sequence[float], ps: Sequence[float] = (0.1, 0.5, 0.9)) -> list[float]:
    v = sorted(values)
    return [v[min(len(v) - 1, int(p * len(v)))] for p in ps] if v else []


def med(values: Iterable[float]) -> float | None:
    v = list(values)
    return median(v) if v else None
