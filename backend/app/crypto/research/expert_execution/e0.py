"""E0 orchestrator: ingest -> normalize -> reconstruct -> audit. Writes JSON/CSV/parquet only under
data/research/expert_execution/. Run from the repo root:

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.e0 --zip <path>

No market data is read. Every number that would need one is reported as a data requirement.
"""
from __future__ import annotations

import argparse
import json
import resource
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import contracts as K
from . import ingest as I
from . import positions as P

OUT = I.BASE
REPORTS = I.BASE / "aoa_reports"
SAT = K.SAT_PER_XBT
HOLD_BUCKETS = [(0, 60, "<1m"), (60, 300, "1-5m"), (300, 1800, "5-30m"), (1800, 14400, "30m-4h"),
                (14400, 86400, "4h-24h"), (86400, np.inf, "24h+")]


def _j(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
    if isinstance(o, (pd.Timestamp,)): return o.isoformat()
    if isinstance(o, np.ndarray): return o.tolist()
    if isinstance(o, np.bool_): return bool(o)
    raise TypeError(type(o))


def dump(obj, name: str, base: Path = OUT) -> Path:
    p = base / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=_j))
    return p


def _flat(series: pd.Series) -> dict:
    return {"|".join(map(str, k)) if isinstance(k, tuple) else str(k): v for k, v in series.items()}


def _share(a, b):
    return float(a) / float(b) if b else None


# ---------------------------------------------------------------------------------------- schema
SEMANTICS = {
    "date": "UTC calendar date of transacttime (string)",
    "execid": "unique execution id (UUID); primary key",
    "orderid": "order id; one order has many fills. Funding/settlement/liquidation use the zero UUID or ''",
    "clordid": "client order id; always empty in this export",
    "clordlinkid": "always empty",
    "account": "account alias; single value 'aoa'",
    "symbol": "BitMEX instrument",
    "side": "Buy/Sell of THIS fill; '' on funding rows",
    "lastqty": "fill size in contracts (XBTUSD: USD; quanto: contracts; XBT-quoted: coin units)",
    "lastpx": "fill price (funding rows: funding reference price, Settlement: settle price)",
    "lastliquidityind": "AddedLiquidity=maker, RemovedLiquidity=taker, '' on funding",
    "orderqty": "order size at time of the fill", "price": "order limit price",
    "displayqty": "iceberg display qty; '' or 0", "stoppx": "stop trigger price for Stop/StopLimit",
    "pegoffsetvalue": "always empty", "pegpricetype": "always empty",
    "currency": "quote currency of the contract (USD, USDT, XBT)",
    "settlcurrency": "settlement currency; always XBt (satoshi) in this ledger",
    "exectype": "Trade / Funding / Settlement",
    "ordtype": "Limit / Market / Stop / StopLimit",
    "timeinforce": "GoodTillCancel / ImmediateOrCancel / AtTheClose (funding, settlement) / FillOrKill",
    "execinst": "ParticipateDoNotInitiate=post-only, Close=reduce/close, LastPrice/IndexPrice=stop trigger ref",
    "contingencytype": "always empty", "ordstatus": "order status after this fill",
    "triggered": "StopOrderTriggered when a stop fired", "workingindicator": "order still working",
    "ordrejreason": "always empty", "leavesqty": "order qty remaining after fill",
    "cumqty": "order qty filled so far", "avgpx": "order average fill price so far",
    "commission": "fee RATE (negative = rebate); funding rows: funding rate signed to the account",
    "tradepublishindicator": "PublishTrade / DoNotPublishTrade / ''",
    "text": "free text; 'Liquidation', 'Funding', 'Settlement', 'Position Close from www.bitmex.com', 'Triggered: ...'",
    "trdmatchid": "exchange trade match id (funding: funding event id)",
    "execcost": "signed XBt value of the fill (funding: position value at funding price). Accounting ground truth",
    "execcomm": "fee in XBt (positive=paid, negative=rebate); funding: funding paid(+)/received(-)",
    "homenotional": "signed size in base asset (XBT for XBTUSD, ETH for ETHUSD ...); sign = position/fill direction",
    "foreignnotional": "signed size in quote currency",
    "transacttime": "UTC event time 'YYYY-MM-DD HH:MM:SS[.ffffff]'",
    "timestamp": "UTC record time; equals transacttime except funding (+~12ms)",
}


def schema_profile(raw: pd.DataFrame) -> dict:
    prof = {}
    for c in raw.columns:
        if c in ("src_file", "src_row"):
            continue
        s = raw[c]
        nonempty = s[s != ""]
        num = pd.to_numeric(nonempty, errors="coerce")
        numeric = len(nonempty) > 0 and num.notna().all()
        e = {"empty": int((s == "").sum()), "nonempty": int(len(nonempty)),
             "unique": int(s.nunique()), "semantic": SEMANTICS.get(c, "")}
        if numeric:
            e["dtype"] = "int" if (num % 1 == 0).all() else "float"
            e["min"], e["max"] = float(num.min()), float(num.max())
        else:
            e["dtype"] = "string"
            e["max_len"] = int(nonempty.str.len().max()) if len(nonempty) else 0
        e["samples"] = nonempty.drop_duplicates().head(5).tolist()
        if e["unique"] <= 30:
            e["values"] = s.value_counts().to_dict()
        prof[c] = e
    return prof


# ---------------------------------------------------------------------------------------- quality
def quality(raw: pd.DataFrame, df: pd.DataFrame) -> dict:
    tr = df[df.exectype == "Trade"]
    q = {
        "rows": len(df),
        "exact_duplicate_rows": int(raw.drop(columns=["src_file", "src_row"]).duplicated().sum()),
        "duplicate_execid": int(df.execid.duplicated().sum()),
        "unparsed_transacttime": int(df.transact_ts.isna().sum()),
        "unparsed_timestamp": int(df.record_ts.isna().sum()),
        "record_minus_transact_seconds_max": float((df.record_ts - df.transact_ts).dt.total_seconds().max()),
        "invalid_side": int((~raw.side.isin(["Buy", "Sell", ""])).sum()),
        "empty_side_by_exectype": df[df.side == ""].exectype.value_counts().to_dict(),
        "zero_or_negative_qty": int((df.lastqty <= 0).sum()),
        "zero_price_rows": df[df.lastpx <= 0][["execid", "symbol", "exectype", "text"]].to_dict("records"),
        "missing_symbol": int((df.symbol == "").sum()),
        "orders": {
            "unique_orderid_trade": int(tr.orderid.nunique()),
            "fills_per_order_median": float(tr.groupby("orderid").size().median()),
            "fills_per_order_p99": float(tr.groupby("orderid").size().quantile(.99)),
            "zero_uuid_rows_by_text": df[df.orderid.str.fullmatch(r"[0-]+")].text.value_counts().to_dict(),
            "empty_orderid_rows_by_exectype": df[df.orderid == ""].exectype.value_counts().to_dict(),
            "orders_spanning_files": int((tr.groupby("orderid").src_file.nunique() > 1).sum()),
        },
        "in_file_time_order": {},
        "file_boundaries": [],
        "fee_liquidity_sign_inconsistency": {
            "maker_with_positive_rate": int(((tr.is_maker == True) & (tr.commission > 0)).sum()),  # noqa: E712
            "taker_with_negative_rate": int(((tr.is_maker == False) & (tr.commission < 0)).sum()),  # noqa: E712
        },
        "commission_rate_range": [float(tr.commission.min()), float(tr.commission.max())],
        "execcomm_vs_rate_check": None,
        "execcost_identity": None,
    }
    for f, g in raw.groupby("src_file"):
        t = I.parse_ts(g.transacttime)
        d = t.diff().dt.total_seconds()
        q["in_file_time_order"][f] = {"backward_steps": int((d < 0).sum()),
                                      "max_backward_seconds": float(-d.min()) if (d < 0).any() else 0.0,
                                      "note": "files are not strictly time-sorted; normalized data is sorted by transacttime, then file, then row"}
    files = df.groupby("src_file").transact_ts.agg(["min", "max", "size"]).sort_values("min")
    prev = None
    for f, r in files.iterrows():
        q["file_boundaries"].append({"file": f, "first": r["min"], "last": r["max"], "rows": int(r["size"]),
                                     "gap_from_previous_hours": None if prev is None else
                                     (r["min"] - prev).total_seconds() / 3600,
                                     "overlap_with_previous": None if prev is None else bool(r["min"] <= prev)})
        prev = r["max"]
    # execcomm should equal |execcost| * rate (BitMEX rounds to satoshi)
    impl = (tr.execcost.abs().astype(float) * tr.commission).round()
    diff = (impl - tr.execcomm.astype(float)).abs()
    q["execcomm_vs_rate_check"] = {"within_1_sat": _share((diff <= 1).sum(), len(tr)),
                                   "max_abs_diff_sat": float(diff.max())}
    return q


# ---------------------------------------------------------------------------------------- aggregates
def aggregates(df: pd.DataFrame, rows: pd.DataFrame, specs: dict) -> dict:
    tr = df[df.exectype == "Trade"].merge(rows[["seq", "action"]], on="seq")
    tr["xbt"] = tr.execcost.abs().astype(float) / SAT
    tr["base"] = tr.symbol.str[:3].map(lambda b: {"XBT": "BTC", "ETH": "ETH"}.get(b, "OTHER"))
    tot_xbt = tr.xbt.sum()
    by_sym = tr.groupby("symbol").agg(fills=("seq", "size"), xbt_value=("xbt", "sum"),
                                      orders=("orderid", "nunique")).sort_values("fills", ascending=False)
    by_sym["share_fills"] = by_sym.fills / len(tr)
    by_sym["share_xbt"] = by_sym.xbt_value / tot_xbt

    def mt(frame):
        n = len(frame); x = frame.xbt.sum()
        mk = frame[frame.is_maker == True]; tk = frame[frame.is_maker == False]  # noqa: E712
        return {"fills": n, "maker_fills": len(mk), "taker_fills": len(tk),
                "unknown_fills": n - len(mk) - len(tk),
                "maker_share_fills": _share(len(mk), n), "maker_share_xbt": _share(mk.xbt.sum(), x),
                "maker_share_contracts": _share(mk.lastqty.sum(), frame.lastqty.sum()),
                "fee_paid_xbt": float(frame.execcomm.clip(lower=0).sum() / SAT),
                "rebate_received_xbt": float(-frame.execcomm.clip(upper=0).sum() / SAT),
                "net_fee_xbt": float(frame.execcomm.sum() / SAT),
                "net_fee_bp_of_value": _share(frame.execcomm.sum() / SAT * 1e4, x)}

    act_group = tr.action.map(lambda a: "OPEN" if a.startswith("OPEN") else "ADD" if a.startswith("ADD")
                              else "REDUCE" if a.startswith("REDUCE") else "CLOSE" if a.startswith("CLOSE")
                              else "REVERSE" if a.startswith("REVERSE") else "SETTLEMENT" if a.startswith("SETTLE") else a)
    tr["act_group"] = act_group
    liq = df.text == "Liquidation"
    out = {
        "total_rows": len(df),
        "rows_by_exectype": df.exectype.value_counts().to_dict(),
        "trade_fills": len(tr),
        "unique_orders_trade": int(tr.orderid.nunique()),
        "unique_execids": int(df.execid.nunique()),
        "coverage": {"first": df.transact_ts.min(), "last": df.transact_ts.max()},
        "symbols": int(df.symbol.nunique()),
        "by_symbol": by_sym.reset_index().to_dict("records"),
        "by_base_asset": tr.groupby("base").agg(fills=("seq", "size"), xbt_value=("xbt", "sum"))
            .assign(share_fills=lambda d: d.fills / len(tr), share_xbt=lambda d: d.xbt_value / tot_xbt)
            .reset_index().to_dict("records"),
        "by_year": tr.groupby("year").agg(fills=("seq", "size"), orders=("orderid", "nunique"),
                                          xbt_value=("xbt", "sum")).reset_index().to_dict("records"),
        "by_month_fills": {str(k): int(v) for k, v in tr.groupby(tr.transact_ts.dt.strftime("%Y-%m")).size().items()},
        "side": tr.side.value_counts().to_dict(),
        "side_xbt": tr.groupby("side").xbt.sum().to_dict(),
        "ordtype_fills": tr.ordtype.value_counts().to_dict(),
        "ordtype_xbt": tr.groupby("ordtype").xbt.sum().to_dict(),
        "ordtype_by_liquidity_fills": _flat(tr.groupby(["ordtype", "lastliquidityind"]).size()),
        "execinst_fills": tr.execinst.value_counts().to_dict(),
        "timeinforce_fills": tr.timeinforce.value_counts().to_dict(),
        "maker_taker_all": mt(tr),
        "maker_taker_by_year": {int(y): mt(g) for y, g in tr.groupby("year")},
        "maker_taker_by_action": {a: mt(g) for a, g in tr.groupby("act_group")},
        "maker_taker_by_year_action": {f"{y}|{a}": mt(g) for (y, a), g in tr.groupby(["year", "act_group"])},
        "maker_taker_xbtusd_by_year": {int(y): mt(g) for y, g in tr[tr.symbol == "XBTUSD"].groupby("year")},
        "events": {
            "funding_rows": int((df.exectype == "Funding").sum()),
            "funding_net_paid_xbt": float(df[df.exectype == "Funding"].execcomm.sum() / SAT),
            "settlement_rows": int((df.exectype == "Settlement").sum()),
            "liquidation_fills": int(liq.sum()),
            "liquidation_by_symbol_year": _flat(df[liq].groupby(["symbol", "year"]).size()),
            "liquidation_xbt_value": float(df[liq].execcost.abs().sum() / SAT),
            "stop_triggered_fills": int((df.triggered == "StopOrderTriggered").sum()),
            "position_close_button_fills": int(df.text.str.startswith("Position Close").sum()),
        },
        "contract_specs": {s: v.to_dict() for s, v in specs.items()},
    }
    return out


# ---------------------------------------------------------------------------------------- wallet
def wallet_report(w: pd.DataFrame, meta: dict, m: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    done = w[w.transactstatus == "Completed"]
    bal = w.walletbalance_sat.astype(float).to_numpy()
    # Balance check. walletbalance is the balance after a posting batch (all 12:00 postings of a date
    # share one value), rows are not in time order even across dates, and some values were saved in
    # 6-significant-digit scientific notation by a spreadsheet. So the check is date-level:
    # cumulative Completed amount through date D must equal SOME stated balance on date D.
    sci = w.walletbalance.str.contains("E", case=False)
    cum = w[w.transactstatus == "Completed"].groupby("date").amount_xbt_sat.sum().astype(float).cumsum()
    chain = {"dates": 0, "match": 0, "match_within_sci_rounding": 0, "break": 0, "break_dates": []}
    for d, g in w.groupby("date", sort=True):
        if d not in cum.index:
            continue
        chain["dates"] += 1
        c = cum[d]
        exact = g.loc[~sci[g.index], "walletbalance_sat"].astype(float)
        approx = g.loc[sci[g.index], "walletbalance_sat"].astype(float)
        if (np.abs(exact - c) <= 0.5).any():
            chain["match"] += 1
        elif len(approx) and (np.abs(approx - c) <= np.abs(approx) * 5e-6 + 1).any():
            chain["match_within_sci_rounding"] += 1
        else:
            chain["break"] += 1
            if len(chain["break_dates"]) < 20:
                chain["break_dates"].append({"date": d, "cumulative_sat": c,
                                             "stated": g.walletbalance_sat.astype(float).tolist()})
    by = lambda t: done[done.transacttype == t]  # noqa: E731
    wd_all = w[w.transacttype == "Withdrawal"]
    # reconstruction vs wallet RealisedPNL: BitMEX posts each symbol's realised PnL daily at 12:00 UTC
    m = m.copy()
    m["net"] = m.realized_gross_sat - m.execcomm.astype(float)
    m["post_date"] = ((m.transact_ts - pd.Timedelta(hours=12)).dt.floor("D") + pd.Timedelta(days=1)).dt.strftime("%Y-%m-%d")
    a = m.groupby(["post_date", "symbol"]).net.sum()
    b = done[done.transacttype == "RealisedPNL"].groupby(["date", "address"]).amount_xbt_sat.sum().astype(float)
    j = pd.concat([a.rename("reconstructed_sat"), b.rename("wallet_sat")], axis=1).fillna(0.0)
    j["diff_sat"] = j.reconstructed_sat - j.wallet_sat
    j.index.names = ["post_date", "symbol"]
    last_post = done[done.transacttype == "RealisedPNL"].date.max()
    in_range = j[j.index.get_level_values(0) <= last_post]
    ad = in_range.diff_sat.abs()
    rep = {
        **meta,
        "status_counts": w.transactstatus.value_counts().to_dict(),
        "type_counts": w.transacttype.value_counts().to_dict(),
        "deposit_xbt": float(by("Deposit").amount_xbt_sat.sum() / SAT),
        "deposit_count": int(len(by("Deposit"))),
        "withdrawal_completed_xbt": float(-by("Withdrawal").amount_xbt_sat.sum() / SAT),
        "withdrawal_completed_count": int(len(by("Withdrawal"))),
        "withdrawal_canceled_xbt": float(-wd_all[wd_all.transactstatus == "Canceled"].amount_xbt_sat.sum() / SAT),
        "withdrawal_canceled_count": int((wd_all.transactstatus == "Canceled").sum()),
        "withdrawal_all_rows_xbt": float(-wd_all.amount_xbt_sat.sum() / SAT),
        "withdrawal_by_year_xbt": {k: float(-v / SAT) for k, v in by("Withdrawal").groupby(by("Withdrawal").date.str[:4]).amount_xbt_sat.sum().items()},
        "realised_pnl_wallet_xbt": float(by("RealisedPNL").amount_xbt_sat.sum() / SAT),
        "realised_pnl_wallet_by_year_xbt": {k: float(v / SAT) for k, v in by("RealisedPNL").groupby(by("RealisedPNL").date.str[:4]).amount_xbt_sat.sum().items()},
        "first_balance_xbt": float(bal[0] / SAT), "ending_balance_xbt": float(bal[-1] / SAT),
        "ending_balance_from_amounts_xbt": float(done.amount_xbt_sat.sum() / SAT),
        "ending_date": w.date.iloc[-1],
        "max_balance_xbt": float(np.nanmax(bal) / SAT),
        "walletbalance_scientific_notation_rows": int(sci.sum()),
        "walletbalance_scientific_notation_dates": [w.date[sci].min(), w.date[sci].max()] if sci.any() else None,
        "date_level_balance_check": chain,
        "identity_deposit_minus_withdraw_plus_pnl_xbt": float(
            (by("Deposit").amount_xbt_sat.sum() + by("Withdrawal").amount_xbt_sat.sum()
             + by("RealisedPNL").amount_xbt_sat.sum()) / SAT),
        "reconciliation": {
            "method": "ledger realized gross (avg-cost on execcost) - execcomm (trades+funding+settlement), "
                      "bucketed to the 12:00 UTC daily posting [D-1 12:00, D 12:00) per symbol, vs wallet RealisedPNL (address=symbol)",
            "cells": int(len(in_range)),
            "within_1_sat": _share((ad <= 1).sum(), len(ad)),
            "within_1000_sat": _share((ad < 1000).sum(), len(ad)),
            "within_0.001_xbt": _share((ad < 1e5).sum(), len(ad)),
            "max_abs_diff_xbt": float(ad.max() / SAT),
            "sum_reconstructed_xbt": float(in_range.reconstructed_sat.sum() / SAT),
            "sum_wallet_xbt": float(in_range.wallet_sat.sum() / SAT),
            "sum_diff_xbt": float(in_range.diff_sat.sum() / SAT),
            "reconstructed_after_last_wallet_post_xbt": float(j[j.index.get_level_values(0) > last_post].reconstructed_sat.sum() / SAT),
            "worst_cells": in_range.reindex(ad.sort_values(ascending=False).index[:10]).reset_index().to_dict("records"),
        },
    }
    return rep, j.reset_index()


# ---------------------------------------------------------------------------------------- funding-snapshot risk
def wallet_balance_at(w: pd.DataFrame, t_ns: np.ndarray) -> np.ndarray:
    """Wallet balance just before each time. RealisedPNL posts at 12:00 UTC of its date; deposit and
    withdrawal times were destroyed in the export (mm:ss only), so they are placed at 00:00 of their date."""
    done = w[w.transactstatus == "Completed"].copy()
    hours = np.where(done.transacttype == "RealisedPNL", 12, 0)
    post = done.date_utc + pd.to_timedelta(hours, unit="h")
    post_ns = post.astype("datetime64[ns]").to_numpy().astype(np.int64)
    order = np.argsort(post_ns, kind="stable")
    post_ns = post_ns[order]
    bal = done.walletbalance_sat.astype(float).to_numpy()[order]
    idx = np.searchsorted(post_ns, t_ns, side="left") - 1
    return np.where(idx >= 0, bal[np.clip(idx, 0, None)], np.nan)


def funding_snapshots(df: pd.DataFrame, rows: pd.DataFrame, w: pd.DataFrame) -> pd.DataFrame:
    """Per funding timestamp: gross/net perp exposure in XBT (funding execcost = value at the funding
    price), unrealized PnL (value - cost basis), equity estimate, effective leverage."""
    m = df.merge(rows[["seq", "pos_before", "cost_before_sat", "realized_gross_sat"]], on="seq")
    f = m[m.exectype == "Funding"].copy()
    f["value_sat"] = f.execcost.astype(float)            # signed like the opening cost of the position
    f["unreal_sat"] = f.value_sat - f.cost_before_sat    # = -(cost + closing cost at funding price)
    snap = f.groupby("transact_ts").agg(
        gross_xbt=("value_sat", lambda v: v.abs().sum() / SAT),
        unreal_xbt=("unreal_sat", lambda v: v.sum() / SAT),
        n_perps=("symbol", "size"))
    xb = f[f.symbol == "XBTUSD"].set_index("transact_ts")
    snap["xbtusd_pos"] = xb.pos_before
    snap["xbtusd_value_xbt"] = xb.value_sat / SAT       # sign: + = short for inverse (opening cost convention)
    snap["xbtusd_unreal_xbt"] = xb.unreal_sat / SAT
    t_ns = snap.index.values.astype("datetime64[ns]").astype(np.int64)
    snap["wallet_xbt"] = wallet_balance_at(w, t_ns) / SAT
    # realized since the last 12:00 posting (not yet in the wallet), events strictly before the snapshot
    m["net"] = m.realized_gross_sat - m.execcomm.astype(float)
    ts_ns = m.transact_ts.values.astype("datetime64[ns]").astype(np.int64)
    order = np.argsort(ts_ns, kind="stable")
    cum = np.concatenate([[0.0], np.cumsum(m.net.to_numpy()[order])])
    ts_sorted = ts_ns[order]
    day = 86_400 * 10**9
    last_post = ((t_ns - 12 * 3600 * 10**9) // day) * day + 12 * 3600 * 10**9
    since = cum[np.searchsorted(ts_sorted, t_ns, "left")] - cum[np.searchsorted(ts_sorted, last_post, "left")]
    snap["pending_realized_xbt"] = since / SAT
    snap["equity_xbt"] = snap.wallet_xbt + snap.pending_realized_xbt + snap.unreal_xbt
    snap["leverage"] = snap.gross_xbt / snap.equity_xbt
    # dated-futures positions open at the snapshot have no mark in the ledger -> flagged
    fut = [s for s in df.symbol.unique() if not K._is_perpetual(s)]
    fr = m[m.symbol.isin(fut) & (m.exectype != "Funding")][["symbol", "transact_ts", "pos_before", "signed_qty"]]
    open_fut = np.zeros(len(t_ns), bool)
    for s, g in fr.groupby("symbol"):
        g = g.sort_values("transact_ts")
        g_ns = g.transact_ts.values.astype("datetime64[ns]").astype(np.int64)
        after = (g.pos_before + g.signed_qty).to_numpy()
        k = np.searchsorted(g_ns, t_ns, "left") - 1
        open_fut |= (k >= 0) & (after[np.clip(k, 0, None)] != 0)
    snap["open_dated_future"] = open_fut
    return snap.reset_index()


# ---------------------------------------------------------------------------------------- episodes
def episode_stats(ep: pd.DataFrame, orders: pd.DataFrame, rows_all: pd.DataFrame) -> dict:
    e = ep[ep.end_ts.notna()].copy()
    e["hold_s"] = (pd.to_datetime(e.end_ts) - pd.to_datetime(e.start_ts)).dt.total_seconds()
    e["year"] = pd.to_datetime(e.start_ts).dt.year
    e["net_sat"] = e.realized_gross_sat - e.fee_sat - e.funding_sat

    def dist(h):
        if not len(h):
            return {"n": 0}
        d = {"n": int(len(h)), **{f"p{int(p*100)}_s": float(h.quantile(p)) for p in (.25, .5, .75, .9, .99)}}
        for lo, hi, lab in HOLD_BUCKETS:
            d[lab] = _share(((h >= lo) & (h < hi)).sum(), len(h))
        d["24h+"] = _share((h >= 86400).sum(), len(h))
        return d

    # order-level scale-in / scale-out per episode (orders assigned by the episode of their first fill)
    o = orders.copy()
    om = o.merge(rows_all[["seq", "episode_id", "closes_episode_id"]], left_on="first_seq", right_on="seq", how="left")
    om["ep"] = np.where(om.first_action.str.startswith(("OPEN", "ADD")), om.episode_id, om.closes_episode_id)
    grp = om.groupby("ep")
    per = pd.DataFrame({
        "opens": grp.first_action.apply(lambda a: a.str.startswith("OPEN").sum()),
        "adds": grp.first_action.apply(lambda a: a.str.startswith("ADD").sum()),
        "reduces": grp.first_action.apply(lambda a: a.str.startswith("REDUCE").sum()),
        "closes": grp.first_action.apply(lambda a: a.str.startswith(("CLOSE", "REVERSE", "SETTLE")).sum()),
    })
    per = per.reindex(e.episode_id).fillna(0)
    adds_ts = om[om.first_action.str.startswith("ADD")].sort_values("first_ts").groupby("ep").first_ts
    spacing = adds_ts.apply(lambda s: s.diff().dt.total_seconds().median() if len(s) > 1 else np.nan).dropna()
    size = om[om.first_action.str.startswith(("OPEN", "ADD"))].sort_values("first_ts")
    size = size[size.symbol == "XBTUSD"]
    prog = size.groupby("ep").qty.apply(lambda q: (q.iloc[1:] / q.iloc[0]).median() if len(q) > 1 else np.nan).dropna()
    return {
        "episodes_total": int(len(ep)), "episodes_closed": int(len(e)),
        "open_at_end": ep[ep.end_ts.isna()][["symbol", "direction", "start_ts", "max_abs_pos"]].to_dict("records"),
        "closed_by": ep.closed_by.value_counts().to_dict(),
        "opened_by_reverse": int(ep.opened_by_reverse.sum()),
        "hold_all": dist(e.hold_s),
        "hold_by_direction": {("LONG" if d > 0 else "SHORT"): dist(g.hold_s) for d, g in e.groupby("direction")},
        "hold_by_year": {int(y): dist(g.hold_s) for y, g in e.groupby("year")},
        "hold_xbtusd": dist(e[e.symbol == "XBTUSD"].hold_s),
        "win_rate_net": _share((e.net_sat > 0).sum(), len(e)),
        "win_rate_net_xbtusd": _share((e[e.symbol == "XBTUSD"].net_sat > 0).sum(), (e.symbol == "XBTUSD").sum()),
        "orders_per_episode": {c: {"mean": float(per[c].mean()), "median": float(per[c].median()),
                                   "p90": float(per[c].quantile(.9)), "max": float(per[c].max())} for c in per},
        "episodes_with_any_add": _share((per.adds > 0).sum(), len(per)),
        "episodes_with_partial_reduce": _share((per.reduces > 0).sum(), len(per)),
        "median_add_spacing_s_per_episode": {"median": float(spacing.median()) if len(spacing) else None,
                                             "p25": float(spacing.quantile(.25)) if len(spacing) else None,
                                             "p75": float(spacing.quantile(.75)) if len(spacing) else None},
        "xbtusd_add_size_vs_first_entry_median": {"median": float(prog.median()), "p25": float(prog.quantile(.25)),
                                                  "p75": float(prog.quantile(.75)), "episodes": int(len(prog))},
    }, e


# ---------------------------------------------------------------------------------------- claims (ledger-only parts)
def claims(orders: pd.DataFrame, rows_all: pd.DataFrame, df: pd.DataFrame, snap: pd.DataFrame, w: pd.DataFrame) -> dict:
    out = {}
    o = orders.copy()
    o["net_sat"] = o.realized_gross_sat - o.fee_sat
    is_stop = o.ordtype.isin(["Stop", "StopLimit"]) | (o.triggered == "StopOrderTriggered")
    is_liq = o.text == "Liquidation"
    dec = o.first_action.str.startswith(("REDUCE", "CLOSE", "REVERSE", "SETTLE"))
    inc = o.first_action.str.startswith(("OPEN", "ADD"))

    # entry-order counts (definition candidates for the "10,862 entries" figure)
    x = o[o.symbol == "XBTUSD"]
    out["entry_order_counts"] = {
        "all_symbols": {"OPEN": int(o.first_action.str.startswith("OPEN").sum()),
                        "ADD": int(o.first_action.str.startswith("ADD").sum()),
                        "OPEN+ADD": int(inc.sum()),
                        "REVERSE": int(o.first_action.str.startswith("REVERSE").sum()),
                        "OPEN+REVERSE": int(o.first_action.str.startswith(("OPEN", "REVERSE")).sum())},
        "xbtusd": {"OPEN": int(x.first_action.str.startswith("OPEN").sum()),
                   "ADD": int(x.first_action.str.startswith("ADD").sum()),
                   "OPEN+ADD": int(x.first_action.str.startswith(("OPEN", "ADD")).sum()),
                   "OPEN+REVERSE": int(x.first_action.str.startswith(("OPEN", "REVERSE")).sum())},
        "taker_entry_orders_all": int((inc & (o.maker_share < 0.5)).sum()),
        "market_ordtype_entry_orders_all": int((inc & (o.ordtype == "Market")).sum()),
        "by_year_open_add": {int(k): int(v) for k, v in o[inc].groupby("year").size().items()},
    }

    # CLAIM 5: stop share among losing closing orders, by year
    c5 = {}
    for basis in ("gross", "net"):
        col = "realized_gross_sat" if basis == "gross" else "net_sat"
        lc = o[dec & (o[col] < 0)]
        c5[basis] = {int(y): {"loss_close_orders": int(len(g)),
                              "stop_orders": int(is_stop[g.index].sum()),
                              "stop_share": _share(is_stop[g.index].sum(), len(g)),
                              "liquidation_orders": int(is_liq[g.index].sum()),
                              "close_button_orders": int(g.text.str.startswith("Position Close").sum()),
                              "stop_or_liquidation_share": _share((is_stop[g.index] | is_liq[g.index]).sum(), len(g))}
                     for y, g in lc.groupby("year")}
    out["claim5_stop_share_in_loss_closes"] = c5

    # CLAIM 2 (last-trade basis only): ADD orders whose first fill price is worse than the avg entry
    r = rows_all.set_index("seq")
    adds = o[o.first_action.str.startswith("ADD")].copy()
    fr = df.set_index("seq").loc[adds.first_seq, ["lastpx", "symbol"]]
    adds["fill_px"] = fr.lastpx.to_numpy()
    adds["avg_entry"] = r.loc[adds.first_seq, "avg_entry_before"].to_numpy()
    adds["dir"] = np.sign(adds.pos_before_first)
    adds["underwater_last"] = np.where(adds.dir > 0, adds.fill_px < adds.avg_entry, adds.fill_px > adds.avg_entry)
    out["claim2_adds_underwater_last_trade_basis"] = {
        "definition": "ADD order (first fill increases an existing position); underwater if its first fill price is "
                      "below (long) / above (short) the position's average entry just before it. Last-trade basis, NOT mark.",
        "all": {"orders": int(len(adds)), "share": _share(adds.underwater_last.sum(), len(adds))},
        "xbtusd": {"orders": int((adds.symbol == "XBTUSD").sum()),
                   "share": _share(adds[adds.symbol == "XBTUSD"].underwater_last.sum(), (adds.symbol == "XBTUSD").sum())},
        "by_year": {int(y): _share(g.underwater_last.sum(), len(g)) for y, g in adds.groupby("year")},
        "by_direction": {("LONG" if d > 0 else "SHORT"): _share(g.underwater_last.sum(), len(g)) for d, g in adds.groupby("dir")},
    }

    # CLAIM 6 (sampled): share of funding snapshots with an open perp position whose unrealized PnL < 0
    s = snap[snap.gross_xbt > 0]
    out["claim6_unrealized_loss_share_at_funding_snapshots"] = {
        "definition": "8h funding snapshots (04/12/20 UTC) with any open perpetual; unrealized = value at funding price - cost basis. "
                      "Sampled, equal weight per snapshot; dated futures not marked.",
        "snapshots_with_position": int(len(s)),
        "share_unrealized_negative": _share((s.unreal_xbt < 0).sum(), len(s)),
        "by_year": {int(y): _share((g.unreal_xbt < 0).sum(), len(g)) for y, g in s.groupby(s.transact_ts.dt.year)},
        "xbtusd_only": _share((s.xbtusd_unreal_xbt.dropna() < 0).sum(), s.xbtusd_unreal_xbt.notna().sum()),
        "snapshots_total": int(len(snap)),
        "snapshots_flat": int((snap.gross_xbt == 0).sum()),
        "note": "flat periods have no funding row, so time spent flat between snapshots is invisible here",
    }

    # CLAIM 7: entry size after a large realized loss vs same-month baseline (XBTUSD, contracts=USD)
    xo = o[o.symbol == "XBTUSD"].sort_values("first_ts")
    ent = xo[xo.first_action.str.startswith(("OPEN", "ADD"))][["first_ts", "qty"]].copy()
    ent["month"] = ent.first_ts.dt.strftime("%Y-%m")
    losses = xo[dec.loc[xo.index] & (xo.net_sat < 0)]
    c7 = {}
    for pct in (0.90, 0.95, 0.99):
        thr = losses.net_sat.quantile(1 - pct)  # most negative tail
        big = losses[losses.net_sat <= thr]
        e_ns = ent.first_ts.values.astype("datetime64[ns]").astype(np.int64)
        in_win = np.zeros(len(ent), bool)
        ratios = []
        for t in big.last_ts:
            t0 = np.datetime64(t.tz_convert(None), "ns").astype(np.int64)
            lo, hi = np.searchsorted(e_ns, t0, "right"), np.searchsorted(e_ns, t0 + 6 * 3600 * 10**9, "right")
            in_win[lo:hi] = True
        base = ent[~in_win].groupby("month").qty.median()
        post = ent[in_win]
        rr = (post.qty / post.month.map(base)).dropna()
        c7[f"loss_tail_{int(round((1-pct)*100))}pct"] = {
            "loss_threshold_xbt": float(thr / SAT), "loss_events": int(len(big)),
            "entries_in_6h_after": int(len(post)),
            "size_ratio_median": float(rr.median()) if len(rr) else None,
            "size_ratio_mean": float(rr.mean()) if len(rr) else None,
            "size_ratio_of_medians": float(post.qty.median() / ent[~in_win].qty.median()) if len(post) else None}
        ratios.append(rr)
    out["claim7_size_after_large_loss"] = {
        "definition": "XBTUSD closing orders with net realized loss in the worst k% tail; entries (OPEN/ADD orders) "
                      "within 6h after the loss order's last fill; ratio = entry qty / same-month median entry qty outside those windows",
        **c7}

    # CLAIM 8: XBTUSD position at 00:00 UTC and 00:00 KST (15:00 UTC)
    xr = df[df.symbol == "XBTUSD"][["seq", "transact_ts"]].merge(rows_all[["seq", "pos_after"]], on="seq").sort_values("seq")
    xr_ns = xr.transact_ts.values.astype("datetime64[ns]").astype(np.int64)
    c8 = {}
    for lab, hh in (("00:00_UTC", 0), ("00:00_KST(15:00_UTC)", 15)):
        days = pd.date_range("2018-03-06", "2021-12-31", freq="D", tz="UTC") + pd.Timedelta(hours=hh)
        d_ns = days.values.astype("datetime64[ns]").astype(np.int64)
        k = np.searchsorted(xr_ns, d_ns, "left") - 1
        pos = np.where(k >= 0, xr.pos_after.to_numpy()[np.clip(k, 0, None)], 0)
        yrs = days.year
        c8[lab] = {"days": int(len(pos)), "short_share": _share((pos < 0).sum(), len(pos)),
                   "material": {f"abs_pos_ge_{th}_usd": {"days": int((np.abs(pos) >= th).sum()),
                                                        "short_share_of_material": _share((pos <= -th).sum(), (np.abs(pos) >= th).sum()),
                                                        "short_days": int((pos <= -th).sum()), "long_days": int((pos >= th).sum())}
                                for th in (100_000, 1_000_000)},
                   "long_share": _share((pos > 0).sum(), len(pos)), "flat_share": _share((pos == 0).sum(), len(pos)),
                   "mean_pos_usd": float(pos.mean()), "median_pos_usd": float(np.median(pos)),
                   "by_year": {int(y): {"short_share": _share((pos[yrs == y] < 0).sum(), (yrs == y).sum()),
                                        "mean_pos_usd": float(pos[yrs == y].mean())} for y in np.unique(yrs)}}
    out["claim8_xbtusd_position_at_midnight"] = c8

    # CLAIM 10: worst wallet realized-PnL days and the three named dates
    wp = w[(w.transacttype == "RealisedPNL") & (w.transactstatus == "Completed")]
    daily = wp.groupby("date").amount_xbt_sat.sum().astype(float) / SAT
    bal = w[w.transactstatus == "Completed"].groupby("date").walletbalance_sat.last().astype(float) / SAT
    out["claim10_drawdown_days"] = {
        "worst_10_realized_days_xbt": daily.nsmallest(10).to_dict(),
        "named_dates": {d: {"wallet_realised_xbt": float(daily.get(d, np.nan)),
                            "rank_among_worst": int((daily < daily.get(d, np.inf)).sum()) + 1 if d in daily else None,
                            "wallet_balance_end_of_day_xbt": float(bal.get(d, np.nan))}
                        for d in ("2018-09-21", "2018-09-22", "2021-05-05", "2021-05-06", "2021-05-19", "2021-05-20")},
        "note": "wallet date D holds realized PnL of [D-1 12:00, D 12:00) UTC, so a crash on D shows on D and D+1",
    }
    return out


def case_studies(df: pd.DataFrame, rows_all: pd.DataFrame, snap: pd.DataFrame) -> dict:
    m = df.merge(rows_all[["seq", "pos_before", "pos_after", "action", "realized_gross_sat"]], on="seq")
    res = {}
    for d in ("2018-09-21", "2021-05-05", "2021-05-19"):
        t0 = pd.Timestamp(d, tz="UTC") - pd.Timedelta(hours=12)
        t1 = pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=36)
        g = m[(m.transact_ts >= t0) & (m.transact_ts < t1)]
        s = snap[(snap.transact_ts >= t0) & (snap.transact_ts < t1)]
        per = {}
        for sym, h in g[g.exectype != "Funding"].groupby("symbol"):
            per[sym] = {"fills": int(len(h)),
                        "pos_start": int(h.pos_before.iloc[0]), "pos_end": int(h.pos_after.iloc[-1]),
                        "max_long": int(max(h.pos_after.max(), 0)), "max_short": int(min(h.pos_after.min(), 0)),
                        "reversals": int(h.action.str.startswith("REVERSE").sum()),
                        "add_fills": int(h.action.str.startswith("ADD").sum()),
                        "realized_gross_xbt": float(h.realized_gross_sat.sum() / SAT),
                        "fees_xbt": float(h.execcomm.sum() / SAT),
                        "liquidation_fills": int((h.text == "Liquidation").sum()),
                        "px_min": float(h.lastpx.min()), "px_max": float(h.lastpx.max())}
        res[d] = {"window_utc": [t0, t1], "by_symbol": per,
                  "funding_snapshots": s[["transact_ts", "gross_xbt", "unreal_xbt", "equity_xbt", "leverage"]].to_dict("records")}
    return res


def leverage_summary(snap: pd.DataFrame) -> dict:
    s = snap[(snap.gross_xbt > 0) & (snap.equity_xbt > 0)]
    clean = s[~s.open_dated_future]
    def d(x):
        return {"n": int(len(x)), "median": float(x.median()), "p90": float(x.quantile(.9)), "max": float(x.max())} if len(x) else {"n": 0}
    return {
        "definition": "gross perpetual exposure at funding price (sum |funding execcost|) / (wallet balance before snapshot "
                      "+ realized since last 12:00 posting + unrealized at funding price)",
        "all": d(clean.leverage),
        "material_all": d(clean[clean.leverage >= 0.05].leverage),
        "material_by_year": {int(y): d(g.leverage) for y, g in clean[clean.leverage >= 0.05].groupby(clean.transact_ts.dt.year)},
        "dust_snapshots_leverage_lt_0.05": int((clean.leverage < 0.05).sum()),
        "by_year": {int(y): d(g.leverage) for y, g in clean.groupby(clean.transact_ts.dt.year)},
        "snapshots_excluded_open_dated_future": int(s.open_dated_future.sum()),
        "equity_nonpositive_snapshots": int((snap.equity_xbt <= 0).sum()),
        "gross_xbt_by_year": {int(y): d(g.gross_xbt) for y, g in clean.groupby(clean.transact_ts.dt.year)},
    }


# ---------------------------------------------------------------------------------------- main
def run(zip_path: Path) -> dict:
    t_start = time.time()
    REPORTS.mkdir(parents=True, exist_ok=True)
    zc = I.check_zip(zip_path)
    if not zc["safe"]:
        raise SystemExit(f"unsafe zip: {zc['unsafe_reasons']}")
    pre = {"zip_path": str(zip_path), "zip_bytes": zip_path.stat().st_size,
           "zip_mtime": pd.Timestamp(zip_path.stat().st_mtime, unit="s", tz="UTC"),
           "zip_sha256": I.sha256_file(zip_path), "zip_check": zc}
    inv = I.inventory()
    pd.DataFrame(inv).to_csv(OUT / "inventory.csv", index=False)
    pre["extracted_bytes"] = int(sum(r["bytes"] for r in inv))

    raw = I.load_raw_executions()
    df = I.normalize_executions(raw)
    w, wmeta = I.load_wallet()
    dump(schema_profile(raw), "schema_profile.json")
    wraw = I.read_raw_strings(next(I.RAW_DIR.glob("aoa-wallet-*.csv")))
    dump(schema_profile(wraw.assign(src_file="", src_row=0)), "aoa_reports/wallet_schema_profile.json")

    specs = K.infer_specs(df[df.exectype == "Trade"])
    R, E, ends, fchk = [], [], {}, {}
    nid = 0
    for sym, g in df.groupby("symbol", sort=True):
        r, e, end = P.reconstruct_symbol(g, specs[sym], nid)
        nid += len(e); R.append(r); E.append(e)
        ends[sym] = {"pos": int(end[0]), "cost_sat": float(end[1])}
        fchk[sym] = P.funding_snapshot_check(g, r)
    rows_all = pd.concat(R, ignore_index=True)
    ep = pd.concat(E, ignore_index=True)
    m = df.merge(rows_all, on="seq")
    orders = P.order_level(m, rows_all)
    first_seq = m[m.exectype == "Trade"].sort_values("seq").copy()
    sys_ = first_seq.orderid.str.fullmatch(r"[0-]+")
    first_seq.loc[sys_, "orderid"] = "SYS-" + first_seq.loc[sys_, "seq"].astype(str)
    orders = orders.merge(first_seq.groupby("orderid").seq.first().rename("first_seq"), on="orderid")

    q = quality(raw, df)
    q["execcost_identity"] = {s: {"kind": v.kind, "p99_rel_dev": v.identity_p99_rel_dev} for s, v in specs.items()}
    dump(q, "quality_report.json")

    agg = aggregates(df, rows_all, specs)
    wrep, recon = wallet_report(w, wmeta, m)
    snap = funding_snapshots(df, rows_all, w)
    est, eclosed = episode_stats(ep, orders, rows_all)
    cl = claims(orders, rows_all, df, snap, w)
    lev = leverage_summary(snap)
    cases = case_studies(df, rows_all, snap)

    agg["position_reconstruction"] = {
        "funding_snapshot_check": fchk,
        "funding_snapshots_matched": int(sum(v["match"] for v in fchk.values())),
        "funding_snapshots_total": int(sum(v["funding_rows"] for v in fchk.values())),
        "end_positions": {k: v for k, v in ends.items() if v["pos"]},
        "action_counts_fills": rows_all.action.value_counts().to_dict(),
        "action_counts_orders": orders.first_action.value_counts().to_dict(),
    }
    agg["wallet"] = wrep
    agg["episodes"] = est
    agg["leverage"] = lev
    dump(agg, "aggregate_summary.json")
    dump(cl, "aoa_reports/claims_ledger_only.json")
    dump(cases, "aoa_reports/drawdown_case_studies.json")
    dump(pre, "aoa_reports/preflight.json")

    sizes = {}
    sizes["executions.parquet"] = I.write_parquet(df, I.NORM_DIR / "executions.parquet")
    sizes["positions.parquet"] = I.write_parquet(rows_all, I.NORM_DIR / "positions.parquet")
    sizes["episodes.parquet"] = I.write_parquet(ep.assign(start_ts=pd.to_datetime(ep.start_ts, utc=True),
                                                          end_ts=pd.to_datetime(ep.end_ts, utc=True)),
                                                I.NORM_DIR / "episodes.parquet")
    sizes["orders.parquet"] = I.write_parquet(orders, I.NORM_DIR / "orders.parquet")
    sizes["wallet.parquet"] = I.write_parquet(w, I.NORM_DIR / "wallet.parquet")
    sizes["funding_snapshots.parquet"] = I.write_parquet(snap, I.NORM_DIR / "funding_snapshots.parquet")
    sizes["wallet_reconciliation.parquet"] = I.write_parquet(recon, I.NORM_DIR / "wallet_reconciliation.parquet")
    perf = {"processing_seconds": round(time.time() - t_start, 1),
            "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
            "zip_bytes": pre["zip_bytes"], "extracted_bytes": pre["extracted_bytes"],
            "normalized_bytes": {k: int(v) for k, v in sizes.items()},
            "normalized_total_bytes": int(sum(sizes.values()))}
    dump(perf, "aoa_reports/performance.json")
    return perf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True, type=Path)
    a = ap.parse_args()
    print(json.dumps(run(a.zip), indent=1, default=_j))


if __name__ == "__main__":
    main()
