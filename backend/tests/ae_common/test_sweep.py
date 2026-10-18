"""A's 09:15 sweep: order, work-stealing, one claim per symbol, the deadline, E's fields untouched."""

from datetime import time

from app.ae_common import sweep as SW
from app.strategy_a_mover_live import acquisition as AQ
from app.strategy_e_max_rt import finalizer as FZ
from tests.ae_common.helpers import SESSION, Clock, at, lanes, minute_rows

A_SYMBOLS = [f"A{i:03d}" for i in range(60)]
E_SYMBOLS = A_SYMBOLS[:20] + ["EONLY1", "EONLY2"]


def caches_for(symbols, complete_through=9 * 60 + 10):
    out = {}
    for s in symbols:
        c = FZ.SymbolCache(s, "ND", s)
        c.complete_through = complete_through          # rolled up to 09:10, as rolling leaves it
        out[s] = c
    return out


def pages(symbols, last=9 * 60 + 30):
    return {s: minute_rows(SESSION, last) for s in symbols}


def test_the_plan_puts_e_overlap_first_then_a_only_in_hash_order_and_never_alphabetically():
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    overlap = [s for s in plan.minute_order if s in set(E_SYMBOLS)]
    a_only = [s for s in plan.minute_order if s not in set(E_SYMBOLS)]
    assert plan.minute_order == tuple(overlap + a_only)
    assert overlap == SW.hash_order(overlap) and a_only == SW.hash_order(a_only)
    assert a_only != sorted(a_only)                      # the alphabetical order is gone
    assert not set(plan.minute_order) & set(tick)
    assert plan.steal_pool == frozenset(a_only)
    # E-only symbols of E's minute shard are not swept; E-only symbols of its tick shard are
    minute_e_only = {s for s in ("EONLY1", "EONLY2") if s not in set(tick)}
    assert not minute_e_only & set(plan.symbols)


def test_with_time_to_spare_every_symbol_is_read_once_and_the_tick_lane_steals_a_only_symbols():
    clock = Clock(at(time(9, 15)))
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    lane_minute, lane_tick, clients, limiters = lanes(pages(plan.symbols), clock)
    caches = caches_for(plan.symbols)
    for lim in limiters:
        lim.begin_phase("a_sweep", at(time(9, 25)))
    swept = SW.run(lane_minute, lane_tick, caches, plan, session=SESSION, now=clock, deadline=at(time(9, 25)))
    outcomes = swept.a_pass.outcomes
    assert set(outcomes) == set(plan.symbols) and not swept.not_reached and not swept.interrupted
    requested = [symbol for client in clients for _, _, symbol in client.sent]
    assert sorted(requested) == sorted(plan.symbols)     # one request per symbol, no duplicate
    stolen = {s for s, o in outcomes.items() if o.lane == "B" and s not in set(tick)}
    assert stolen and stolen <= plan.steal_pool          # the tick lane only takes A-only symbols
    assert swept.steals == len(stolen)
    assert all(o.usable for o in outcomes.values())


def test_the_sweep_leaves_e_verdict_fields_alone():
    clock = Clock(at(time(9, 15)))
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    lane_minute, lane_tick, _, _ = lanes(pages(plan.symbols), clock)
    caches = caches_for(plan.symbols)
    SW.run(lane_minute, lane_tick, caches, plan, session=SESSION, now=clock, deadline=at(time(9, 25)))
    assert all(c.finalized_at is None and c.data_source is None and c.contiguous is None for c in caches.values())
    assert all(c.complete_through >= 9 * 60 + 14 for c in caches.values())


def test_a_cut_sweep_misses_a_hash_band_of_a_only_symbols_never_an_alphabetical_tail_or_e_overlap():
    clock = Clock(at(time(9, 24, 50)))                   # ten seconds: far too little for 62 symbols
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    lane_minute, lane_tick, clients, limiters = lanes(pages(plan.symbols), clock)
    caches = caches_for(plan.symbols)
    for lim in limiters:
        lim.begin_phase("a_sweep", at(time(9, 25)))
    swept = SW.run(lane_minute, lane_tick, caches, plan, session=SESSION, now=clock, deadline=at(time(9, 25)))
    missed = set(swept.not_reached) | set(swept.interrupted)
    assert missed and swept.a_pass.deadline_hit
    assert all(sent < at(time(9, 25)) for client in clients for sent, _, _ in client.sent)
    assert missed <= plan.steal_pool                     # E-overlap and E's tick shard were all read
    stealable = [s for s in plan.minute_order if s in plan.steal_pool]
    first = min(stealable.index(s) for s in missed)
    assert sorted(stealable.index(s) for s in missed) == list(range(first, first + len(missed)))
    assert sorted(missed) != sorted(plan.symbols)[-len(missed):]   # not the alphabetical tail


def test_the_old_alphabetical_pass_starves_the_tail_the_sweep_does_not(tmp_path):
    """Same symbols, same request budget: ``run_a_pass`` loses the alphabetical tail, the sweep a hash band."""
    budget_clock = Clock(at(time(9, 24, 50)))
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    union = sorted(set(A_SYMBOLS) | set(tick))
    lane_minute, lane_tick, _, limiters = lanes(pages(union), budget_clock)
    for lim in limiters:
        lim.begin_phase("a_sweep", at(time(9, 25)))
    caches = caches_for(union)
    order = AQ.plan_order([s for s in sorted(A_SYMBOLS)], tick)
    old = AQ.run_a_pass(lane_minute, lane_tick, caches, session=SESSION, order=order, tick_shard=tick,
                        now=budget_clock, deadline=at(time(9, 25)))
    minute_side = [s for s in order if s not in set(tick)]
    old_missed = [s for s in minute_side if s not in old.outcomes]
    assert old_missed and old_missed == minute_side[-len(old_missed):]   # the alphabetical tail
    assert all(s in old.outcomes for s in tick)          # its tick lane did only E's shard


def test_a_request_hanging_past_the_deadline_neither_holds_the_sweep_nor_reaches_the_cache():
    """Production saw a 202 s answer. The sweep closes at 09:25:00; the late answer is discarded."""
    import threading
    from tests.ae_common.helpers import LimitedClient
    clock = Clock(at(time(9, 24, 55)))
    _, tick = FZ.hash_lanes(E_SYMBOLS)
    plan = SW.plan(A_SYMBOLS, E_SYMBOLS, tick)
    lane_minute, lane_tick, clients, limiters = lanes(pages(plan.symbols), clock)
    release = threading.Event()
    hung = tick[0]

    class Hanging(LimitedClient):
        def request(self, api_id, path, body, continuation=None):
            if str(body["stk_cd"]) == hung:
                release.wait(timeout=10)
            return super().request(api_id, path, body, continuation)
    lane_tick.client = Hanging(pages(plan.symbols), clock, limiters[1])
    caches = caches_for(plan.symbols)
    before = dict(caches[hung].bars), caches[hung].complete_through
    for lim in limiters:
        lim.begin_phase("a_sweep", at(time(9, 25)))
    swept = SW.run(lane_minute, lane_tick, caches, plan, session=SESSION, now=clock, deadline=at(time(9, 25)))
    # the hung walker is detached; the minute walker may also be mid-step at the instant the sweep
    # closes - either way nothing it answers later is kept
    assert swept.detached_walkers >= 1 and hung in swept.not_reached
    release.set()
    import time as _t
    _t.sleep(0.2)                                         # let the late answer arrive
    assert (caches[hung].bars, caches[hung].complete_through) == before
    assert hung not in swept.a_pass.outcomes
