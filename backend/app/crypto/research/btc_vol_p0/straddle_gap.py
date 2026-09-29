"""Put the break-even implied volatility next to the volatility an option would be priced at.

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_p0.straddle_gap

The two halves of the long-volatility question were computed separately: what a straddle must
cost to break even, and how volatile the market already looked when P1 fired. This joins them.

The implied side is a proxy. Trailing realised volatility is what an option price tracks, and
implied normally sits *above* it because sellers charge a risk premium, so using trailing
realised as the assumed implied is the most generous assumption available to the buyer. If the
structure loses under that assumption, it loses under the real one by more.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

OUT = Path("data/research/crypto/btc_vol_p0/straddle_gap_v1.json")
ECONOMICS = Path("data/research/crypto/btc_vol_p0/move_economics_v1.json")
VOL = Path("data/research/crypto/btc_vol_p0/realized_vs_trailing_v1.json")

HOURS_PER_YEAR = 365.25 * 24
STRADDLE_COEFFICIENT = 0.7978845608
HIGH_STATE_KEY = "p>=0.75"


def annualise(vol_bp_over_horizon: float, horizon_minutes: int) -> float:
    """A realised volatility measured over one horizon, restated per year."""
    periods = HOURS_PER_YEAR / (horizon_minutes / 60.0)
    return (vol_bp_over_horizon / 1e4) * np.sqrt(periods)


def analyse() -> dict[str, Any]:
    economics = json.loads(ECONOMICS.read_text())
    vol = json.loads(VOL.read_text())

    rows: dict[str, Any] = {}
    for combo, entry in vol["combos"].items():
        state = entry["states"].get(HIGH_STATE_KEY)
        econ = economics["combos"].get(combo, {}).get("subsets", {}).get("p1_high_state")
        if not state or "median_ratio" not in state or not econ or not econ.get("n"):
            continue
        horizon = entry["horizon_minutes"]
        straddle = econ["straddle_held_to_expiry"]

        trailing_iv = annualise(state["median_trailing_bp"], horizon)
        forward_rv = annualise(state["median_forward_bp"], horizon)
        breakeven_mean = straddle["breakeven_implied_vol_vs_mean_endpoint"]
        breakeven_median = straddle["breakeven_implied_vol_vs_median_endpoint"]

        years = horizon / (HOURS_PER_YEAR * 60)
        cost_at_trailing = STRADDLE_COEFFICIENT * trailing_iv * np.sqrt(years)
        mean_payoff = straddle["mean_absolute_endpoint_move"]

        rows[combo] = {
            "horizon_minutes": horizon,
            "signals": econ["n"],
            "assumed_implied_vol_from_trailing": round(trailing_iv, 4),
            "forward_realised_vol": round(forward_rv, 4),
            "forward_over_trailing": round(state["median_ratio"], 4),
            "breakeven_implied_vol_mean_payoff": round(breakeven_mean, 4),
            "breakeven_implied_vol_median_payoff": round(breakeven_median, 4),
            "headroom_mean_pp": round((breakeven_mean - trailing_iv) * 100, 2),
            "straddle_cost_pct_of_spot": round(cost_at_trailing * 100, 3),
            "mean_payoff_pct_of_spot": round(mean_payoff * 100, 3),
            "edge_pct_of_spot_before_fees": round((mean_payoff - cost_at_trailing) * 100, 3),
            "verdict": ("POSITIVE_BEFORE_FEES" if mean_payoff > cost_at_trailing
                        else "NEGATIVE_BEFORE_FEES"),
        }

    return {
        "record": "CRYPTO_BTC_VOL_P0_STRADDLE_GAP_V1",
        "status": "READ_ONLY_PROXY_ARITHMETIC",
        "no_pnl_backtest": True,
        "no_option_data_used": True,
        "assumption": "implied volatility is assumed equal to trailing realised volatility at "
                      "the signal. Implied normally trades above realised, so this is the most "
                      "generous assumption available to the option buyer.",
        "excluded_costs": ["option bid-ask spread", "exchange option fees", "exercise or "
                           "settlement cost", "slippage", "the volatility risk premium"],
        "interpretation": "a negative edge under the generous assumption cannot become positive "
                          "under the real one; a positive edge here would still have to survive "
                          "every excluded cost before it meant anything",
        "combos": rows,
    }


def main() -> int:
    payload = analyse()
    print("long straddle in the P1 high state, implied assumed equal to trailing realised\n")
    print(f"{'combo':10s} {'sig':>5s} {'assumed IV':>11s} {'fwd RV':>8s} "
          f"{'breakeven':>10s} {'cost %':>7s} {'payoff %':>9s} {'edge %':>8s}")
    for combo, row in payload["combos"].items():
        print(f"{combo:10s} {row['signals']:5d} "
              f"{row['assumed_implied_vol_from_trailing'] * 100:10.1f}% "
              f"{row['forward_realised_vol'] * 100:7.1f}% "
              f"{row['breakeven_implied_vol_mean_payoff'] * 100:9.1f}% "
              f"{row['straddle_cost_pct_of_spot']:7.3f} "
              f"{row['mean_payoff_pct_of_spot']:9.3f} "
              f"{row['edge_pct_of_spot_before_fees']:+8.3f}  {row['verdict']}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
