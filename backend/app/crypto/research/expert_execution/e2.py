"""E2 position management study (XBTUSD episodes), per the frozen contract
docs/crypto/expert_execution/AOA_E2_POSITION_MANAGEMENT_CONTRACT_V1.md. Section numbers refer to it.

    PYTHONPATH=backend .venv/bin/python -m app.crypto.research.expert_execution.e2

Inputs: E0 normalized ledger (read only), E2 minute grid (grid.py, built from the E1 archive).
"""
from __future__ import annotations

import hashlib
import json
import resource
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import grid as G
from .e0 import wallet_balance_at
from .e1 import cluster_boot

CONTRACT = Path("docs/crypto/expert_execution/AOA_E2_POSITION_MANAGEMENT_CONTRACT_V1.md")
E2 = Path("data/research/expert_execution/e2")
FREEZE = E2 / "contract_freeze_v1.json"
NORM = Path("data/research/expert_execution/aoa_normalized")
BYBIT_FEE_JSON = Path("data/runtime/crypto/reference/fee_source_verification_v1.json")

MULT = -1e8                      # XBTUSD inverse multiplier (E0 contract model)
SAT = 1e8
MIN_NS = 60 * 10**9
HOUR_NS = 60 * MIN_NS
ENTRY_H_MIN = (15, 60, 240, 480, 1440)
ADD_H_MIN = (15, 60, 240, 480)
PRIMARY_ENTRY_H = 240
PATH_MISSING_MAX = 0.05
STOP_ROE = -0.02
RULE_X = (0.005, 0.010)
RULE_MAX_ADDS = 4
OUTCOME = ((0.005, "SUCCESS"), (-0.005, "FLAT"), (-0.05, "FAILED"))   # >= thresholds, else CATASTROPHIC
BOOT_B, SEED = 2000, 20260927


# ------------------------------------------------------------------ contract
def verify_contract() -> str:
    frozen = json.loads(FREEZE.read_text())["sha256"]
    now = hashlib.sha256(CONTRACT.read_bytes()).hexdigest()
    if now != frozen:
        raise SystemExit(f"contract hash mismatch: frozen {frozen} now {now}")
    return now


def outcome_class(roe: float) -> str:
    if roe >= 0.005:
        return "SUCCESS"
    if roe > -0.005:
        return "FLAT"
    if roe > -0.05:
        return "FAILED"
    return "CATASTROPHIC"


# ------------------------------------------------------------------ accounting engine (4)
def exec_cost(q: float, p: float) -> float:
    return q * MULT / p


@dataclass
class Book:
    """Inverse average-cost book. Events append (t, pos, cost, net) snapshots for path evaluation."""
    pos: float = 0.0
    cost: float = 0.0
    realized: float = 0.0
    fees: float = 0.0
    funding: float = 0.0
    ev_t: list = field(default_factory=list)
    ev_pos: list = field(default_factory=list)
    ev_cost: list = field(default_factory=list)
    ev_net: list = field(default_factory=list)
    ev_px: list = field(default_factory=list)

    @property
    def net(self) -> float:
        return self.realized - self.fees - self.funding

    def trade(self, t: int, q: float, ec: float, fee: float, px: float) -> None:
        if q == 0:
            return
        if self.pos == 0 or np.sign(q) == np.sign(self.pos):
            self.pos += q
            self.cost += ec
        else:
            k = min(abs(q), abs(self.pos))
            if k < abs(q) - 1e-9:
                raise ValueError("a single episode trade may not reverse the position")
            basis = self.cost * (k / abs(self.pos))
            self.realized += -(basis + ec)
            self.cost -= basis
            self.pos += q
            if abs(self.pos) < 1e-9:
                self.pos, self.cost = 0.0, 0.0
        self.fees += fee
        self._snap(t, px)

    def trade_at(self, t: int, q: float, px: float, rate: float) -> None:
        ec = exec_cost(q, px)
        self.trade(t, q, ec, abs(ec) * rate, px)

    def fund(self, t: int, amount: float, px: float) -> None:
        self.funding += amount
        self._snap(t, px)

    def unreal(self, p) -> float:
        return exec_cost(self.pos, p) - self.cost

    def _snap(self, t, px):
        self.ev_t.append(t); self.ev_pos.append(self.pos); self.ev_cost.append(self.cost)
        self.ev_net.append(self.net); self.ev_px.append(px)


# ------------------------------------------------------------------ grid access
class Grid:
    def __init__(self, g: pd.DataFrame):
        self.b = g.b.to_numpy()
        self.b0 = int(self.b[0])
        self.mid = g.mid_close.to_numpy()
        self.hi = g.mid_hi.to_numpy()
        self.lo = g.mid_lo.to_numpy()
        self.ntr = g.n_trades.to_numpy()
        self.year = pd.to_datetime(self.b, utc=True).year.to_numpy()

    def idx(self, b):
        return ((np.asarray(b) - self.b0) // MIN_NS).astype(np.int64)

    def M(self, b):
        i = self.idx(b)
        ok = (i >= 0) & (i < len(self.mid))
        return np.where(ok, self.mid[np.clip(i, 0, len(self.mid) - 1)], np.nan)

    @staticmethod
    def floor(t):
        return (np.asarray(t) // MIN_NS) * MIN_NS

    @staticmethod
    def ceil(t):
        t = np.asarray(t)
        return -((-t) // MIN_NS) * MIN_NS

    def context(self, t) -> dict:
        """60 completed minutes before t: vol (std of 1m log returns), ret60, trades in last 60 minutes."""
        b0 = self.floor(t)
        i0 = self.idx(b0)
        ks = np.arange(61)
        ii = i0[:, None] - ks[None, :]
        ok = ii >= 0
        m = np.where(ok, self.mid[np.clip(ii, 0, None)], np.nan)
        lr = np.log(m[:, :-1] / m[:, 1:])
        full = np.isfinite(lr).all(axis=1)
        vol = np.where(full, np.std(np.where(full[:, None], lr, 0.0), axis=1, ddof=1), np.nan)
        ret = np.where(full, np.log(m[:, 0] / m[:, 60]), np.nan)
        # minutes [b0-60m, b0) are grid rows i0-59 .. i0 (row i holds minute ending at b_i)
        cs = np.r_[0, np.cumsum(self.ntr)]
        lo_i = np.clip(i0 - 59, 0, len(self.ntr))
        hi_i = np.clip(i0 + 1, 0, len(self.ntr))
        trades = cs[hi_i] - cs[lo_i]
        return {"vol60": vol, "ret60": ret, "trades60": trades}


# ------------------------------------------------------------------ path metrics (4, 5, 6)
def minute_bounds(t_e: int, t_x: int) -> np.ndarray:
    """Boundaries b of the minutes [b - 1m, b) that start at or after t_e and before t_x."""
    first_b = int(Grid.ceil(t_e)) + MIN_NS
    bs = np.arange(first_b, t_x + MIN_NS, MIN_NS, dtype=np.int64)
    return bs[bs - MIN_NS < t_x]


def slice_book(b: Book, t: int) -> Book:
    """The book as it stood strictly before t (from the replay snapshots)."""
    k = int(np.searchsorted(np.asarray(b.ev_t, np.int64), t, "left"))
    out = Book(ev_t=b.ev_t[:k], ev_pos=b.ev_pos[:k], ev_cost=b.ev_cost[:k], ev_net=b.ev_net[:k], ev_px=b.ev_px[:k])
    if k:
        out.pos, out.cost, out.realized = b.ev_pos[k - 1], b.ev_cost[k - 1], b.ev_net[k - 1]
    return out


def path_metrics(book: Book, t_e: int, t_x: int, grid: Grid, equity0: float) -> dict:
    ev_t = np.asarray(book.ev_t, np.int64)
    ev_pos = np.asarray(book.ev_pos, float)
    ev_cost = np.asarray(book.ev_cost, float)
    ev_net = np.asarray(book.ev_net, float)
    ev_px = np.asarray(book.ev_px, float)
    ev_pnl = ev_net + exec_cost(ev_pos, ev_px) - ev_cost
    bs = minute_bounds(t_e, t_x)
    out = {"minutes": int(len(bs))}
    if len(bs):
        i = grid.idx(bs)
        inr = (i >= 0) & (i < len(grid.mid))
        ic = np.clip(i, 0, len(grid.mid) - 1)
        close = np.where(inr, grid.mid[ic], np.nan)
        hi = np.where(inr, grid.hi[ic], np.nan)
        lo = np.where(inr, grid.lo[ic], np.nan)
        k = np.searchsorted(ev_t, bs - MIN_NS, "right") - 1
        valid = (k >= 0) & np.isfinite(close)
        kk = np.clip(k, 0, None)
        pos, cost, net = ev_pos[kk], ev_cost[kk], ev_net[kk]
        with np.errstate(invalid="ignore", divide="ignore"):
            u_close = exec_cost(pos, close) - cost
            u_hi = exec_cost(pos, hi) - cost
            u_lo = exec_cost(pos, lo) - cost
        worst = net + np.minimum(u_hi, u_lo)
        best = net + np.maximum(u_hi, u_lo)
        pnl_close = net + u_close
        out["missing_share"] = float(1 - valid.mean())
        v = valid
        out["underwater_minutes"] = int(((u_close < 0) & v & (pos != 0)).sum())
        out["valid_minutes"] = int((v & (pos != 0)).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            notional = np.abs(pos) / close                      # |pos| USD / price = XBT
            eq = equity0 + pnl_close / SAT
            lev = notional / eq
        out["exposure_xbt_days"] = float(np.nansum(np.where(v, notional, 0.0)) / 1440)
        out["peak_leverage"] = float(np.nanmax(np.where(v, lev, np.nan))) if v.any() else np.nan
        cand_t = np.r_[ev_t, bs[v]]
        cand_lo = np.r_[ev_pnl, worst[v]]
        cand_hi = np.r_[ev_pnl, best[v]]
        cand_close = np.r_[ev_pnl, pnl_close[v]]
    else:
        out.update({"missing_share": 0.0, "underwater_minutes": 0, "valid_minutes": 0, "exposure_xbt_days": 0.0,
                    "peak_leverage": np.nan})
        cand_t, cand_lo, cand_hi, cand_close = ev_t, ev_pnl, ev_pnl, ev_pnl
    o = np.argsort(cand_t, kind="stable")
    cand_t, cand_lo, cand_hi, cand_close = cand_t[o], cand_lo[o], cand_hi[o], cand_close[o]
    j_mae = int(np.argmin(cand_lo)); j_mfe = int(np.argmax(cand_hi))
    mae, mfe = min(0.0, float(cand_lo[j_mae])), max(0.0, float(cand_hi[j_mfe]))
    out["mae_xbt"], out["mfe_xbt"] = mae / SAT, mfe / SAT
    out["mae_roe"], out["mfe_roe"] = mae / SAT / equity0, mfe / SAT / equity0
    out["t_mae_h"] = (cand_t[j_mae] - t_e) / HOUR_NS if mae < 0 else 0.0
    out["t_mfe_h"] = (cand_t[j_mfe] - t_e) / HOUR_NS if mfe > 0 else 0.0
    rec = np.nan
    if mae < 0:
        after = np.where((cand_t > cand_t[j_mae]) & (cand_close >= 0))[0]
        if len(after):
            rec = (cand_t[after[0]] - cand_t[j_mae]) / HOUR_NS
    out["recovery_h"] = rec
    return out


# ------------------------------------------------------------------ data loading
def load_inputs():
    ex = pd.read_parquet(NORM / "executions.parquet", columns=[
        "seq", "orderid", "symbol", "exectype", "text", "transact_ts", "signed_qty", "lastqty", "lastpx",
        "execcost", "execcomm", "commission", "is_maker"])
    pos = pd.read_parquet(NORM / "positions.parquet")
    ep = pd.read_parquet(NORM / "episodes.parquet")
    w = pd.read_parquet(NORM / "wallet.parquet")
    m = ex.merge(pos, on="seq")
    m["t"] = m.transact_ts.values.astype("datetime64[ns]").astype(np.int64)
    return m, ep, w


def pending_realized_fn(m: pd.DataFrame):
    """Ledger realized net (all symbols) since the last 12:00 UTC wallet posting, strictly before t."""
    net = (m.realized_gross_sat - m.execcomm.astype(float)).to_numpy()
    t = m.t.to_numpy()
    o = np.argsort(t, kind="stable")
    ts, cum = t[o], np.r_[0.0, np.cumsum(net[o])]
    day = 86_400 * 10**9

    def f(tq):
        tq = np.asarray(tq, np.int64)
        last_post = ((tq - 12 * HOUR_NS) // day) * day + 12 * HOUR_NS
        return cum[np.searchsorted(ts, tq, "left")] - cum[np.searchsorted(ts, last_post, "left")]
    return f


def equity_at(tq, w, pend, grid: Grid, pos_before, cost_before) -> np.ndarray:
    """XBT. Wallet + pending realized + XBTUSD unrealized at M(floor(t)) of the position held just before t."""
    tq = np.asarray(tq, np.int64)
    wb = wallet_balance_at(w, tq)
    mid = grid.M(grid.floor(tq))
    with np.errstate(invalid="ignore", divide="ignore"):
        u = np.where(np.asarray(pos_before) != 0, exec_cost(np.asarray(pos_before, float), mid) - np.asarray(cost_before), 0.0)
    return (wb + pend(tq) + np.nan_to_num(u)) / SAT


# ------------------------------------------------------------------ episode event streams (2)
def episode_streams(m: pd.DataFrame, ep: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Per XBTUSD episode: rows (t, seq, orderid, q, ec, fee, px, kind, is_maker, funding) where q is the
    signed quantity this episode sees. Reverse fills are split by quantity (E0 rule)."""
    x = m[m.symbol == "XBTUSD"].sort_values("seq")
    rows = []
    for r in x.itertuples(index=False):
        if r.exectype == "Funding":
            if r.episode_id >= 0:
                rows.append((r.episode_id, r.t, r.seq, "", 0.0, 0.0, 0.0, float(r.lastpx), "FUND", None, float(r.execcomm)))
            continue
        q = float(r.signed_qty)
        if q == 0:
            continue
        ec, fee = float(r.execcost), float(r.execcomm)
        if r.closes_episode_id >= 0:
            k = float(r.closed_qty)
            f = k / abs(q)
            rows.append((r.closes_episode_id, r.t, r.seq, r.orderid, np.sign(q) * k, ec * f, fee * f, float(r.lastpx),
                         "DEC", r.is_maker, 0.0))
            if r.opened_qty > 0 and r.episode_id >= 0:
                rows.append((r.episode_id, r.t, r.seq, r.orderid, np.sign(q) * float(r.opened_qty), ec * (1 - f),
                             fee * (1 - f), float(r.lastpx), "INC", r.is_maker, 0.0))
        elif r.episode_id >= 0:
            rows.append((r.episode_id, r.t, r.seq, r.orderid, q, ec, fee, float(r.lastpx), "INC", r.is_maker, 0.0))
    df = pd.DataFrame(rows, columns=["episode_id", "t", "seq", "orderid", "q", "ec", "fee", "px", "kind", "is_maker", "funding"])
    return {k: g.reset_index(drop=True) for k, g in df.groupby("episode_id")}


def classify_orders(s: pd.DataFrame) -> pd.DataFrame:
    """Order-level events inside one episode (2): ENTRY / ADD / REDUCE / FULL_CLOSE."""
    tr = s[s.kind != "FUND"].copy()
    tr["pos_after"] = tr.q.cumsum()
    first_order = tr.orderid.iloc[0]
    zeroing = set(tr.loc[tr.pos_after.abs() < 1e-9, "orderid"])
    g = tr.groupby("orderid", sort=False)
    o = pd.DataFrame({"t_first": g.t.first(), "t_last": g.t.last(), "kind_first": g.kind.first(),
                      "qty": g.q.apply(lambda q: q.abs().sum()), "ec": g.ec.sum(), "fee": g.fee.sum(),
                      "vwap": g.apply(lambda h: h.q.abs().sum() / (h.q.abs() / h.px).sum(), include_groups=False),
                      "maker_share": g.apply(lambda h: (h.q.abs() * h.is_maker.astype(float)).sum() / h.q.abs().sum(),
                                             include_groups=False)})
    o["type"] = np.where(o.index == first_order, "ENTRY",
                np.where(o.index.isin(zeroing), "FULL_CLOSE",
                np.where(o.kind_first == "INC", "ADD", "REDUCE")))
    o["rate"] = o.fee / o.ec.abs()
    return o.reset_index().sort_values("t_first", kind="stable").reset_index(drop=True)


# ------------------------------------------------------------------ actual and counterfactual books (9)
def replay_actual(s: pd.DataFrame) -> Book:
    b = Book()
    for r in s.itertuples(index=False):
        if r.kind == "FUND":
            b.fund(r.t, r.funding, r.px)
        else:
            b.trade(r.t, r.q, r.ec, r.fee, r.px)
    return b


def _pos_track(s: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    tr = s[s.kind != "FUND"]
    return tr.t.to_numpy(), tr.q.cumsum().to_numpy()


def _actual_pos_at(t_arr, pos_arr, t, before: bool):
    k = np.searchsorted(t_arr, t, "left" if before else "right") - 1
    return pos_arr[k] if k >= 0 else 0.0


def cf_book(s: pd.DataFrame, orders: pd.DataFrame, mode: str, grid: Grid | None = None, x: float = 0.0,
            maker_rate_fn=None) -> Book:
    """mode: NO_ADD, NO_PARTIAL, RULE (fixed adverse-move add on top of NO_ADD)."""
    t_arr, pos_arr = _pos_track(s)
    b = Book()
    entry = orders[orders.type == "ENTRY"].iloc[0]
    init_rows = s[(s.orderid == entry.orderid) & (s.kind == "INC")]
    events = []                                              # (t, prio, kind, payload)
    for r in init_rows.itertuples(index=False):
        events.append((r.t, 0, "ACT", r))
    others = orders[orders.type != "ENTRY"]
    for o in others.itertuples(index=False):
        if o.type == "ADD":
            if mode == "NO_PARTIAL":
                for r in s[(s.orderid == o.orderid) & (s.kind == "INC")].itertuples(index=False):
                    events.append((r.t, 0, "ACT", r))
        elif o.type == "REDUCE":
            if mode in ("NO_ADD", "RULE"):
                before = abs(_actual_pos_at(t_arr, pos_arr, o.t_first, before=True))
                frac = min(1.0, o.qty / before) if before > 0 else 0.0
                events.append((o.t_first, 1, "RED", (frac, o.vwap, o.rate)))
        else:  # FULL_CLOSE
            events.append((o.t_last, 2, "CLOSE", (o.vwap, o.rate)))
    for r in s[s.kind == "FUND"].itertuples(index=False):
        events.append((r.t, 1, "FUND", r))
    if mode == "RULE":
        t_x = int(orders[orders.type == "FULL_CLOSE"].t_last.max())
        d = np.sign(init_rows.q.sum())
        last = float(init_rows.q.abs().sum() / (init_rows.q.abs() / init_rows.px).sum())
        unit = float(init_rows.q.abs().sum())
        bs = minute_bounds(int(init_rows.t.max()), t_x)
        i = np.clip(grid.idx(bs), 0, len(grid.mid) - 1)
        lo, hi = grid.lo[i], grid.hi[i]
        adds = 0
        for bb, l, h in zip(bs, lo, hi):
            if adds >= RULE_MAX_ADDS:
                break
            trig = last * (1 - x) if d > 0 else last * (1 + x)
            if (d > 0 and l <= trig) or (d < 0 and h >= trig):
                events.append((int(bb), 0, "RULEADD", (d * unit, trig, maker_rate_fn(int(bb)))))
                last = trig
                adds += 1
    events.sort(key=lambda e: (e[0], e[1]))
    for t, _, kind, p in events:
        if kind == "ACT":
            b.trade(p.t, p.q, p.ec, p.fee, p.px)
        elif kind == "RULEADD":
            b.trade_at(t, p[0], p[1], p[2])
        elif kind == "RED":
            if b.pos != 0:
                b.trade_at(t, -b.pos * p[0], p[1], p[2])
        elif kind == "CLOSE":
            if b.pos != 0:
                b.trade_at(t, -b.pos, p[0], p[1])
        elif kind == "FUND":
            act = abs(_actual_pos_at(t_arr, pos_arr, t, before=True))
            scale = abs(b.pos) / act if act > 0 else 0.0
            b.fund(t, p.funding * scale, p.px)
    return b


def stop_cf(actual: Book, t_e: int, t_x: int, grid: Grid, equity0: float, taker_rate_fn) -> tuple[float, bool, int | None]:
    """CF4: exit at the close of the first minute whose worst-case path PnL <= STOP_ROE x equity0."""
    ev_t = np.asarray(actual.ev_t, np.int64)
    ev_pos = np.asarray(actual.ev_pos, float); ev_cost = np.asarray(actual.ev_cost, float)
    ev_net = np.asarray(actual.ev_net, float)
    bs = minute_bounds(t_e, t_x)
    thr = STOP_ROE * equity0 * SAT
    if len(bs):
        i = np.clip(grid.idx(bs), 0, len(grid.mid) - 1)
        k = np.searchsorted(ev_t, bs - MIN_NS, "right") - 1
        kk = np.clip(k, 0, None)
        pos, cost, net = ev_pos[kk], ev_cost[kk], ev_net[kk]
        with np.errstate(invalid="ignore", divide="ignore"):
            worst = net + np.minimum(exec_cost(pos, grid.hi[i]) - cost, exec_cost(pos, grid.lo[i]) - cost)
        hit = np.where((k >= 0) & (pos != 0) & np.isfinite(worst) & (worst <= thr))[0]
        if len(hit):
            j = hit[0]
            px = grid.mid[i[j]]
            if np.isfinite(px):
                val = exec_cost(pos[j], px)
                pnl = net[j] + val - cost[j] - abs(val) * taker_rate_fn(int(bs[j]))
                return pnl / SAT, True, int(bs[j])
    return actual.net / SAT, False, None


# ------------------------------------------------------------------ main
def modal_rate_fns(m: pd.DataFrame):
    tr = m[(m.symbol == "XBTUSD") & (m.exectype == "Trade")].copy()
    tr["ym"] = tr.transact_ts.dt.strftime("%Y-%m")
    mk = tr[tr.is_maker == True].groupby("ym").commission.agg(lambda c: c.round(7).mode().iloc[0])  # noqa: E712
    tk = tr[tr.is_maker == False].groupby("ym").commission.agg(lambda c: c.round(7).mode().iloc[0])  # noqa: E712

    def f(series):
        def g(t):
            ym = pd.Timestamp(t, tz="UTC").strftime("%Y-%m")
            return float(series.get(ym, series.iloc[-1]))
        return g
    return f(mk), f(tk)


def boot(x, cl) -> dict:
    x = np.asarray(x, float)
    ok = np.isfinite(x)
    if ok.sum() == 0:
        return {"n": 0}
    est, se, lo, hi = cluster_boot(x[ok], np.ones(ok.sum()), np.asarray(cl)[ok], b=BOOT_B, seed=SEED)
    return {"n": int(ok.sum()), "mean": est, "se": se, "ci95": [lo, hi], "median": float(np.median(x[ok])),
            "p25": float(np.percentile(x[ok], 25)), "p75": float(np.percentile(x[ok], 75)),
            "pos_rate": float((x[ok] > 0).mean())}


def run() -> dict:
    t_start = time.time()
    sha = verify_contract()
    grid = Grid(G.load_grid())
    m, ep, w = load_inputs()
    pend = pending_realized_fn(m)
    maker_rate, taker_rate = modal_rate_fns(m)
    streams = episode_streams(m, ep)
    bb = json.loads(BYBIT_FEE_JSON.read_text())["adopted"]
    by_maker, by_taker = float(bb["maker_rate"]), float(bb["taker_rate"])

    xe = ep[(ep.symbol == "XBTUSD") & ep.end_ts.notna()].copy()
    by_seq = m.set_index("seq")[["pos_before", "cost_before_sat"]]
    E, ADDS, REDS, CFR = [], [], [], []
    for e in xe.itertuples(index=False):
        s = streams[e.episode_id]
        orders = classify_orders(s)
        entry = orders[orders.type == "ENTRY"].iloc[0]
        t_e = int(entry.t_first)
        t_x = int(orders[orders.type == "FULL_CLOSE"].t_last.max())
        ff = by_seq.loc[s.seq.iloc[0]]
        eq0 = float(equity_at([t_e], w, pend, grid, [ff.pos_before], [ff.cost_before_sat])[0])
        act = replay_actual(s)
        pm = path_metrics(act, t_e, t_x, grid, eq0)
        d = int(np.sign(s[s.kind != "FUND"].q.iloc[0]))
        fund = s[s.kind == "FUND"].funding
        n_add = int((orders.type == "ADD").sum()); n_red = int((orders.type == "REDUCE").sum())
        init_qty = float(s[(s.orderid == entry.orderid) & (s.kind == "INC")].q.abs().sum())
        ctx = grid.context([t_e])
        rec = {"episode_id": e.episode_id, "direction": d, "year": pd.Timestamp(t_e, tz="UTC").year,
               "week": int(t_e // (7 * 86_400 * 10**9)), "t_e": t_e, "t_x": t_x, "hold_h": (t_x - t_e) / HOUR_NS,
               "closed_by": e.closed_by, "liquidation": e.closed_by == "LIQUIDATION", "opened_by_reverse": bool(e.opened_by_reverse),
               "equity0": eq0, "init_qty": init_qty, "max_abs_pos": float(e.max_abs_pos), "n_add": n_add, "n_reduce": n_red,
               "gross_xbt": act.realized / SAT, "fee_xbt": act.fees / SAT, "funding_xbt": act.funding / SAT,
               "funding_paid_xbt": float(fund[fund > 0].sum() / SAT), "funding_recv_xbt": float(-fund[fund < 0].sum() / SAT),
               "net_xbt": act.net / SAT, "e0_net_xbt": (e.realized_gross_sat - e.fee_sat - e.funding_sat) / SAT,
               "roe": act.net / SAT / eq0, "entry_vwap": float(entry.vwap), "entry_maker_share": float(entry.maker_share),
               "vol60": float(ctx["vol60"][0]), "ret60_signed": float(d * ctx["ret60"][0]), "trades60": int(ctx["trades60"][0]),
               "start_leverage": init_qty / entry.vwap / eq0, **pm}
        # entry forward returns (7)
        b0 = grid.floor(t_e); m0 = grid.M(b0)
        for h in ENTRY_H_MIN:
            rec[f"entry_r_{h}m"] = float(d * (grid.M(grid.ceil(t_e + h * MIN_NS)) - m0) / m0)
        # Bybit cost reference (14)
        tr = s[s.kind != "FUND"]
        by_fee = (tr.ec.abs() * np.where(tr.is_maker == True, by_maker, by_taker)).sum()  # noqa: E712
        rec["roe_bybit_fee"] = (act.realized - by_fee - act.funding) / SAT / eq0
        # counterfactuals (9)
        cf = {"episode_id": e.episode_id}
        if n_add:
            b1 = cf_book(s, orders, "NO_ADD")
            p1 = path_metrics(b1, t_e, t_x, grid, eq0)
            cf.update({"cf1_net_xbt": b1.net / SAT, "cf1_roe": b1.net / SAT / eq0, "cf1_mae_roe": p1["mae_roe"],
                       "cf1_peak_leverage": p1["peak_leverage"], "cf1_class": outcome_class(b1.net / SAT / eq0)})
            for x in RULE_X:
                b2 = cf_book(s, orders, "RULE", grid, x, maker_rate)
                cf[f"cf2_{x}_roe"] = b2.net / SAT / eq0
                cf[f"cf2_{x}_class"] = outcome_class(b2.net / SAT / eq0)
        if n_red:
            b3 = cf_book(s, orders, "NO_PARTIAL")
            p3 = path_metrics(b3, t_e, t_x, grid, eq0)
            cf.update({"cf3_net_xbt": b3.net / SAT, "cf3_roe": b3.net / SAT / eq0, "cf3_mae_roe": p3["mae_roe"],
                       "cf3_peak_leverage": p3["peak_leverage"]})
        s4, hit, tb = stop_cf(act, t_e, t_x, grid, eq0, taker_rate)
        cf.update({"cf4_roe": s4 / eq0, "cf4_triggered": hit, "cf4_t": tb})
        CFR.append(cf)
        # ADD and REDUCE events (8, 10-I)
        tr_pos = tr.q.cumsum().to_numpy(); tr_t = tr.t.to_numpy()
        prev_add_t, n_prev = None, 0
        for o in orders.itertuples(index=False):
            if o.type not in ("ADD", "REDUCE"):
                continue
            k = int(np.searchsorted(tr_t, o.t_first, "left")) - 1
            pos_b = float(tr_pos[k]) if k >= 0 else 0.0
            bk = slice_book(act, int(o.t_first))
            mid0 = float(grid.M(grid.floor(o.t_first)))
            u = bk.unreal(mid0) if bk.pos != 0 else 0.0
            eq = eq0 + (bk.net + u) / SAT
            avg = bk.pos * MULT / bk.cost if bk.cost != 0 else np.nan
            c = grid.context([o.t_first])
            sub_pm = path_metrics(bk, t_e, int(o.t_first), grid, eq0) if len(bk.ev_t) else {"mae_roe": 0.0}
            rowd = {"episode_id": e.episode_id, "orderid": o.orderid, "type": o.type, "t": int(o.t_first), "year": rec["year"],
                    "week": rec["week"], "direction": d, "pos_before": abs(pos_b), "avg_entry": avg, "mid": mid0,
                    "dist_avg_bp": d * (mid0 - avg) / avg * 1e4 if np.isfinite(avg) else np.nan,
                    "unreal_roe": u / SAT / eq0, "mae_to_date_roe": sub_pm["mae_roe"],
                    "since_entry_h": (o.t_first - t_e) / HOUR_NS, "qty": float(o.qty), "vwap": float(o.vwap),
                    "maker_share": float(o.maker_share), "equity": eq,
                    "vol60": float(c["vol60"][0]), "ret60_signed": float(d * c["ret60"][0]), "trades60": int(c["trades60"][0]),
                    "episode_roe": rec["roe"], "episode_class": outcome_class(rec["roe"]), "liquidation": rec["liquidation"]}
            for h in ADD_H_MIN:
                rowd[f"fwd_{h}m"] = float(d * (grid.M(grid.ceil(o.t_first + h * MIN_NS)) - mid0) / mid0)
            end_mid = float(grid.M(grid.ceil(t_x)))
            rowd["fwd_close"] = float(d * (end_mid - mid0) / mid0)
            if o.type == "ADD":
                rowd.update({"since_prev_add_h": (o.t_first - prev_add_t) / HOUR_NS if prev_add_t else np.nan,
                             "add_over_init": o.qty / init_qty, "add_over_pos": o.qty / abs(pos_b) if pos_b else np.nan,
                             "lev_before": abs(pos_b) / mid0 / eq, "lev_after": (abs(pos_b) + o.qty) / mid0 / eq,
                             "n_prev_adds": n_prev})
                prev_add_t = o.t_first
                n_prev += 1
                ADDS.append(rowd)
            else:
                rowd.update({"frac": o.qty / abs(pos_b) if pos_b else np.nan, "remaining": abs(pos_b) - o.qty,
                             "vwap_vs_avg_bp": d * (o.vwap - avg) / avg * 1e4 if np.isfinite(avg) else np.nan,
                             "since_last_add_h": (o.t_first - prev_add_t) / HOUR_NS if prev_add_t else np.nan})
                REDS.append(rowd)
        E.append(rec)

    epi = pd.DataFrame(E)
    adds = pd.DataFrame(ADDS)
    reds = pd.DataFrame(REDS)
    cfm = pd.DataFrame(CFR)
    epi["class"] = epi.roe.map(outcome_class)
    epi.to_parquet(E2 / "episode_metrics.parquet", index=False)
    adds.to_parquet(E2 / "add_events.parquet", index=False)
    reds.to_parquet(E2 / "reduce_events.parquet", index=False)
    cfm.to_parquet(E2 / "counterfactual_metrics.parquet", index=False)
    summ = summarize(epi, adds, reds, cfm, grid, m, w, pend)
    summ["contract_sha256"] = sha
    summ["runtime"] = {"seconds": round(time.time() - t_start, 1),
                       "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)}
    (E2 / "summary.json").write_text(json.dumps(summ, indent=1, default=_jd))
    return summ


# ------------------------------------------------------------------ summaries and verdicts (11, 12)
def _grp(df, col, cols, cl="week"):
    return {str(k): {c: boot(g[c], g[cl]) for c in cols} for k, g in df.groupby(col)}


def summarize(epi, adds, reds, cfm, grid: Grid, m, w, pend) -> dict:
    S: dict = {}
    ok = epi[epi.missing_share <= PATH_MISSING_MAX]
    S["episodes"] = {"closed_xbtusd": int(len(epi)), "path_ok": int(len(ok)), "path_excluded": int(len(epi) - len(ok)),
                     "liquidation": int(epi.liquidation.sum()),
                     "e0_net_match_max_abs_xbt": float((epi.net_xbt - epi.e0_net_xbt).abs().max()),
                     "by_year": epi.groupby("year").size().to_dict(), "class_counts": epi["class"].value_counts().to_dict()}
    # Q1 entry edge
    mu = {}
    for h in ENTRY_H_MIN:
        r = (np.r_[grid.mid[h:], np.full(h, np.nan)] - grid.mid) / grid.mid
        mu[h] = pd.Series(r).groupby(grid.year).mean().to_dict()
    for h in ENTRY_H_MIN:
        epi[f"entry_adj_{h}m"] = epi[f"entry_r_{h}m"] - epi.direction * epi.year.map(mu[h])
    q1 = {"drift_by_year": {h: {int(k): float(v) for k, v in mu[h].items()} for h in ENTRY_H_MIN}}
    for lab, sub in (("all", epi), ("no_liq", epi[~epi.liquidation])):
        q1[lab] = {f"{k}_{h}m": boot(sub[f"entry_{k}_{h}m"], sub.week) for h in ENTRY_H_MIN for k in ("r", "adj")}
    q1["by_year_adj_4h"] = {int(y): boot(g[f"entry_adj_{PRIMARY_ENTRY_H}m"], g.week) for y, g in epi.groupby("year")}
    q1["by_direction_adj_4h"] = {("LONG" if d > 0 else "SHORT"): boot(g[f"entry_adj_{PRIMARY_ENTRY_H}m"], g.week) for d, g in epi.groupby("direction")}
    vq = np.nanpercentile(epi.vol60, [100 / 3, 200 / 3])
    epi["vol_bucket"] = np.where(epi.vol60.isna(), "NA", np.where(epi.vol60 <= vq[0], "LOW", np.where(epi.vol60 <= vq[1], "MID", "HIGH")))
    q1["by_vol_adj_4h"] = {k: boot(g[f"entry_adj_{PRIMARY_ENTRY_H}m"], g.week) for k, g in epi.groupby("vol_bucket")}
    a4 = q1["all"][f"adj_{PRIMARY_ENTRY_H}m"]
    yrs = [v.get("mean", np.nan) for v in q1["by_year_adj_4h"].values()]
    q1["verdict"] = ("SUPPORTED" if a4["mean"] > 0 and a4["ci95"][0] > 0 and sum(y > 0 for y in yrs) >= 3
                     else "NOT_SUPPORTED" if a4["mean"] <= 0 else "MIXED")
    S["Q1_entry_edge"] = q1

    # Q2 ADD events
    fw = [f"fwd_{h}m" for h in ADD_H_MIN] + ["fwd_close"]
    S["Q2_add_events"] = {"n": int(len(adds)), "episodes_with_add": int((epi.n_add > 0).sum()),
                          "forward": {c: boot(adds[c], adds.week) for c in fw},
                          "state_median": adds[["unreal_roe", "dist_avg_bp", "mae_to_date_roe", "since_entry_h", "since_prev_add_h",
                                                "add_over_init", "add_over_pos", "lev_before", "lev_after", "vol60", "ret60_signed",
                                                "trades60", "maker_share"]].median().to_dict(),
                          "share_underwater_at_add_mid": float((adds.unreal_roe < 0).mean()),
                          "share_price_worse_than_avg": float((adds.dist_avg_bp < 0).mean())}

    # CF comparisons
    j = epi.merge(cfm, on="episode_id")
    addep = j[j.n_add > 0].copy()
    addep["d_cf1"] = addep.roe - addep.cf1_roe
    for x in RULE_X:
        addep[f"d_cf2_{x}"] = addep.roe - addep[f"cf2_{x}_roe"]
    cf1 = {"n": int(len(addep)), "d_actual_minus_noadd": boot(addep.d_cf1, addep.week),
           "d_no_liq": boot(addep[~addep.liquidation].d_cf1, addep[~addep.liquidation].week),
           "by_year": {int(y): boot(g.d_cf1, g.week) for y, g in addep.groupby("year")},
           "sum_xbt_actual": float(addep.net_xbt.sum()), "sum_xbt_noadd": float(addep.cf1_net_xbt.sum()),
           "roe_actual": boot(addep.roe, addep.week), "roe_noadd": boot(addep.cf1_roe, addep.week),
           "mae_actual": boot(addep.mae_roe, addep.week), "mae_noadd": boot(addep.cf1_mae_roe, addep.week),
           "peak_lev_actual_median": float(addep.peak_leverage.median()), "peak_lev_noadd_median": float(addep.cf1_peak_leverage.median()),
           "p5_roe_actual": float(addep.roe.quantile(.05)), "p5_roe_noadd": float(addep.cf1_roe.quantile(.05)),
           "catastrophic_actual": int((addep.roe <= -0.05).sum()), "catastrophic_noadd": int((addep.cf1_roe <= -0.05).sum()),
           "win_rate_actual": float((addep.roe > 0).mean()), "win_rate_noadd": float((addep.cf1_roe > 0).mean())}
    cf1["rules"] = {str(x): {"d_actual_minus_rule": boot(addep[f"d_cf2_{x}"], addep.week),
                             "roe_rule": boot(addep[f"cf2_{x}_roe"], addep.week),
                             "catastrophic_rule": int((addep[f"cf2_{x}_roe"] <= -0.05).sum())} for x in RULE_X}
    S["CF1_CF2_add"] = cf1

    redep = j[j.n_reduce > 0].copy()
    redep["d_cf3"] = redep.roe - redep.cf3_roe
    redep["mae_gain"] = redep.mae_roe - redep.cf3_mae_roe
    S["CF3_partial"] = {"n": int(len(redep)), "d_actual_minus_nopartial": boot(redep.d_cf3, redep.week),
                        "d_no_liq": boot(redep[~redep.liquidation].d_cf3, redep[~redep.liquidation].week),
                        "mae_improvement": boot(redep.mae_gain, redep.week),
                        "by_year": {int(y): boot(g.d_cf3, g.week) for y, g in redep.groupby("year")},
                        "mae_by_year": {int(y): boot(g.mae_gain, g.week) for y, g in redep.groupby("year")},
                        "sum_xbt_actual": float(redep.net_xbt.sum()), "sum_xbt_nopartial": float(redep.cf3_net_xbt.sum()),
                        "peak_lev_actual_median": float(redep.peak_leverage.median()),
                        "peak_lev_nopartial_median": float(redep.cf3_peak_leverage.median()),
                        "catastrophic_actual": int((redep.roe <= -0.05).sum()), "catastrophic_nopartial": int((redep.cf3_roe <= -0.05).sum())}

    trig = j[j.cf4_triggered == True].copy()  # noqa: E712
    trig["d_cf4"] = trig.roe - trig.cf4_roe
    S["CF4_stop"] = {"episodes": int(len(j)), "triggered": int(len(trig)), "d_actual_minus_stop": boot(trig.d_cf4, trig.week),
                     "d_no_liq": boot(trig[~trig.liquidation].d_cf4, trig[~trig.liquidation].week),
                     "by_year": {int(y): boot(g.d_cf4, g.week) for y, g in trig.groupby("year")},
                     "triggered_final_roe": boot(trig.roe, trig.week), "triggered_recovered_rate": float((trig.roe > 0).mean()),
                     "sum_xbt_actual": float(trig.net_xbt.sum()), "sum_xbt_stop": float((trig.cf4_roe * trig.equity0).sum())}

    # E ADD count, K combos
    epi["add_group"] = epi.n_add.clip(upper=4).map(lambda n: "4+" if n >= 4 else str(n))
    epi["cap_eff"] = epi.net_xbt / epi.exposure_xbt_days.replace(0, np.nan)
    cols = ["roe", "mae_roe", "mfe_roe", "hold_h", "peak_leverage", "cap_eff"]
    S["E_add_count"] = {k: {"n": int(len(g)), "win_rate": float((g.roe > 0).mean()),
                            "catastrophic": int((g.roe <= -0.05).sum()), **{c: boot(g[c], g.week) for c in cols}}
                        for k, g in epi.groupby("add_group")}
    S["E_add_count_by_mae"] = {f"{k}|{mb}": {"n": int(len(g)), "roe_mean": float(g.roe.mean()), "win": float((g.roe > 0).mean())}
                               for (k, mb), g in epi.assign(mae_b=pd.cut(epi.mae_roe, [-np.inf, -0.05, -0.02, -0.005, np.inf],
                                                                           labels=["<=-5%", "-5~-2%", "-2~-0.5%", ">-0.5%"]))
                               .groupby(["add_group", "mae_b"], observed=True)}
    epi["combo"] = np.where(epi.n_add > 0, "ADD", "NOADD") + "|" + np.where(epi.n_reduce > 0, "PARTIAL", "NOPARTIAL")
    S["K_combo"] = {k: {"n": int(len(g)), "win_rate": float((g.roe > 0).mean()), "catastrophic": int((g.roe <= -0.05).sum()),
                        "roe_median": float(g.roe.median()), **{c: boot(g[c], g.week) for c in cols}}
                    for k, g in epi.groupby("combo")}

    # F successful vs failed ADD episodes: state at first and last ADD
    if len(adds):
        a = adds.copy()
        a["rank"] = a.groupby("episode_id").cumcount()
        last_rank = a.groupby("episode_id")["rank"].transform("max")
        st = ["unreal_roe", "dist_avg_bp", "mae_to_date_roe", "vol60", "ret60_signed", "trades60", "since_entry_h",
              "lev_before", "lev_after", "add_over_init", "add_over_pos", "n_prev_adds", "maker_share"]
        S["F_add_outcome"] = {"first_add": a[a["rank"] == 0].groupby("episode_class")[st].median().to_dict("index"),
                              "last_add": a[a["rank"] == last_rank].groupby("episode_class")[st].median().to_dict("index"),
                              "episodes": a.drop_duplicates("episode_id").episode_class.value_counts().to_dict(),
                              "adds_per_episode_median": a.groupby("episode_class").apply(
                                  lambda g: g.groupby("episode_id").size().median(), include_groups=False).to_dict()}

    # G MAE/MFE, H underwater
    S["G_mae_mfe"] = {grp: {c: boot(g[c], g.week) for c in ["mae_roe", "mfe_roe", "t_mae_h", "t_mfe_h", "recovery_h", "roe"]}
                      for grp, g in (("all", ok), ("winners", ok[ok.roe > 0]), ("losers", ok[ok.roe <= 0]),
                                     ("add", ok[ok.n_add > 0]), ("no_add", ok[ok.n_add == 0]))}
    S["G_recovered_from_mae_le_2pct"] = {"n": int((ok.mae_roe <= -0.02).sum()),
                                         "finished_positive": float((ok[ok.mae_roe <= -0.02].roe > 0).mean())}
    uw = ok.underwater_minutes.sum() / ok.valid_minutes.sum()
    ok = ok.assign(uw_ratio=ok.underwater_minutes / ok.valid_minutes.replace(0, np.nan))
    S["H_underwater"] = {"pooled_time_weighted": float(uw),
                         "by_year_pooled": {int(y): float(g.underwater_minutes.sum() / g.valid_minutes.sum()) for y, g in ok.groupby("year")},
                         "episode_ratio": boot(ok.uw_ratio, ok.week),
                         "episode_ratio_by_year": {int(y): boot(g.uw_ratio, g.week) for y, g in ok.groupby("year")}}

    # I REDUCE events
    if len(reds):
        r = reds.copy()
        r["in_profit"] = r.vwap_vs_avg_bp > 0
        S["I_reduce"] = {"n": int(len(r)), "frac": boot(r.frac, r.week), "in_profit_share": float(r.in_profit.mean()),
                         "vwap_vs_avg_bp": boot(r.vwap_vs_avg_bp, r.week),
                         "since_entry_h_median": float(r.since_entry_h.median()),
                         "forward": {c: boot(r[c], r.week) for c in fw},
                         "forward_in_profit": {c: boot(r[r.in_profit][c], r[r.in_profit].week) for c in fw},
                         "forward_in_loss": {c: boot(r[~r.in_profit][c], r[~r.in_profit].week) for c in fw}}

    # L leverage
    lq = np.nanpercentile(epi.peak_leverage, [100 / 3, 200 / 3])
    epi["lev_bucket"] = np.where(epi.peak_leverage.isna(), "NA", np.where(epi.peak_leverage <= lq[0], "LOW",
                                 np.where(epi.peak_leverage <= lq[1], "MID", "HIGH")))
    S["L_leverage"] = {"terciles": [float(x) for x in lq],
                       "by_bucket": {k: {"n": int(len(g)), "catastrophic_rate": float((g.roe <= -0.05).mean()),
                                         "liquidations": int(g.liquidation.sum()), "roe": boot(g.roe, g.week),
                                         "mae": boot(g.mae_roe, g.week)} for k, g in epi.groupby("lev_bucket")},
                       "by_year": {int(y): {"start_median": float(g.start_leverage.median()), "peak_median": float(g.peak_leverage.median()),
                                            "peak_p90": float(g.peak_leverage.quantile(.9)), "peak_max": float(g.peak_leverage.max())}
                                   for y, g in epi.groupby("year")},
                       "liquidation_episode_peak_leverage": epi[epi.liquidation].peak_leverage.describe().to_dict(),
                       "add_leverage_increase_median": float((adds.lev_after - adds.lev_before).median()) if len(adds) else None}
    cr = S["L_leverage"]["by_bucket"]
    rates = [cr.get(k, {}).get("catastrophic_rate", np.nan) for k in ("LOW", "MID", "HIGH")]
    liq_top = cr.get("HIGH", {}).get("liquidations", 0) / max(1, int(epi.liquidation.sum()))
    lev_v = ("SUPPORTED" if rates[0] <= rates[1] <= rates[2] and rates[2] >= 2 * rates[0] and liq_top >= 0.75
             else "NOT_SUPPORTED" if rates[2] <= rates[0] else "MIXED")

    # M equity-relative sizing
    ent = pd.concat([epi[["t_e", "init_qty", "entry_vwap", "equity0", "year", "week"]].rename(
        columns={"t_e": "t", "init_qty": "qty", "entry_vwap": "px", "equity0": "equity"}).assign(kind="ENTRY"),
        adds[["t", "qty", "vwap", "equity", "year", "week"]].rename(columns={"vwap": "px"}).assign(kind="ADD")] if len(adds) else [])
    ent["notional_over_equity"] = ent.qty / ent.px / ent.equity
    eqq = np.nanpercentile(np.log(ent.equity.clip(lower=1e-9)), [100 / 3, 200 / 3])
    ent["eq_bucket"] = np.where(np.log(ent.equity.clip(lower=1e-9)) <= eqq[0], "LOW_EQ",
                                np.where(np.log(ent.equity.clip(lower=1e-9)) <= eqq[1], "MID_EQ", "HIGH_EQ"))
    epi["peak_exposure_over_eq"] = epi.peak_leverage
    S["M_sizing"] = {"equity_terciles_xbt": [float(np.exp(x)) for x in eqq],
                     "order_over_equity_by_eq_bucket": {k: boot(g.notional_over_equity, g.week) for k, g in ent.groupby("eq_bucket")},
                     "order_over_equity_by_year": {int(k): boot(g.notional_over_equity, g.week) for k, g in ent.groupby("year")},
                     "peak_exposure_over_equity_by_year": {int(k): boot(g.peak_leverage, g.week) for k, g in epi.groupby("year")}}

    # N loss-after
    thr = epi.roe.quantile(0.05)
    big = epi[epi.roe <= thr]
    ent = ent.sort_values("t").reset_index(drop=True)
    ent["month"] = pd.to_datetime(ent.t, utc=True).dt.strftime("%Y-%m")
    et = ent.t.to_numpy()
    in24 = np.zeros(len(ent), bool)
    for tx in big.t_x:
        in24[np.searchsorted(et, tx, "right"):np.searchsorted(et, tx + 24 * HOUR_NS, "right")] = True
    base = ent[~in24].groupby("month").notional_over_equity.median()
    nres = {"loss_threshold_roe": float(thr), "loss_episodes": int(len(big))}
    for hh in (1, 6, 24):
        sel = np.zeros(len(ent), bool)
        for tx in big.t_x:
            sel[np.searchsorted(et, tx, "right"):np.searchsorted(et, tx + hh * HOUR_NS, "right")] = True
        post = ent[sel]
        ratio = post.notional_over_equity / post.month.map(base)
        nres[f"{hh}h"] = {"orders": int(len(post)), "adds": int((post.kind == "ADD").sum()),
                          "entries": int((post.kind == "ENTRY").sum()),
                          "ratio_median": float(ratio.median()) if len(ratio) else None,
                          "ratio_mean": float(ratio.mean()) if len(ratio) else None,
                          "orders_per_loss_event": len(post) / max(1, len(big))}
    base_rate = len(ent[~in24]) / max(1, (ent.t.max() - ent.t.min()) / HOUR_NS)
    nres["baseline_orders_per_hour"] = float(base_rate)
    S["N_loss_after"] = nres
    r6, r24 = nres["6h"]["ratio_median"], nres["24h"]["ratio_median"]
    loss_v = ("SUPPORTED" if r6 is not None and r6 <= 1.10 and r24 <= 1.10
              else "NOT_SUPPORTED" if (r6 or 0) >= 1.5 or (r24 or 0) >= 1.5 else "MIXED")

    # R funding
    S["R_funding"] = {"total_paid_xbt": float(epi.funding_paid_xbt.sum()), "total_recv_xbt": float(epi.funding_recv_xbt.sum()),
                      "net_funding_xbt": float(epi.funding_xbt.sum()), "gross_xbt": float(epi.gross_xbt.sum()),
                      "net_xbt": float(epi.net_xbt.sum()), "fee_xbt": float(epi.fee_xbt.sum()),
                      "by_year": {int(y): {"gross": float(g.gross_xbt.sum()), "fee": float(g.fee_xbt.sum()),
                                           "funding": float(g.funding_xbt.sum()), "net": float(g.net_xbt.sum()),
                                           "funding_over_net": float(-g.funding_xbt.sum() / g.net_xbt.sum()) if g.net_xbt.sum() else None}
                                  for y, g in epi.groupby("year")},
                      "long_hold_24h_plus": {"n": int((epi.hold_h >= 24).sum()),
                                             "funding_xbt": float(epi[epi.hold_h >= 24].funding_xbt.sum()),
                                             "net_xbt": float(epi[epi.hold_h >= 24].net_xbt.sum())}}
    S["S_liquidation"] = epi[epi.liquidation][["episode_id", "year", "direction", "equity0", "roe", "mae_roe", "peak_leverage",
                                               "start_leverage", "hold_h", "n_add", "net_xbt"]].to_dict("records")
    S["bybit_cost_reference"] = {"roe_actual": boot(epi.roe, epi.week), "roe_bybit_fee": boot(epi.roe_bybit_fee, epi.week)}

    # verdicts (12)
    d1 = cf1["d_actual_minus_noadd"]
    y1 = [v.get("mean", np.nan) for v in cf1["by_year"].values()]
    rules_ok = all(cf1["rules"][str(x)]["d_actual_minus_rule"]["mean"] >= 0 for x in RULE_X)
    cat_ok = cf1["catastrophic_actual"] <= 1.5 * cf1["catastrophic_noadd"] + 1
    add_v = ("SUPPORTED" if d1["mean"] > 0 and d1["ci95"][0] > 0 and sum(v > 0 for v in y1) >= 3 and rules_ok and cat_ok
             else "NOT_SUPPORTED" if d1["mean"] <= 0 else "MIXED")
    d3 = S["CF3_partial"]["d_actual_minus_nopartial"]; mg = S["CF3_partial"]["mae_improvement"]
    par_v = ("SUPPORTED" if d3["ci95"][0] > 0 or (mg["ci95"][0] > 0 and d3["ci95"][1] >= 0)
             else "NOT_SUPPORTED" if d3["ci95"][1] < 0 and mg["ci95"][1] <= 0 else "MIXED")
    d4 = S["CF4_stop"]["d_actual_minus_stop"]
    y4 = [v.get("mean", np.nan) for v in S["CF4_stop"]["by_year"].values()]
    hold_v = ("SUPPORTED" if d4["mean"] > 0 and d4["ci95"][0] > 0 and sum(v > 0 for v in y4) >= 3
              else "NOT_SUPPORTED" if d4["mean"] <= 0 else "MIXED")
    feats = {"ADD": add_v, "PARTIAL_CLOSE": par_v, "LONG_HOLD": hold_v, "LOW_LEVERAGE": lev_v, "LOSS_AFTER_SIZE_CONTROL": loss_v}
    core = [add_v, par_v, hold_v]
    overall = ("POSITION_MANAGEMENT_EDGE_SUPPORTED" if sum(v == "SUPPORTED" for v in core) >= 2
               else "NOT_SUPPORTED" if all(v == "NOT_SUPPORTED" for v in core) else "MIXED")
    S["verdict"] = {"features": feats, "overall": overall, "entry_edge": q1["verdict"],
                    "detail": {"add": {"d_mean": d1["mean"], "ci": d1["ci95"], "years_pos": int(sum(v > 0 for v in y1)),
                                       "rules_ok": rules_ok, "catastrophic_ok": cat_ok},
                               "partial": {"d": d3, "mae_gain": mg}, "long_hold": {"d": d4, "years_pos": int(sum(v > 0 for v in y4))},
                               "leverage": {"catastrophic_rates_low_mid_high": rates, "liq_share_top": liq_top},
                               "loss_after": {"r6": r6, "r24": r24}}}
    epi.to_parquet(E2 / "episode_metrics.parquet", index=False)
    return S


def _jd(o):
    if isinstance(o, (np.integer,)): return int(o)
    if isinstance(o, (np.floating,)): return None if np.isnan(o) else float(o)
    if isinstance(o, np.bool_): return bool(o)
    if isinstance(o, pd.Timestamp): return o.isoformat()
    raise TypeError(type(o))


if __name__ == "__main__":
    out = run()
    print(json.dumps(out["verdict"], indent=1, default=_jd))
