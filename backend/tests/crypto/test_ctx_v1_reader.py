"""The forward journal answers Market Context R0's per-second questions without the research journal.

R0's T2 frame is built by `market_context_r0.journal.replay`, which is not on this branch, so its
two decisions are restated here exactly as that module makes them and applied to the **full** V0
wall stream of a research twin fed the same input:

* a row is resting if `status == ACTIVE`, or it is not ENDED/INTERRUPTED and its event is
  OPENED/UPDATED; otherwise it removes its key;
* a sample sees the rows whose `seq` is below its `derived` record's `seq`, and applies
  `wallrule.select` to them with no continuity callables.

The reader must produce the same walls from `context` + `wall_r0` alone, every second, through a
gap, a HARD resync and a pulled wall. The live equivalent - R0's own module on a 25-minute
recording - is in the report: 2,996 of 2,996 side-samples identical and all six T2 families
identical.
"""
from __future__ import annotations

from decimal import Decimal

from app.crypto.context_collector_v1 import reader as RD
from app.crypto.context_collector_v1.wallr0 import passes_open_floors
from app.crypto.liquidity_map import wallrule as R
from app.crypto.market_structure_v0.envelope import Session

from tests.crypto.ctx_v1_fixtures import BASE_MS, Script, context_collector, flush, research_collector
from tests.crypto.liquidity_map_fixtures import wall_snapshot
from tests.crypto.ms_v0_fixtures import depth_frame


def r0_frame(sink) -> dict[int, dict[str, list]]:
    """R0's replay, restated, over a research twin's full envelope stream."""
    resting: dict[tuple[str, str], dict] = {}
    out = {}
    for record in sink.records:
        if record["kind"] == "wall":
            p = record["payload"]
            key = (str(p.get("side") or ""), str(p.get("price") or ""))
            status, event = str(p.get("status") or ""), str(p.get("event") or "")
            if status == "ACTIVE" or (status not in ("ENDED", "INTERRUPTED")
                                      and event in ("OPENED", "UPDATED")):
                resting[key] = p
            else:
                resting.pop(key, None)
        elif record["kind"] == "derived":
            book = record["payload"]["book"]
            dec = lambda v: None if v is None else Decimal(str(v))
            out[record["receive_ms"]] = {
                side: R.select(list(resting.values()), side=side, mid=dec(book.get("mid")),
                               latest_sample_ms=record["receive_ms"],
                               known_low=dec(book.get("known_low")),
                               known_high=dec(book.get("known_high"))).walls
                for side in ("BID", "ASK")}
    return out


def scripted(tmp_path):
    session = Session(started_ms=BASE_MS, started_ns=0)
    research, research_sink = research_collector(tmp_path, session=session)
    twin = Session(session_id=session.session_id, started_ms=BASE_MS, started_ns=0)
    ctx, ctx_sink = context_collector(tmp_path, session=twin)
    script = Script([research, ctx])
    script.start()
    script.run(25)
    script.second = 26
    # Pull the ask wall, add a bid wall, and add a level that V0 calls a candidate (4x its
    # neighbours) but R2 never can (below 5x), which is what the R0 filter exists to leave out.
    script.depth(25.5, asks=[["84820", "0"]], bids=[["84700", "12"], ["84640", "4"]])
    script.trade(25.6)
    script.sample(26)
    script.run(12)
    script.each("on_depth_frame", depth_frame(first=999_000, last=999_100, previous=998_000),
                receive_ms=script.ms(38.3), mono_ns=script.ns(38.3))
    script.sample(39)
    script.each("mark_snapshot_requested", receive_ms=script.ms(39.1), mono_ns=script.ns(39.1))
    script.each("on_snapshot", wall_snapshot(last_update_id=9_000, big_ask_offset=3),
                receive_ms=script.ms(39.2), mono_ns=script.ns(39.2), request_ms=script.ms(39.1),
                request_mono_ns=script.ns(39.1))
    script.each("on_depth_frame", depth_frame(first=8_900, last=9_100, previous=8_850),
                receive_ms=script.ms(39.3), mono_ns=script.ns(39.3))
    script.next_update = 9_100
    script.second = 39
    script.run(20)
    for c in (research, ctx):
        flush(c.store)
        c.store.close()
    return research_sink, ctx, ctx_sink


def test_r0_walls_are_rebuilt_exactly_from_the_forward_journal(tmp_path):
    research_sink, ctx, _ = scripted(tmp_path)
    expected = r0_frame(research_sink)
    seconds = list(RD.seconds(ctx.store.root))
    # Here the session record is flushed before the first sample; live, the first sample can
    # precede that flush and is then skipped as a reading without a session.
    assert len(seconds) == len(expected)
    compared = with_walls = 0
    for second in seconds:
        assert second.walls_r0 == expected[second.sample_ms], second.sample_ms
        compared += 1
        with_walls += bool(second.walls_r0["ASK"] or second.walls_r0["BID"])
    assert compared == len(seconds) and with_walls > 30


def test_only_rows_that_could_ever_qualify_under_open_row_values_are_kept(tmp_path):
    research_sink, ctx, ctx_sink = scripted(tmp_path)
    full = research_sink.of("wall")
    kept = ctx_sink.of("wall_r0")
    assert 0 < len(kept) < len(full)
    opened_kept = [row for row in kept if row["event"] == "OPENED"]
    assert opened_kept and all(passes_open_floors(row) for row in opened_kept)
    refused_opens = [row for row in full if row["event"] == "OPENED"
                     and not passes_open_floors(row)]
    assert refused_opens, "the fixture must contain candidates R0 can never select"
    assert set(kept[0]) == {"v0_seq", "event", "status", "side", "price", "qty", "notional",
                            "multiple", "first_seen_ms", "persistence_ms", "coverage",
                            "generation", "local_average", "neighbours"}


def test_display_walls_are_the_bins_the_collector_had_open(tmp_path):
    _, ctx, ctx_sink = scripted(tmp_path)
    seconds = list(RD.seconds(ctx.store.root))
    contexts = {c["sample_ms"]: c for c in ctx_sink.of("context") if c.get("sample_ms")}
    for second in seconds:
        counts = {side: len(bins) for side, bins in second.walls_display.items()}
        assert counts == contexts[second.sample_ms]["open_v2_bins"]


def test_sessions_are_read_in_the_order_they_started(tmp_path):
    """The later session's id sorts first; the reader must still return it second."""
    for k, sid in enumerate(("ffffffff-0000-0000-0000-000000000000",
                             "00000000-0000-0000-0000-000000000000")):
        offset_ms = k * 100_000
        session = Session(session_id=sid, started_ms=BASE_MS + offset_ms, started_ns=0)
        ctx, _ = context_collector(tmp_path, session=session)
        script = Script([ctx])
        script.ms = lambda i, offset_ms=offset_ms: BASE_MS + offset_ms + int(i * 1000)
        script.start()
        script.run(3)
        flush(ctx.store)
        ctx.store.close()
    seen = [second.session_id for second in RD.seconds(tmp_path / "ctx")]
    assert seen[0].startswith("ffffffff") and seen[-1].startswith("00000000")
    times = [second.sample_ms for second in RD.seconds(tmp_path / "ctx")]
    assert times == sorted(times)
