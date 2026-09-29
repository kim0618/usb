"""Run the audit: match P1 high-confidence instants to real Deribit ATM straddles.

    PYTHONPATH=backend python -m app.crypto.research.btc_vol_a.runner

VOL-P0 assumed implied volatility equalled trailing realised volatility and still found a long
straddle negative. This reads what the option actually cost at those instants.

Check B is the one that decides: the straddle is priced at its real time to expiry and real ask,
and compared with the endpoint move over that same window. Comparing a 20-hour option's premium
against a 12-hour horizon's move would be unfair to the option, so the horizons are matched.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from app.crypto.research import dataset
from app.crypto.research.btc_p2 import gate as G

from . import chain as CH
from . import contract as C

OUT_DIR = Path("data/research/crypto/btc_vol_a")

#: Seconds to microseconds, for the staleness tolerance.
MICROS = 1_000_000
#: Milliseconds to microseconds, for a decision instant. Kept separate from MICROS on purpose:
#: using the seconds factor here silently multiplies every instant by a thousand, and the filter
#: then matches nothing at all rather than matching the wrong rows.
MS_TO_US = 1_000
MINUTE_MS = 60_000


def free_dates() -> list[str]:
    """First day of each month inside the P1 validation window."""
    out, year, month = [], 2022, 1
    while (year, month) <= (2026, 9):
        out.append(f"{year:04d}-{month:02d}-01")
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def events_for(horizon: int, threshold_bp: int, level: float) -> list[dict[str, Any]]:
    """P1 decision instants at or above `level`, restricted to free dates. Read-only."""
    frame = G.load(horizon, threshold_bp)
    wanted = {d for d in free_dates()}
    rows = []
    for i, ts in enumerate(frame.ts_ms):
        if frame.large_move[i] < level:
            continue
        day = str(np.datetime64(int(ts), "ms"))[:10]
        if day in wanted:
            rows.append({"ts_ms": int(ts), "day": day, "utc": str(np.datetime64(int(ts), "ms")),
                         "p_large_move": float(frame.large_move[i]),
                         "p_up": float(frame.p_up[i]), "p_down": float(frame.p_down[i]),
                         "horizon_minutes": horizon, "threshold_bp": threshold_bp})
    return rows


def _pick_expiry(quotes: dict[str, CH.Quote], instant_us: int, horizon_minutes: int) -> int | None:
    """Nearest expiry that clears the horizon plus the frozen buffer."""
    minimum = instant_us + (horizon_minutes + C.EXPIRY_BUFFER_MINUTES) * 60 * MICROS
    candidates = {q.expiration_us for q in quotes.values() if q.expiration_us >= minimum}
    return min(candidates) if candidates else None


def _pick_atm(quotes: dict[str, CH.Quote], expiry_us: int) -> tuple[CH.Quote, CH.Quote] | None:
    """Call and put at the strike closest to the underlying, both sides quoted."""
    calls: dict[float, CH.Quote] = {}
    puts: dict[float, CH.Quote] = {}
    for quote in quotes.values():
        if quote.expiration_us != expiry_us or not quote.usable:
            continue
        (calls if quote.option_type == "call" else puts)[quote.strike] = quote
    shared = set(calls) & set(puts)
    if not shared:
        return None
    underlying = np.median([q.underlying for q in quotes.values() if q.underlying > 0])
    strike = min(shared, key=lambda s: abs(s - underlying))
    return calls[strike], puts[strike]


def _endpoint_move(grid: dict[str, np.ndarray], ts_ms: int, minutes: int) -> dict[str, float] | None:
    """Absolute endpoint move and maximum excursion over exactly `minutes` from `ts_ms`."""
    index = int(np.searchsorted(grid["ts"], ts_ms))
    if index >= len(grid["ts"]) or int(grid["ts"][index]) != ts_ms:
        return None
    end = index + minutes
    if end >= len(grid["ts"]):
        return None
    reference = float(grid["close"][index])
    window = slice(index + 1, end + 1)
    return {
        "endpoint_abs": abs(float(grid["close"][end]) / reference - 1.0),
        "max_excursion": max(float(grid["high"][window].max()) / reference - 1.0,
                             1.0 - float(grid["low"][window].min()) / reference),
        "reference_close": reference,
    }


def measure(event: dict[str, Any], quotes: dict[str, CH.Quote],
            grid: dict[str, np.ndarray]) -> dict[str, Any]:
    """One event: the real straddle, and the move over the option's own life."""
    out = dict(event)
    instant_us = event["ts_ms"] * 1000
    if not quotes:
        return {**out, "status": C.NOT_MEASURABLE, "reason": "no BTC quote inside the window"}

    expiry_us = _pick_expiry(quotes, instant_us, event["horizon_minutes"])
    if expiry_us is None:
        return {**out, "status": C.NOT_MEASURABLE, "reason": "no expiry clears horizon + buffer"}

    pair = _pick_atm(quotes, expiry_us)
    if pair is None:
        return {**out, "status": C.NOT_MEASURABLE,
                "reason": "no strike with both a usable call and put"}
    call, put = pair

    tte_minutes = (expiry_us - instant_us) / (60 * MICROS)
    tte_years = tte_minutes / (C.HOURS_PER_YEAR * 60)
    straddle_ask = call.ask + put.ask
    straddle_mid = (call.bid + call.ask) / 2 + (put.bid + put.ask) / 2
    straddle_mark = call.mark + put.mark

    # Tardis reports implied volatility in percent; the contract records that this is checked on
    # the first run rather than assumed silently.
    mark_iv = (call.mark_iv + put.mark_iv) / 2 / 100.0
    ask_iv = (call.ask_iv + put.ask_iv) / 2 / 100.0

    realised = _endpoint_move(grid, event["ts_ms"], int(round(tte_minutes)))
    if realised is None:
        return {**out, "status": C.NOT_MEASURABLE,
                "reason": "research grid does not cover the option's expiry"}

    staleness = (instant_us - max(call.timestamp_us, put.timestamp_us)) / MICROS
    return {
        **out,
        "status": "OK",
        "expiry_utc": str(np.datetime64(expiry_us // 1000, "ms")),
        "time_to_expiry_minutes": round(tte_minutes, 1),
        "time_to_expiry_hours": round(tte_minutes / 60, 2),
        "strike": call.strike,
        "underlying_price": call.underlying,
        "quote_staleness_seconds": round(staleness, 1),
        "call": {"bid": call.bid, "ask": call.ask, "mark": call.mark,
                 "mark_iv_pct": call.mark_iv, "ask_iv_pct": call.ask_iv},
        "put": {"bid": put.bid, "ask": put.ask, "mark": put.mark,
                "mark_iv_pct": put.mark_iv, "ask_iv_pct": put.ask_iv},
        "straddle_ask": straddle_ask,
        "straddle_mid": straddle_mid,
        "straddle_mark": straddle_mark,
        "spread_pct_of_mid": (straddle_ask - straddle_mid) / straddle_mid
        if straddle_mid > 0 else None,
        "atm_mark_iv": mark_iv,
        "atm_ask_iv": ask_iv,
        "implied_move_from_iv": C.STRADDLE_COEFFICIENT * ask_iv * math.sqrt(tte_years),
        "implied_move_from_straddle": straddle_ask,
        "realized_endpoint_abs": realised["endpoint_abs"],
        "realized_max_excursion": realised["max_excursion"],
        # Check B: the option priced at its own tenor against the move over that same tenor.
        "check_b_edge": realised["endpoint_abs"] - straddle_ask,
        "excursion_minus_ask": realised["max_excursion"] - straddle_ask,
    }


def _median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def verdict(measured: list[dict[str, Any]], attempted: int) -> dict[str, Any]:
    ok = [m for m in measured if m["status"] == "OK"]
    unmeasurable_share = 1.0 - (len(ok) / attempted) if attempted else 1.0

    if len(ok) < C.MIN_MEASURABLE_EVENTS or unmeasurable_share > C.MAX_UNMEASURABLE_SHARE:
        return {"verdict": C.INCONCLUSIVE,
                "reason": f"{len(ok)} measurable of {attempted} attempted "
                          f"(minimum {C.MIN_MEASURABLE_EVENTS}, maximum unmeasurable share "
                          f"{C.MAX_UNMEASURABLE_SHARE})",
                "measurable": len(ok), "attempted": attempted}

    edge = _median([m["check_b_edge"] for m in ok])
    excursion_gap = _median([m["excursion_minus_ask"] for m in ok])
    if edge is not None and edge <= 0:
        return {"verdict": C.CONFIRMS_UNPROMISING,
                "reason": f"median Check B edge {edge:+.4f} of underlying is not positive",
                "median_check_b_edge": edge, "measurable": len(ok), "attempted": attempted}
    if edge is not None and excursion_gap is not None and excursion_gap > 0:
        return {"verdict": C.EARLY_EXIT_WORTH_STUDYING,
                "reason": f"median Check B edge {edge:+.4f} and median excursion minus ask "
                          f"{excursion_gap:+.4f} are both positive",
                "median_check_b_edge": edge, "median_excursion_minus_ask": excursion_gap,
                "measurable": len(ok), "attempted": attempted}
    return {"verdict": C.CONFIRMS_UNPROMISING,
            "reason": f"median Check B edge {edge:+.4f} is positive but the excursion does not "
                      f"clear the ask ({excursion_gap:+.4f})",
            "median_check_b_edge": edge, "measurable": len(ok), "attempted": attempted}


def run(levels: tuple[float, ...] = (C.PRIMARY_THRESHOLD,) + C.SECONDARY_THRESHOLDS,
        write: bool = True, verbose: bool = True) -> dict[str, Any]:
    contract_hash = C.require_frozen()
    grid = dataset.load()

    # Every instant any combo or level will ask for, gathered before a byte is downloaded. A
    # daily file is fetched once and filtered in the pipe; re-fetching per combo would download
    # the same ten gigabytes several times over.
    plan: dict[str, set[int]] = {}
    requests: list[tuple[int, int, float, list[dict[str, Any]]]] = []
    for horizon, threshold_bp in C.REPORTED_COMBOS:
        for level in levels:
            events = events_for(horizon, threshold_bp, level)
            requests.append((horizon, threshold_bp, level, events))
            for event in events:
                plan.setdefault(event["day"], set()).add(event["ts_ms"] * MS_TO_US)

    books_by_day: dict[str, dict[int, dict[str, CH.Quote]]] = {}
    unavailable: dict[str, str] = {}
    for day in sorted(plan):
        wanted = sorted(plan[day])
        if verbose:
            print(f"  fetching {day}: {len(wanted)} instants", flush=True)
        try:
            path = CH.fetch_windows(day, wanted, C.STALE_TOLERANCE_SECONDS * MICROS)
        except RuntimeError as exc:
            # One unreachable date should shrink the sample and be named, not destroy the run.
            # The free endpoint rate-limits after a long sequence of multi-gigabyte pulls.
            unavailable[day] = str(exc)
            books_by_day[day] = {t: {} for t in wanted}
            if verbose:
                print(f"    UNAVAILABLE: {exc}", flush=True)
            continue
        books_by_day[day] = CH.latest_before(path, wanted, C.STALE_TOLERANCE_SECONDS * MICROS)
        sizes = [len(b) for b in books_by_day[day].values()]
        # An instant well inside the day must find a book. When it does not, the filter matched
        # nothing, which is a pipeline fault and not an absence of quotes; letting it through
        # would quietly become NOT_MEASURABLE and then INCONCLUSIVE, hiding the cause.
        midnight_us = int(np.datetime64(day, "ms").astype("int64")) * MS_TO_US
        well_inside = [t for t in wanted
                       if t - midnight_us > C.STALE_TOLERANCE_SECONDS * MICROS]
        if well_inside and max(sizes) == 0:
            raise RuntimeError(
                f"{day}: {len(well_inside)} instants sit inside the day yet no BTC quote "
                f"survived the filter; the window arithmetic or the download is wrong")
        if verbose:
            print(f"    {path.stat().st_size / 1e6:.1f} MB, instruments per instant "
                  f"min {min(sizes)} max {max(sizes)}", flush=True)

    combos: dict[str, Any] = {}
    for horizon, threshold_bp, level, events in requests:
        label = f"{horizon // 60}H_{threshold_bp:03d}"
        by_level = combos.setdefault(label, {})
        if not events:
            by_level[f"p>={level:.2f}"] = {"events": 0, "measured": [],
                                           "verdict": {"verdict": C.INCONCLUSIVE,
                                                       "reason": "no events on free dates"}}
            continue
        measured = [measure(event, books_by_day[event["day"]].get(event["ts_ms"] * MS_TO_US, {}),
                            grid) for event in events]
        by_level[f"p>={level:.2f}"] = {
            "events": len(events),
            "dates": sorted({e["day"] for e in events}),
            "measured": measured,
            "verdict": verdict(measured, len(events)),
            "summary": _summarise(measured),
        }

    primary_label = f"{C.PRIMARY_COMBO[0] // 60}H_{C.PRIMARY_COMBO[1]:03d}"
    primary = combos[primary_label][f"p>={C.PRIMARY_THRESHOLD:.2f}"]

    payload = {
        "record": "CRYPTO_BTC_VOL_A_RESULTS_V1",
        "contract_sha256": contract_hash,
        "source": "Tardis free Deribit options_chain, first day of each month, no API key",
        "orders": 0, "api_keys": 0, "data_purchased": False, "deployed": False,
        "early_exit_computed": False,
        "dates_unavailable": unavailable,
        "vol_p0_reference": {"proxy_iv": C.VOL_P0_PROXY_IV,
                             "forward_realised_vol": C.VOL_P0_FORWARD_RV,
                             "breakeven_iv": C.VOL_P0_BREAKEVEN_IV},
        "primary": {"combo": primary_label, "threshold": C.PRIMARY_THRESHOLD,
                    **primary["verdict"]},
        "combos": combos,
    }
    if write:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUT_DIR / "results_v1.json").write_text(json.dumps(payload, indent=1, default=str))
    return payload


def _summarise(measured: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [m for m in measured if m["status"] == "OK"]
    if not ok:
        reasons: dict[str, int] = {}
        for m in measured:
            reasons[m.get("reason", "?")] = reasons.get(m.get("reason", "?"), 0) + 1
        return {"measurable": 0, "reasons": reasons}
    return {
        "measurable": len(ok),
        "median_atm_mark_iv": _median([m["atm_mark_iv"] for m in ok]),
        "median_atm_ask_iv": _median([m["atm_ask_iv"] for m in ok]),
        "median_time_to_expiry_hours": _median([m["time_to_expiry_hours"] for m in ok]),
        "median_straddle_ask": _median([m["straddle_ask"] for m in ok]),
        "median_straddle_mid": _median([m["straddle_mid"] for m in ok]),
        "median_spread_pct_of_mid": _median([m["spread_pct_of_mid"] for m in ok
                                             if m["spread_pct_of_mid"] is not None]),
        "median_implied_move_from_iv": _median([m["implied_move_from_iv"] for m in ok]),
        "median_realized_endpoint_abs": _median([m["realized_endpoint_abs"] for m in ok]),
        "median_realized_max_excursion": _median([m["realized_max_excursion"] for m in ok]),
        "median_check_b_edge": _median([m["check_b_edge"] for m in ok]),
        "median_excursion_minus_ask": _median([m["excursion_minus_ask"] for m in ok]),
        "median_quote_staleness_seconds": _median([m["quote_staleness_seconds"] for m in ok]),
    }


def main() -> int:
    payload = run()
    print(f"\nBTC-VOL-A  contract {payload['contract_sha256'][:16]}")
    for label, levels in payload["combos"].items():
        for level, entry in levels.items():
            summary = entry.get("summary", {})
            if not summary.get("measurable"):
                print(f"  {label} {level}: {entry['events']} events, 0 measurable "
                      f"{summary.get('reasons', '')}")
                continue
            print(f"  {label} {level}: {entry['events']} events, "
                  f"{summary['measurable']} measurable")
            print(f"      ATM mark IV {summary['median_atm_mark_iv'] * 100:6.1f}%  "
                  f"ask IV {summary['median_atm_ask_iv'] * 100:6.1f}%  "
                  f"TTE {summary['median_time_to_expiry_hours']:5.1f}h")
            print(f"      straddle ask {summary['median_straddle_ask'] * 100:5.2f}%  "
                  f"realised endpoint {summary['median_realized_endpoint_abs'] * 100:5.2f}%  "
                  f"edge {summary['median_check_b_edge'] * 100:+5.2f}%")
            print(f"      -> {entry['verdict']['verdict']}")
    print(f"\nPRIMARY: {payload['primary']['combo']} "
          f"p>={payload['primary']['threshold']:.2f} -> {payload['primary']['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
