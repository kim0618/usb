"""C1 V2 EXACT-A census runner. Every F1/F3 candidate -> its own paper-cash account -> production A.

Run with PYTHONPATH=<scratch rtroot>/backend (production strategy/risk/execution files + shims, see
PARITY.md). Read-only: no DB, no network, no orders. Output: one JSON line per candidate.
    python c1_runner.py <jobs.json> <out.jsonl> <workers>
"""
import gzip, json, os, sys, time, traceback
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

DRIVE = Path("/mnt/g/내 드라이브/1_US-B")
GROUPED = Path.home() / "usb/data/runtime/strategy_c/raw/grouped"


def _daily_bars(symbol, sessions, rows_by_day, tape_end_at, DailyBar):
    bars = []
    for day in sessions:
        r = rows_by_day.get(day)
        if r is None:
            continue
        o, h, l, c, v = r
        bars.append(DailyBar(symbol=symbol, trading_date=day, open=o, high=h, low=l, close=c,
                             volume=int(round(v)), observed_at=tape_end_at(day), available_at=tape_end_at(day)))
    return bars


def run_symbol(job):
    """job = {symbol, candidates:[{family,D,E,E1}], daily:{iso:[o,h,l,c,v]}}"""
    from app.backtest.baseline import analytics
    from app.backtest.baseline.current_paper import CurrentPaperConfig
    from app.backtest.portfolio.candidates import FrozenTop8CandidateSource
    from app.backtest.portfolio.config import PortfolioConfig
    from app.backtest.portfolio.replay import MultiSymbolPortfolioReplay
    from app.backtest.replay.daily_volume import PREMARKET_VOLUME_BASIS_V2
    from app.backtest.research.sparse_minute import load_sparse_minute
    from app.backtest.research.contract import tape_end_at
    from app.backtest.authority.contract import research_modes
    from app.market.calendar import MarketCalendar
    from app.market.domain import DailyBar
    from app.strategy.engine import StrategyV0Engine

    cal = MarketCalendar(); paper = CurrentPaperConfig.read(); sym = job["symbol"]
    out = []
    need = set()
    for c in job["candidates"]:
        need |= {date.fromisoformat(c["D"]), date.fromisoformat(c["E"])}
        if c.get("E1"): need.add(date.fromisoformat(c["E1"]))
    try:
        ds = load_sparse_minute(DRIVE, sym, need, calendar=cal, window=(min(need), max(need)))
    except Exception as e:
        return [dict(c, symbol=sym, status="MINUTE_LOAD_FAIL", error=f"{type(e).__name__}:{str(e)[:160]}") for c in job["candidates"]]
    rows_by_day = {date.fromisoformat(k): v for k, v in job["daily"].items()}
    daily_days = sorted(rows_by_day)
    for c in job["candidates"]:
        rec = dict(c, symbol=sym)
        D, E = date.fromisoformat(c["D"]), date.fromisoformat(c["E"])
        try:
            have = set(ds.rows) if hasattr(ds, "rows") else None
            bars = _daily_bars(sym, [d for d in daily_days if E - timedelta(days=60) <= d < E], rows_by_day, tape_end_at, DailyBar)
            cfg = PortfolioConfig(starting_cash=paper.starting_cash, universe=(sym,), trading_dates=(E,),
                                  settlement_sessions=1, benchmark_symbol="SPY",
                                  premarket_volume_basis=PREMARKET_VOLUME_BASIS_V2)
            src = FrozenTop8CandidateSource([{"session_date": E.isoformat(), "rank": 1, "symbol": sym}],
                                            calendar=cal, checksum=f"C1V2:{c['family']}:{sym}:{D}")
            holder = {}
            replay = MultiSymbolPortfolioReplay(
                {sym: ds}, config=cfg, modes=research_modes((sym,)), candidates=src, calendar=cal,
                engine=StrategyV0Engine(paper.strategy), risk_config=paper.risk,
                execution_config=paper.execution, research_universe_checksum=f"C1V2:{sym}:{D}",
                premarket_daily_bars={sym: bars}, sparse_coverage=True)
            res = replay.run(observer=lambda tick, account: holder.__setitem__("b", account.broker))
            ent = res.entries[0] if res.entries else None
            if ent is not None:
                rec.update(premarket_passed=ent.premarket_passed, gate_reason=ent.premarket_gate_reason,
                           gap=None if ent.gap_pct is None else float(ent.gap_pct),
                           vol_ratio=None if ent.v1_volume_ratio is None else float(ent.v1_volume_ratio),
                           invalid=ent.premarket_invalid_field,
                           or_formed=ent.opening_range_high is not None, signal=bool(ent.signal_present),
                           signal_at=None if ent.signal_at is None else ent.signal_at.isoformat(),
                           exec_attempted=bool(ent.execution_attempted), exec_result=ent.execution_result,
                           risk_rejection=ent.risk_rejection, final_reason=ent.final_reason)
            rec["excluded"] = dict(res.excluded_symbols)
            rec.update(pit=res.pit_violations, invariants=len(res.invariant_violations))
            trades = analytics.trade_records(res)
            broker = holder.get("b")
            fills = broker.get_fills() if broker is not None else ()
            tlist = []
            for t in trades:
                v = t.values
                fs = [f for f in fills if f.symbol == sym]
                gross = sum(((f.raw_market_price * f.quantity) * (1 if f.side.value == "SELL" else -1) for f in fs), Decimal(0))
                comm = sum((f.commission + f.fx_cost for f in fs), Decimal(0))
                cost = sum((f.total_cost for f in fs), Decimal(0))
                turnover = sum((f.raw_market_price * f.quantity for f in fs), Decimal(0))
                tlist.append({k: (str(x) if x is not None else None) for k, x in {
                    "entry_time": v["entry_time"], "exit_time": v["exit_time"], "exit_date": v["exit_date"],
                    "exit_reason": v["exit_reason"], "sessions_held": v["sessions_held"], "day2": v["day2_reached"],
                    "quantity": v["quantity"], "entry_price": v["entry_price"], "exit_price": v["exit_price"],
                    "initial_risk": v["initial_risk"], "net_pnl": v["net_pnl"], "realized_r": v["realized_r"],
                    "gross_pnl_engine": v["gross_pnl"], "execution_cost_engine": v["execution_cost"],
                    "pyramid_adds": v["pyramid_adds"], "raw_gross": gross, "commission": comm,
                    "total_cost": cost, "turnover": turnover, "fills": len(fs)}.items()})
            rec["trades"] = tlist
            rec["fill_rejects"] = [str(o.rejection_reason) for o in (broker._orders.values() if broker else []) if getattr(o, "rejection_reason", None)]
            rec["status"] = "OK"
        except Exception as e:
            rec["status"] = "REPLAY_ERROR"; rec["error"] = f"{type(e).__name__}:{str(e)[:200]}"
            rec["tb"] = traceback.format_exc()[-600:]
        out.append(rec)
    return out


def main():
    jobs = json.load(open(sys.argv[1])); outp = sys.argv[2]; workers = int(sys.argv[3])
    t0 = time.time(); n = 0
    with open(outp, "w") as fh, ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(run_symbol, j) for j in jobs]
        for k, f in enumerate(as_completed(futs)):
            for rec in f.result():
                fh.write(json.dumps(rec, default=str) + "\n"); n += 1
            fh.flush()
            if k % 50 == 0:
                print(f"symbols {k}/{len(jobs)} candidates {n} {time.time()-t0:.0f}s", flush=True)
    print("done", n, f"{time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
