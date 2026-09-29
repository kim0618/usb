"""BitMEX contract model, derived from the ledger itself and checked against it.

Every BitMEX execution carries `execcost` (XBt satoshi, signed). For each symbol exactly one of
these identities holds, which fixes both the contract type and the multiplier:

    inverse : execcost = -signed_qty * M / price      (M = 1e8 XBt per 1 USD contract; XBTUSD, XBT futures)
    quanto  : execcost =  signed_qty * M * price      (price in USD/USDT, settled in XBt; ETHUSD M=100, ...)
    linear  : execcost =  signed_qty * M * price      (price quoted in XBT, M=1e8; XRPZ18, ADAU18, ...)

quanto and linear share the formula; they differ in quote currency (USD/USDT vs XBT), which is
what makes the PnL of a quanto contract depend on the XBT/USD rate at no point in this ledger:
the XBt value per price point is fixed by M. The Bybit USDT-linear formula is never used here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

SAT_PER_XBT = 100_000_000
IDENTITY_TOL = 1e-3  # p99 relative deviation allowed (inverse rounds cost per contract)


@dataclass(frozen=True)
class ContractSpec:
    symbol: str
    kind: str            # inverse | quanto | linear
    multiplier: float    # XBt; negative for inverse (BitMEX convention)
    quote_currency: str  # `currency` column: USD, USDT or XBT
    settle_currency: str
    identity_p99_rel_dev: float
    fills_checked: int
    perpetual: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _is_perpetual(symbol: str) -> bool:
    # BitMEX dated futures end in a month code + 2-digit year (H/M/U/Z + YY, e.g. XBTU21, ETHUSDM21);
    # XBT7D_* are weekly binaries. Everything else in this ledger is a perpetual swap.
    if symbol.startswith("XBT7D"):
        return False
    return re.search(r"[FGHJKMNQUVXZ]\d{2}$", symbol) is None


def _round_multiplier(m: float) -> float:
    # multipliers are powers of ten or 2x powers of ten in this ledger; snap to 4 significant digits
    if m == 0:
        return 0.0
    mag = 10 ** (np.floor(np.log10(abs(m))) - 3)
    return float(np.round(m / mag) * mag)


def infer_specs(trades: pd.DataFrame) -> dict[str, ContractSpec]:
    """trades: Trade executions with signed_qty, lastpx, execcost, currency, settlcurrency."""
    specs = {}
    for sym, g in trades.groupby("symbol"):
        g = g[g["lastpx"] > 0]
        q = g["signed_qty"].to_numpy(float)
        px = g["lastpx"].to_numpy(float)
        ec = g["execcost"].to_numpy(float)
        inv = -ec * px / q
        lin = ec / (q * px)
        cands = []
        for kind, r in (("inverse", inv), ("linear_or_quanto", lin)):
            m = float(np.median(r))
            dev = float(np.quantile(np.abs(r / m - 1), 0.99)) if m else np.inf
            cands.append((dev, kind, m))
        dev, kind, m = min(cands)
        if dev > IDENTITY_TOL:
            raise ValueError(f"{sym}: no contract identity holds (best {kind} p99 dev {dev:.2e})")
        quote = g["currency"].iloc[0]
        if kind == "inverse":
            mult = -_round_multiplier(m)
        else:
            kind = "linear" if quote == "XBT" else "quanto"
            mult = _round_multiplier(m)
        specs[sym] = ContractSpec(sym, kind, mult, quote, g["settlcurrency"].iloc[0], dev, len(g),
                                  _is_perpetual(sym))
    return specs


def exec_cost_sat(kind: str, multiplier: float, signed_qty: float, price: float) -> float:
    """Theoretical signed execcost in XBt (before BitMEX's per-contract rounding)."""
    if kind == "inverse":
        return signed_qty * multiplier / price
    return signed_qty * multiplier * price


def round_trip_pnl_sat(kind: str, multiplier: float, signed_qty: float, entry_px: float, exit_px: float) -> float:
    """Gross XBt PnL of opening `signed_qty` at entry_px and closing it at exit_px: -(cost_in + cost_out)."""
    return -(exec_cost_sat(kind, multiplier, signed_qty, entry_px)
             + exec_cost_sat(kind, multiplier, -signed_qty, exit_px))


def avg_price_from_cost(kind: str, multiplier: float, pos: float, cost: float) -> float:
    if pos == 0 or cost == 0:
        return float("nan")
    if kind == "inverse":
        return pos * multiplier / cost
    return cost / (pos * multiplier)
