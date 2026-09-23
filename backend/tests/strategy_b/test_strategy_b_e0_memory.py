"""The replay memory optimisation changes representation and lifetime, never a value.

Two changes in ``session_source`` are pinned here:

* a warmup session's bars are read, turned into its RVOL profile and dropped, instead of being
  held until the end of ``symbol_sessions`` (that was the replay's memory peak);
* each profile curve is stored as a float64 ``array`` instead of a tuple of Python floats.

Every test compares the optimised output with the reference built the original way, on the
synthetic mirror whose symbols stand for the dense, sparse, gap, split, IPO, quiet and short
history cases.
"""

from array import array
from datetime import date, timedelta

import pytest

from app.backtest.strategy_b.costs import CostModel, FillScenario
from app.backtest.strategy_b.engine import run_session
from app.backtest.strategy_b.portfolio import Portfolio
from app.backtest.strategy_b_e0.session_source import compact_profile
from app.strategy_b.config import StrategyBConfig
from app.strategy_b.features import SessionTape
from app.strategy_b.rvol import build_volume_profile, time_of_day_rvol
from tests.strategy_b.test_strategy_b_e0_adapter import SCOPE, World

CONFIG = StrategyBConfig()


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return World(tmp_path_factory.mktemp("memory"))


def _reference_profile(world: World, symbol: str, day: date):
    bounds = world.source().boundaries(day)
    tape = SessionTape(symbol, bounds, list(world.cache.bars(symbol, day, bounds)))
    return build_volume_profile(tape, CONFIG.rvol.scope)


def _curves(profile):
    return {str(k): (tuple(c.elapsed_seconds), tuple(c.cumulative)) for k, c in profile.curves.items()}


def test_compact_profile_holds_the_identical_doubles(world):
    reference = _reference_profile(world, "AAA", SCOPE[0] - timedelta(days=3))
    compact = compact_profile(reference)
    assert (compact.session_date, compact.scope) == (reference.session_date, reference.scope)
    assert _curves(compact) == _curves(reference)
    for curve in compact.curves.values():
        assert isinstance(curve.elapsed_seconds, array) and curve.elapsed_seconds.typecode == "d"
        assert isinstance(curve.cumulative, array) and curve.cumulative.typecode == "d"


def test_every_source_profile_equals_the_reference_built_from_the_tape(world):
    source = world.source()
    checked = 0
    for session in SCOPE:
        source.symbol_sessions(session, world.universe.symbols_for(session))
        for (symbol, day), profile in source._profiles.items():
            assert _curves(profile) == _curves(_reference_profile(world, symbol, day)), (symbol, day)
            checked += 1
    assert checked > 0


def test_rvol_is_identical_with_compact_and_tuple_histories(world):
    source = world.source()
    compared = 0
    for session in SCOPE:
        for item in source.symbol_sessions(session, world.universe.symbols_for(session)):
            reference = tuple(_reference_profile(world, item.symbol, p.session_date)
                              for p in item.rvol_history)
            for bar in item.tape.bars:
                args = dict(splits=item.splits, config=CONFIG.rvol)
                assert time_of_day_rvol(item.tape, bar.available_at, item.rvol_history, **args) == \
                    time_of_day_rvol(item.tape, bar.available_at, reference, **args)
                compared += 1
    assert compared > 0


def _run(items, session, boundaries):
    portfolio = Portfolio(equity=7428.92, costs=CostModel(10.0, 15.0), risk=CONFIG.risk)
    return run_session(items, config=CONFIG, portfolio=portfolio,
                       scenario=FillScenario.NEXT_BAR_OPEN, boundaries=boundaries)


def test_the_engine_output_is_identical_with_compact_and_tuple_histories(world):
    from dataclasses import replace

    from app.backtest.strategy_b_e0 import artifacts
    source = world.source()
    for session in SCOPE:
        items = source.symbol_sessions(session, world.universe.symbols_for(session))
        tuple_items = [replace(item, rvol_history=tuple(
            _reference_profile(world, item.symbol, p.session_date) for p in item.rvol_history))
            for item in items]
        bounds = source.boundaries(session)
        a, b = _run(items, session, bounds), _run(tuple_items, session, bounds)
        assert artifacts.session_row(a) == artifacts.session_row(b)
        assert [artifacts.candidate_row(c, session) for c in a.finished_candidates] == \
            [artifacts.candidate_row(c, session) for c in b.finished_candidates]
        assert [artifacts.trade_row(t) for t in a.trades] == [artifacts.trade_row(t) for t in b.trades]


def test_warmup_bars_are_not_retained_after_their_profile_is_built(world):
    source = world.source()
    for session in SCOPE:
        source.symbol_sessions(session, world.universe.symbols_for(session))
        held = {day for _, day in source._bars}
        assert held <= {session}, held  # only D survives the call, as before
        assert source._profiles  # the profiles themselves are kept for later sessions


def test_warmup_bars_are_never_cached_during_the_call(world, monkeypatch):
    """The memory peak came from every warmup tape being alive at once inside one call."""
    source = world.source()
    session = SCOPE[0]
    seen: list[int] = []
    original = source._flags

    def spy(symbol, day, records):
        seen.append(len([k for k in source._bars if k[1] < source._previous_session(day)]))
        return original(symbol, day, records)

    monkeypatch.setattr(source, "_flags", spy)
    source.symbol_sessions(session, world.universe.symbols_for(session))
    assert seen and max(seen) == 0


# ---- code rebind guards -----------------------------------------------------------------------


def _rebind_env(tmp_path, monkeypatch, files):
    """A frozen contract copy with one code record, and a working tree whose files are ``files``."""
    import json as _json

    from app.backtest.strategy_b_e0 import code_identity
    from app.dev import run_strategy_b_e0 as cli
    from tests.strategy_b.test_strategy_b_e0_authoritative import frozen_copy
    contract_path, _, frozen = frozen_copy(tmp_path)
    ledger = tmp_path / "ledger.jsonl"
    record = _json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    record["code_files"] = {"app/backtest/strategy_b_e0/session_source.py": "0" * 64,
                            "app/strategy_b/rvol.py": "1" * 64}
    ledger.write_text(_json.dumps(record) + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "FREEZE_LEDGER", ledger)
    monkeypatch.setattr(code_identity, "identity",
                        lambda root: {"version": "t", "code_digest": "d" * 64, "file_count": 2,
                                      "files": files})
    return cli, contract_path, ledger


def _evidence(tmp_path, verdict):
    import json as _json
    path = tmp_path / f"eq_{verdict}.json"
    path.write_text(_json.dumps({"verdict": verdict}), encoding="utf-8")
    bench = tmp_path / "bench.json"
    bench.write_text("{}", encoding="utf-8")
    return path, bench


def test_a_rebind_that_moves_the_pure_strategy_layer_is_refused(tmp_path, monkeypatch):
    cli, contract_path, ledger = _rebind_env(tmp_path, monkeypatch, {
        "app/backtest/strategy_b_e0/session_source.py": "0" * 64, "app/strategy_b/rvol.py": "9" * 64})
    eq, bench = _evidence(tmp_path, "EQUIVALENT")
    before = ledger.read_bytes()
    with pytest.raises(SystemExit, match="pure strategy layer"):
        cli.main(["--contract", str(contract_path), "rebind-code", "--reason", "t",
                  "--equivalence", str(eq), "--benchmark", str(bench)])
    assert ledger.read_bytes() == before


def test_a_rebind_without_equivalence_is_refused(tmp_path, monkeypatch):
    cli, contract_path, ledger = _rebind_env(tmp_path, monkeypatch, {
        "app/backtest/strategy_b_e0/session_source.py": "9" * 64, "app/strategy_b/rvol.py": "1" * 64})
    eq, bench = _evidence(tmp_path, "DIFFERENT")
    before = ledger.read_bytes()
    with pytest.raises(SystemExit, match="equivalence verdict DIFFERENT"):
        cli.main(["--contract", str(contract_path), "rebind-code", "--reason", "t",
                  "--equivalence", str(eq), "--benchmark", str(bench)])
    assert ledger.read_bytes() == before


def test_an_accepted_rebind_is_appended_and_becomes_the_bound_digest(tmp_path, monkeypatch):
    import json as _json

    from app.backtest.strategy_b_e0 import freeze
    from app.backtest.strategy_b_e0.contract import load_contract
    cli, contract_path, ledger = _rebind_env(tmp_path, monkeypatch, {
        "app/backtest/strategy_b_e0/session_source.py": "9" * 64, "app/strategy_b/rvol.py": "1" * 64})
    eq, bench = _evidence(tmp_path, "EQUIVALENT")
    contract_before = contract_path.read_bytes()
    cli.main(["--contract", str(contract_path), "rebind-code", "--reason", "memory",
              "--equivalence", str(eq), "--benchmark", str(bench)])
    rows = [_json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[-1]["record"] == "CODE_REBIND"
    assert rows[-1]["modified_files"] == ["app/backtest/strategy_b_e0/session_source.py"]
    assert rows[-1]["old_code_digest"] == "c" * 64 and rows[-1]["code_digest"] == "d" * 64
    assert contract_path.read_bytes() == contract_before  # the frozen contract is never edited
    bound = freeze.require_frozen(load_contract(contract_path),
                                  contract_path.parent / "b_e0_contract_v1.sha256")
    assert cli.bound_code_digest(bound) == "d" * 64
