"""Signed-position and episode reconstruction with BitMEX average-cost accounting in XBt.

Cost basis is BitMEX's own `execcost` of each fill, so the accounting is contract-agnostic
(inverse, quanto and linear all reduce to "PnL = -(sum of execcost over a closed quantity)").
A reducing fill removes basis pro rata (|closed| / |pos|), which is BitMEX's average-cost rule.

No market data is used anywhere in this module: unrealized PnL is deliberately absent.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .contracts import ContractSpec, avg_price_from_cost

ACTIONS = (
    "OPEN_LONG", "OPEN_SHORT", "ADD_LONG", "ADD_SHORT", "REDUCE_LONG", "REDUCE_SHORT",
    "CLOSE_LONG", "CLOSE_SHORT", "REVERSE_TO_LONG", "REVERSE_TO_SHORT",
    "FUNDING", "SETTLEMENT_CLOSE", "NOOP",
)
INCREASING = {"OPEN_LONG", "OPEN_SHORT", "ADD_LONG", "ADD_SHORT"}
DECREASING = {"REDUCE_LONG", "REDUCE_SHORT", "CLOSE_LONG", "CLOSE_SHORT", "REVERSE_TO_LONG",
              "REVERSE_TO_SHORT", "SETTLEMENT_CLOSE"}


def classify(pos_before: int, q: int) -> str:
    """Action of a fill of signed size q (q != 0) against position pos_before."""
    if q == 0:
        return "NOOP"
    if pos_before == 0:
        return "OPEN_LONG" if q > 0 else "OPEN_SHORT"
    if (pos_before > 0) == (q > 0):
        return "ADD_LONG" if q > 0 else "ADD_SHORT"
    long_before = pos_before > 0
    if abs(q) < abs(pos_before):
        return "REDUCE_LONG" if long_before else "REDUCE_SHORT"
    if abs(q) == abs(pos_before):
        return "CLOSE_LONG" if long_before else "CLOSE_SHORT"
    return "REVERSE_TO_SHORT" if long_before else "REVERSE_TO_LONG"


@dataclass
class _Episode:
    episode_id: int
    symbol: str
    direction: int
    start_ts: pd.Timestamp
    start_seq: int
    opened_by_reverse: bool
    max_abs_pos: int = 0
    fills: int = 0
    realized_gross_sat: float = 0.0
    fee_sat: float = 0.0
    funding_sat: float = 0.0
    liquidation_fills: int = 0
    end_ts: pd.Timestamp | None = None
    end_seq: int | None = None
    closed_by: str = "OPEN_AT_END"


def reconstruct_symbol(g: pd.DataFrame, spec: ContractSpec, next_episode_id: int = 0):
    """g: one symbol's executions sorted by seq. Returns (per-row frame, episode frame).

    Per-row columns: pos_before, pos_after, action, closed_qty, opened_qty, realized_gross_sat,
    avg_entry_before, cost_before_sat (open cost basis in XBt), episode_id (episode the row's opening/holding part belongs to),
    closes_episode_id (episode a decreasing row closes into).
    """
    n = len(g)
    seq = g["seq"].to_numpy()
    ts = g["transact_ts"].to_numpy()
    q_arr = g["signed_qty"].to_numpy(np.int64)
    ec = g["execcost"].to_numpy(np.float64)
    comm = g["execcomm"].to_numpy(np.float64)
    et = g["exectype"].to_numpy()
    liq = (g["text"] == "Liquidation").to_numpy()

    pos_b = np.zeros(n, np.int64); pos_a = np.zeros(n, np.int64)
    closed = np.zeros(n, np.int64); opened = np.zeros(n, np.int64)
    realized = np.zeros(n); avg_b = np.full(n, np.nan); cost_b = np.zeros(n)
    act = np.empty(n, dtype=object)
    ep_id = np.full(n, -1, np.int64); close_ep = np.full(n, -1, np.int64)

    pos, cost = 0, 0.0
    eps: list[_Episode] = []
    cur: _Episode | None = None
    nid = next_episode_id

    for i in range(n):
        pos_b[i] = pos
        cost_b[i] = cost
        avg_b[i] = avg_price_from_cost(spec.kind, spec.multiplier, pos, cost)
        if et[i] == "Funding":
            act[i] = "FUNDING"
            pos_a[i] = pos
            if cur is not None:
                cur.funding_sat += comm[i]
                ep_id[i] = cur.episode_id
            continue
        q = int(q_arr[i])
        a = classify(pos, q)
        if et[i] == "Settlement" and a in DECREASING:
            a = "SETTLEMENT_CLOSE" if abs(q) <= abs(pos) else a
        act[i] = a
        if a == "NOOP":
            pos_a[i] = pos
            continue
        if a in INCREASING:
            if pos == 0:
                cur = _Episode(nid, spec.symbol, 1 if q > 0 else -1, ts[i], int(seq[i]), False)
                eps.append(cur); nid += 1
            pos += q; cost += ec[i]; opened[i] = abs(q)
            cur.fee_sat += comm[i]; cur.fills += 1
            cur.liquidation_fills += int(liq[i])
            cur.max_abs_pos = max(cur.max_abs_pos, abs(pos))
            ep_id[i] = cur.episode_id
            pos_a[i] = pos
            continue
        # decreasing: close k contracts, possibly reverse with the remainder
        k = min(abs(q), abs(pos))
        frac_fill = k / abs(q)
        basis = cost * (k / abs(pos))
        r_gross = -(basis + ec[i] * frac_fill)
        realized[i] = r_gross
        closed[i] = k
        cost -= basis
        pos += int(np.sign(q)) * k
        cur.realized_gross_sat += r_gross
        cur.fee_sat += comm[i] * frac_fill
        cur.fills += 1
        cur.liquidation_fills += int(liq[i])
        close_ep[i] = cur.episode_id
        if pos == 0:
            cost = 0.0  # drop float residue; BitMEX basis is exactly zero when flat
            cur.end_ts, cur.end_seq = ts[i], int(seq[i])
            cur.closed_by = ("LIQUIDATION" if liq[i] else "SETTLEMENT" if et[i] == "Settlement"
                             else "REVERSE" if abs(q) > k else "CLOSE")
            rem = abs(q) - k
            if rem:
                cur = _Episode(nid, spec.symbol, 1 if q > 0 else -1, ts[i], int(seq[i]), True)
                eps.append(cur); nid += 1
                pos = int(np.sign(q)) * rem
                cost = ec[i] * (1 - frac_fill)
                opened[i] = rem
                cur.fee_sat += comm[i] * (1 - frac_fill)
                cur.fills += 1
                cur.liquidation_fills += int(liq[i])
                cur.max_abs_pos = abs(pos)
                ep_id[i] = cur.episode_id
            else:
                cur = None
        else:
            ep_id[i] = cur.episode_id
        pos_a[i] = pos

    rows = pd.DataFrame({
        "seq": seq, "pos_before": pos_b, "pos_after": pos_a, "action": act,
        "closed_qty": closed, "opened_qty": opened, "realized_gross_sat": realized,
        "avg_entry_before": avg_b, "cost_before_sat": cost_b, "episode_id": ep_id, "closes_episode_id": close_ep,
    })
    ep = pd.DataFrame([e.__dict__ for e in eps])
    if len(ep):
        ep["kind"] = spec.kind
    return rows, ep, (pos, cost)


def funding_snapshot_check(g: pd.DataFrame, rows: pd.DataFrame) -> dict:
    """Funding rows state |position| (lastqty) and its sign (homenotional). Compare with the
    reconstructed position immediately before each funding row."""
    m = g[["seq", "exectype", "lastqty", "homenotional"]].merge(rows[["seq", "pos_before"]], on="seq")
    f = m[m["exectype"] == "Funding"]
    stated = (np.sign(f["homenotional"]) * f["lastqty"]).astype(np.int64)
    diff = f["pos_before"].to_numpy() - stated.to_numpy()
    mism = f[diff != 0]
    return {"funding_rows": int(len(f)), "match": int((diff == 0).sum()),
            "mismatch": int((diff != 0).sum()),
            "first_mismatches": [
                {"seq": int(s), "reconstructed": int(p), "stated": int(t)}
                for s, p, t in zip(mism["seq"][:5], mism["pos_before"][:5], stated[diff != 0][:5])]}


def order_level(g: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """One row per orderID: action of its first fill, fill mix, contracts, maker share, span."""
    m = g[g["exectype"] == "Trade"][["seq", "orderid", "symbol", "transact_ts", "lastqty", "is_maker",
                                    "ordtype", "execinst", "triggered", "text", "side_sign", "execcomm",
                                    "year"]].merge(rows[["seq", "action", "realized_gross_sat", "pos_before"]], on="seq")
    m = m.sort_values("seq")
    # Liquidation and settlement fills all carry the zero orderID; each is its own system order.
    sys_ = m["orderid"].str.fullmatch(r"[0-]+")
    m.loc[sys_, "orderid"] = "SYS-" + m.loc[sys_, "seq"].astype(str)
    m["maker_qty"] = np.where(m["is_maker"] == True, m["lastqty"], 0)  # noqa: E712 (nullable bool)
    agg = m.groupby("orderid", sort=False).agg(
        symbol=("symbol", "first"), first_ts=("transact_ts", "first"), last_ts=("transact_ts", "last"),
        year=("year", "first"), first_action=("action", "first"), fills=("seq", "size"),
        qty=("lastqty", "sum"), maker_qty=("maker_qty", "sum"), side=("side_sign", "first"),
        ordtype=("ordtype", "first"), execinst=("execinst", "first"), triggered=("triggered", "first"),
        text=("text", "first"), realized_gross_sat=("realized_gross_sat", "sum"),
        fee_sat=("execcomm", "sum"), pos_before_first=("pos_before", "first"),
        any_reverse=("action", lambda a: bool(a.str.startswith("REVERSE").any())),
    )
    agg["maker_share"] = agg["maker_qty"] / agg["qty"]
    return agg.reset_index()
