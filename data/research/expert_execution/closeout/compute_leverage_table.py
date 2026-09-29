"""AOA closeout: US-B leverage presets vs AOA 2019-2021 exposure. Descriptive only.

Runs the real paper engine offline (in-memory ledger, synthetic deep book, no network, no run
directory) to read notional, fee, margin and liquidation price exactly as the engine computes
them. It changes nothing in the engine, the UI or any run. Output: leverage_table.json.

    PYTHONPATH=backend .venv/bin/python data/research/expert_execution/closeout/compute_leverage_table.py
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

from app.crypto.paper.book import BookSide, Quote
from app.crypto.paper.config import build_config
from app.crypto.paper.engine import PaperEngine
from app.crypto.paper.instrument import RiskTierTable
from app.crypto.paper.sizing import max_entry, probe
from app.crypto.paper.account import SIDE_LONG

OUT = Path("data/research/expert_execution/closeout/leverage_table.json")
RUN_CONFIG = Path("data/runtime/crypto/paper/run_config.json")          # D4 local record, read only
PRESETS = ["1", "3", "5", "10", "20", "50"]                              # frontend LEVERAGE_PRESETS
CAPITAL_KRW = "10000000"                                                  # DEFAULT_STARTING_CAPITAL_KRW
REF_PRICE = Decimal("100000.0")                                           # illustrative; % results do not depend on it
MOVES = (0.0025, 0.005, 0.01, 0.02)


def engine_for(lev: str, rc: dict) -> PaperEngine:
    tiers = RiskTierTable.from_file(Path(rc["risk_limit_path"]))
    cfg = build_config(
        run_id="aoa-closeout-offline", starting_capital_krw=CAPITAL_KRW, fx_krw_per_usdt=rc["fx_krw_per_usdt"],
        fx_source=rc["fx_source"], fx_asof_utc=rc["fx_asof_utc"], fee_version=rc["fee_version"],
        fee_taker_rate=rc["fee_taker_rate"], fee_maker_rate=rc["fee_maker_rate"], fee_source=rc["fee_source"],
        fee_effective_date=rc["fee_effective_date"], slippage_model="NONE", slippage_bps="0", leverage=lev,
        risk_limit_source=rc["risk_limit_path"], risk_limit_sha256=tiers.source_sha256)
    e = PaperEngine(cfg, tiers)
    e.start(1_000)
    deep = "1000"                                                         # deep book: depth is not the constraint here
    e.apply_market(Quote(ts_ms=1_000, bids=BookSide.from_rows([[str(REF_PRICE - Decimal("0.1")), deep]], descending=True),
                         asks=BookSide.from_rows([[str(REF_PRICE), deep]], descending=False),
                         mark_price=REF_PRICE, last_price=REF_PRICE, funding_rate=None, next_funding_time_ms=None))
    return e


def us_b_table(rc: dict) -> list[dict]:
    rows = []
    taker, maker = float(rc["fee_taker_rate"]), float(rc["fee_maker_rate"])
    for lev in PRESETS:
        e = engine_for(lev, rc)
        eq = float(e.config.starting_capital_usdt)
        mx = max_entry(e, SIDE_LONG)
        notional = float(mx.notional)
        liq = float(mx.liquidation_price) if mx.liquidation_price is not None else None   # 0 = no positive liquidation price
        row = {"leverage": float(lev), "equity_usdt": eq, "equity_krw": float(CAPITAL_KRW),
               "max_qty_btc": float(mx.qty), "max_notional_usdt": notional,
               "max_notional_krw": notional * float(rc["fx_krw_per_usdt"]),
               "exposure_over_equity": notional / eq, "reserved_margin_usdt": float(mx.reserved_margin),
               "entry_fee_usdt": float(mx.fee), "risk_tier": mx.risk_tier,
               "liquidation_price": liq, "liq_distance_pct": (float(REF_PRICE) - liq) / float(REF_PRICE) * 100 if liq is not None else None,
               "taker_side_pct_of_equity": notional * taker / eq * 100,
               "taker_roundtrip_pct_of_equity": 2 * notional * taker / eq * 100,
               "maker_roundtrip_pct_of_equity": 2 * notional * maker / eq * 100,
               "adverse_move_pct_of_equity": {f"{m * 100:g}%": -notional * m / eq * 100 for m in MOVES}}
        # same leverage with a fixed 1x-equity position: leverage setting is not exposure
        one_x_qty = (Decimal(str(eq)) / REF_PRICE).quantize(Decimal("0.001"), rounding="ROUND_DOWN")
        p1 = probe(engine_for(lev, rc), SIDE_LONG, one_x_qty)
        row["at_1x_exposure"] = {"qty_btc": float(one_x_qty),
                                 "liquidation_price": float(p1.liquidation_price) if p1.liquidation_price is not None else None,
                                 "liq_distance_pct": (float(REF_PRICE) - float(p1.liquidation_price)) / float(REF_PRICE) * 100
                                 if p1.liquidation_price is not None else None}
        rows.append(row)
    return rows


def aoa_table() -> dict:
    e2 = pd.read_parquet("data/research/expert_execution/e2/episode_metrics.parquet")
    fs = pd.read_parquet("data/research/expert_execution/aoa_normalized/funding_snapshots.parquet")
    fs = fs[(fs.gross_xbt > 0) & (fs.equity_xbt > 0) & (~fs.open_dated_future)]
    fs["year"] = fs.transact_ts.dt.year
    out = {}
    for y in (2018, 2019, 2020, 2021):
        g = e2[e2.year == y]
        pk = g.peak_leverage.dropna()
        s = fs[(fs.year == y) & (fs.leverage >= 0.05)].leverage
        out[str(y)] = {
            "episodes": int(len(g)),
            "episode_peak_leverage": {"median": float(pk.median()), "p75": float(pk.quantile(.75)),
                                      "p90": float(pk.quantile(.9)), "max": float(pk.max())},
            "funding_snapshot_leverage_material": {"n": int(len(s)), "median": float(s.median()),
                                                   "p75": float(s.quantile(.75)), "p90": float(s.quantile(.9)),
                                                   "max": float(s.max())},
            "large_loss_episodes_roe_le_-5pct": int((g.roe <= -0.05).sum()),
            "large_loss_rate": float((g.roe <= -0.05).mean()),
            "liquidation_episodes": int(g.liquidation.sum()),
            "liquidation_episodes_nondust": int((g.liquidation & (g.roe < -0.001)).sum()),
        }
    lq = np.nanpercentile(e2.peak_leverage, [100 / 3, 200 / 3])
    b = np.where(e2.peak_leverage.isna(), "NA", np.where(e2.peak_leverage <= lq[0], "LOW",
                 np.where(e2.peak_leverage <= lq[1], "MID", "HIGH")))            # NA kept out, as in E2
    out["tercile_bounds"] = [float(x) for x in lq]
    out["large_loss_rate_by_tercile"] = {k: float((e2[b == k].roe <= -0.05).mean()) for k in ("LOW", "MID", "HIGH")}
    return out


def main():
    rc = json.loads(RUN_CONFIG.read_text())
    res = {"note": "descriptive only; no engine/UI/limit change",
           "inputs": {"capital_krw": CAPITAL_KRW, "fx_krw_per_usdt": rc["fx_krw_per_usdt"], "fx_source": rc["fx_source"],
                      "fee_taker": rc["fee_taker_rate"], "fee_maker": rc["fee_maker_rate"], "fee_version": rc["fee_version"],
                      "risk_limit_path": rc["risk_limit_path"], "ref_price_usdt": str(REF_PRICE),
                      "book": "synthetic single level 1000 BTC each side, spread 0.1"},
           "us_b": us_b_table(rc), "aoa": aoa_table()}
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
