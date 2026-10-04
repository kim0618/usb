"""A-MOVER-SCANNER-V1 unit tests. No network, no database, no provider, no replay.

The premarket panel is driven by a stand-in for the B-E0 session cache: ``build_panel`` reads
only a symbol list, a session list, ``coverage`` and the two row arrays, so a hand-built tape
exercises the window arithmetic exactly, including the point-in-time cut. The production
reader itself is exercised by the research run.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal

import numpy as np
import pytest

from app.backtest.mover_scanner_v1 import compare as C
from app.backtest.mover_scanner_v1 import handoff as H
from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1 import score as S
from app.backtest.mover_scanner_v1 import universe as U
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.daily import DailyPanel
from app.backtest.mover_scanner_v1.scan import Rejection, gate_reading, scan_session
from app.core.exceptions import DataError
from app.research.prompt import ResearchPromptService
from app.research.versions import TOP8_PROMPT_VERSION
from app.strategy.config import GapDirection, StrategyConfig
from app.strategy.engine import PremarketContext, StrategyV0Engine

SESSION = date(2026, 6, 15)
PRIOR = tuple(date(2026, 5, d) for d in range(1, 21))


@dataclass
class FakeCoverage:
    start: int
    end: int
    rows: int


class FakeCache:
    """The subset of ``SessionCache`` that ``build_panel`` uses."""

    def __init__(self, bars_by_pair, sessions, symbols):
        self.sessions = tuple(sessions)
        self.symbols = tuple(symbols)
        self.digest = "fake-cache"
        stamps: list[int] = []
        values: list[list[float]] = []
        self._rows: dict[tuple[str, date], FakeCoverage] = {}
        for (symbol, session), bars in bars_by_pair.items():
            start = len(stamps)
            for minute, open_, high, low, close, volume in bars:
                stamps.append(P.session_premarket_start_ms(session)
                              + (minute - 240) * P.MS_PER_MINUTE)
                values.append([open_, high, low, close, volume, np.nan, np.nan])
            self._rows[(symbol, session)] = FakeCoverage(start, len(stamps), len(bars))
        self._t = np.asarray(stamps, dtype=np.int64) if stamps else np.zeros(0, dtype=np.int64)
        self._v = (np.asarray(values, dtype=np.float64) if values
                   else np.zeros((0, 7), dtype=np.float64))

    def coverage(self, symbol, session):
        return self._rows.get((symbol, session))


def quiet_history(symbol, volume=1_000.0):
    """Twenty prior sessions of one thin premarket bar, so a baseline always exists."""
    return {(symbol, day): [(300, 10.0, 10.0, 10.0, 10.0, volume)] for day in PRIOR}


def flat_daily(symbols, sessions, close=10.0, volume=1_000_000.0):
    grid = tuple(sessions)
    return DailyPanel(grid,
                      {symbol: np.full(len(grid), close) for symbol in symbols},
                      {symbol: np.full(len(grid), volume) for symbol in symbols})


# ---- gap quality (section H) ------------------------------------------------------------


def test_gap_quality_follows_the_declared_shape():
    config = MoverScannerConfig()
    assert S.gap_quality(-0.10, config) == 0.0
    assert S.gap_quality(0.0, config) < S.gap_quality(0.02, config)
    assert S.gap_quality(0.02, config) < S.gap_quality(0.05, config)
    assert S.gap_quality(0.05, config) < S.gap_quality(0.10, config)
    assert S.gap_quality(0.10, config) == S.gap_quality(0.15, config) == 1.0
    assert S.gap_quality(0.20, config) < S.gap_quality(0.15, config)
    assert S.gap_quality(0.40, config) < S.gap_quality(0.20, config)


def test_a_twenty_percent_gap_scores_below_a_mid_single_digit_one():
    """Section H: a 20%+ gap must not be pushed to the top of the ranking."""
    config = MoverScannerConfig()
    assert S.gap_quality(0.20, config) < S.gap_quality(0.04, config)
    assert S.gap_quality(0.30, config) < S.gap_quality(0.025, config)


def test_a_sub_two_percent_gap_is_scored_not_rejected():
    config = MoverScannerConfig()
    assert S.gap_quality(0.005, config) > 0.0


# ---- components -------------------------------------------------------------------------


def test_momentum_reads_a_held_move_above_a_faded_one():
    config = MoverScannerConfig()
    held = S.momentum_parts(11.0, 10.5, 11.0, 10.0, 0.7, config)
    faded = S.momentum_parts(10.1, 11.0, 11.0, 10.0, 0.3, config)
    assert held["pm_momentum"] > faded["pm_momentum"]
    assert held["high_proximity"] == pytest.approx(1.0)
    assert faded["late_return"] < 0


def test_momentum_is_neutral_on_a_flat_premarket_range():
    config = MoverScannerConfig()
    parts = S.momentum_parts(10.0, 10.0, 10.0, 10.0, 0.0, config)
    assert parts["high_proximity"] == 0.5
    assert 0.0 <= parts["pm_momentum"] <= 1.0


def test_tradability_penalises_thin_and_cheap_names():
    config = MoverScannerConfig()
    deep = S.tradability(50.0, 5e8, 200, config)["tradability_score"]
    thin = S.tradability(1.2, 2e5, 4, config)["tradability_score"]
    assert deep == pytest.approx(1.0)
    assert thin < 0.2


# ---- normalisation and dominance (section G) --------------------------------------------


def test_normalisation_keeps_a_heavy_tailed_component_from_dominating():
    config = MoverScannerConfig()
    raw = {}
    for index in range(40):
        raw[f"S{index:02d}"] = {
            "pm_dollar_volume": 10.0 ** (4 + index / 4),   # six orders of magnitude
            "pm_rvol": 1.0 + index / 10,
            "gap_quality": 0.5,
            "pm_momentum": 0.5,
            "tradability": 0.5,
        }
    normalized = S.normalize_components(raw, config)
    shares = S.dominance([S.contributions(normalized[symbol], config) for symbol in raw])
    assert shares["pm_dollar_volume"] < 0.75
    assert max(abs(row["pm_dollar_volume"]) for row in normalized.values()) < 3.0


def test_normalisation_preserves_gap_quality_order():
    """Standardisation is monotone, so the gap shape survives it."""
    config = MoverScannerConfig()
    raw = {symbol: {"pm_dollar_volume": 1e6, "pm_rvol": 2.0,
                    "gap_quality": S.gap_quality(gap, config),
                    "pm_momentum": 0.5, "tradability": 0.5}
           for symbol, gap in (("MID", 0.04), ("HUGE", 0.20), ("TOP", 0.12))}
    normalized = S.normalize_components(raw, config)
    assert normalized["TOP"]["gap_quality"] > normalized["MID"]["gap_quality"]
    assert normalized["MID"]["gap_quality"] > normalized["HUGE"]["gap_quality"]


def test_a_negative_log_component_is_refused_rather_than_shifted():
    config = MoverScannerConfig()
    raw = {"A": {"pm_dollar_volume": -1.0, "pm_rvol": 1.0, "gap_quality": 0.5,
                 "pm_momentum": 0.5, "tradability": 0.5}}
    with pytest.raises(DataError):
        S.normalize_components(raw, config)


# ---- the premarket window is point in time ----------------------------------------------


def test_the_cut_excludes_every_bar_that_had_not_closed():
    """A bar starting at the cut minute belongs to the future and must not be read."""
    config = MoverScannerConfig()
    bars = [(240, 10.0, 10.0, 10.0, 10.0, 100.0),
            (554, 10.0, 10.0, 10.0, 11.0, 100.0),      # last completed bar
            (555, 11.0, 50.0, 11.0, 50.0, 9_999.0),    # starts at the cut
            (569, 50.0, 90.0, 50.0, 90.0, 9_999.0)]
    cache = FakeCache({("AAA", SESSION): bars}, [SESSION], ["AAA"])
    panel = P.build_panel(cache, config)
    row, column = 0, 0
    assert panel.field("pm_bars", row, column) == 2
    assert panel.field("pm_last_price", row, column) == 11.0
    assert panel.field("pm_high", row, column) == 10.0
    assert panel.field("pm_volume", row, column) == 200.0
    # the gate window does see the later bars, and only it does
    assert panel.field("gate_bars", row, column) == 4
    assert panel.field("gate_last_price", row, column) == 90.0


def test_dollar_volume_and_range_use_the_scan_window_only():
    config = MoverScannerConfig()
    bars = [(300, 10.0, 12.0, 9.0, 11.0, 1_000.0), (560, 11.0, 30.0, 11.0, 30.0, 5_000.0)]
    cache = FakeCache({("AAA", SESSION): bars}, [SESSION], ["AAA"])
    panel = P.build_panel(cache, config)
    assert panel.field("pm_dollar_volume", 0, 0) == pytest.approx(11_000.0)
    assert panel.field("pm_high", 0, 0) == 12.0
    assert panel.field("pm_low", 0, 0) == 9.0


def test_a_covered_session_with_no_print_is_a_zero_not_a_hole():
    config = MoverScannerConfig()
    cache = FakeCache({("AAA", SESSION): []}, [SESSION], ["AAA"])
    panel = P.build_panel(cache, config)
    assert bool(panel.covered[0, 0])
    assert panel.field("pm_volume", 0, 0) == 0.0
    assert np.isnan(panel.field("pm_last_price", 0, 0))


def test_the_rvol_baseline_needs_its_full_history_and_takes_the_median():
    config = MoverScannerConfig()
    pairs = {("AAA", day): [(300, 10.0, 10.0, 10.0, 10.0, float(index + 1) * 100.0)]
             for index, day in enumerate(PRIOR)}
    pairs[("AAA", SESSION)] = [(300, 10.0, 10.0, 10.0, 10.0, 5_000.0)]
    cache = FakeCache(pairs, [*PRIOR, SESSION], ["AAA"])
    panel = P.build_panel(cache, config)
    assert panel.rvol_baseline(0, 19, 20) == (None, 19)
    median, used = panel.rvol_baseline(0, 20, 20)
    assert used == 20
    assert median == pytest.approx(1_050.0)


# ---- eligibility is data quality only (section P) ---------------------------------------


def _single_symbol_scan(bars, *, config=None, close=10.0, volume=1_000_000.0,
                        baseline_volume=1_000.0, symbol="AAA"):
    config = config or MoverScannerConfig()
    pairs = quiet_history(symbol, baseline_volume)
    pairs[(symbol, SESSION)] = bars
    cache = FakeCache(pairs, [*PRIOR, SESSION], [symbol])
    panel = P.build_panel(cache, config)
    daily = flat_daily([symbol], [*PRIOR, SESSION], close=close, volume=volume)
    return scan_session(SESSION, [symbol], panel, daily, config)


def test_a_large_gap_is_never_an_eligibility_failure():
    bars = [(300, 10.0, 60.0, 10.0, 60.0, 50_000.0)] * 1
    bars = [(300 + k, 50.0, 62.0, 49.0, 60.0, 20_000.0) for k in range(5)]
    result = _single_symbol_scan(bars)
    assert result.eligible == 1
    assert result.top[0].gap_pct > 4.0
    assert result.top[0].gap_quality == pytest.approx(0.05)


def test_a_sub_dollar_name_is_removed_as_unexecutable():
    bars = [(300 + k, 0.4, 0.5, 0.3, 0.4, 500_000.0) for k in range(5)]
    result = _single_symbol_scan(bars, close=0.5)
    assert result.eligible == 0
    assert result.rejections[Rejection.PRICE_TOO_LOW] == 1


def test_a_premarket_below_the_notional_floor_is_removed():
    bars = [(300 + k, 10.0, 10.0, 10.0, 10.0, 100.0) for k in range(5)]
    result = _single_symbol_scan(bars)
    assert result.rejections[Rejection.PREMARKET_DOLLAR_VOLUME_TOO_LOW] == 1


def test_too_few_prints_is_distinguished_from_no_print():
    config = MoverScannerConfig()
    two = _single_symbol_scan([(300, 10.0, 10.0, 10.0, 10.0, 50_000.0),
                               (301, 10.0, 10.0, 10.0, 10.0, 50_000.0)], config=config)
    assert two.rejections[Rejection.TOO_FEW_PREMARKET_PRINTS] == 1
    none = _single_symbol_scan([], config=config)
    assert none.rejections[Rejection.NO_PREMARKET_PRINT] == 1


def test_no_market_capitalisation_rule_exists():
    config = MoverScannerConfig()
    assert not any("market_cap" in name or "capitalisation" in name
                   for name in config.declaration())


# ---- pool and output (sections F, L) -----------------------------------------------------


def _many_symbol_panel(count, config, gap_for=None):
    """Notional rises with the index. ``gap_for`` decides each member's gap."""
    gap_for = gap_for or (lambda index: 0.01)
    symbols = [f"S{index:03d}" for index in range(count)]
    pairs: dict = {}
    for index, symbol in enumerate(symbols):
        pairs.update(quiet_history(symbol, 1_000.0))
        close = 10.0 * (1.0 + gap_for(index))
        pairs[(symbol, SESSION)] = [
            (300 + k, close, close, close, close, 5_000.0 * (index + 1)) for k in range(6)]
    cache = FakeCache(pairs, [*PRIOR, SESSION], symbols)
    panel = P.build_panel(cache, config)
    daily = flat_daily(symbols, [*PRIOR, SESSION])
    return symbols, panel, daily


def test_the_pool_is_capped_and_the_output_is_its_top_slice():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(60, config)
    result = scan_session(SESSION, symbols, panel, daily, config)
    assert result.eligible == 60
    assert len(result.pool) == config.pool_size
    assert len(result.top) == config.top_count
    assert {item.symbol for item in result.top} <= {item.symbol for item in result.pool}
    assert [item.candidate_pool_rank for item in result.pool] == list(range(1, 26))
    assert [item.rank for item in result.top] == list(range(1, 9))


def test_the_output_ranks_on_the_full_score_and_the_pool_on_participation():
    """Two genuinely different orderings: the pool finds movers, the output ranks them."""
    config = MoverScannerConfig()
    # The biggest names barely gap; a mid-notional group gaps into the strategy's own band.
    symbols, panel, daily = _many_symbol_panel(
        60, config, gap_for=lambda index: 0.12 if 30 <= index < 40 else 0.001)
    result = scan_session(SESSION, symbols, panel, daily, config)
    scores = [item.total_score for item in result.top]
    assert scores == sorted(scores, reverse=True)
    pool_scores = [item.pool_score for item in result.pool]
    assert pool_scores == sorted(pool_scores, reverse=True)
    # The two cuts disagree, so a pool rank carries information the output rank does not.
    assert any(item.candidate_pool_rank != item.rank
               for item in result.top if item.rank is not None)
    assert [item.symbol for item in result.pool[:config.top_count]] != \
        [item.symbol for item in result.top]


def test_fewer_eligible_candidates_produce_a_shorter_output():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(4, config)
    result = scan_session(SESSION, symbols, panel, daily, config)
    assert result.eligible == 4
    assert len(result.pool) == 4
    assert len(result.top) == 4
    assert [item.rank for item in result.top] == [1, 2, 3, 4]


def test_no_eligible_candidate_produces_no_output_and_no_error():
    config = MoverScannerConfig()
    cache = FakeCache({("AAA", SESSION): []}, [SESSION], ["AAA"])
    panel = P.build_panel(cache, config)
    daily = flat_daily(["AAA"], [SESSION])
    result = scan_session(SESSION, ["AAA"], panel, daily, config)
    assert (result.eligible, result.pool, result.top) == (0, (), ())


def test_the_output_row_carries_every_declared_field():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(30, config)
    row = scan_session(SESSION, symbols, panel, daily, config).top[0].row("09:15 ET")
    for field in ("session_date", "scan_time", "rank", "symbol", "gap_pct", "pm_volume",
                  "pm_dollar_volume", "pm_rvol", "pm_momentum", "pm_range",
                  "tradability_score", "component_scores", "total_score",
                  "candidate_pool_rank"):
        assert field in row, field
    assert set(row["component_scores"]) == set(config.opportunity_weights)


def test_the_scan_is_deterministic():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(40, config)
    first = scan_session(SESSION, symbols, panel, daily, config)
    second = scan_session(SESSION, list(reversed(symbols)), panel, daily, config)
    assert [item.symbol for item in first.top] == [item.symbol for item in second.top]
    assert [item.symbol for item in first.pool] == [item.symbol for item in second.pool]


# ---- the measured gate is Production's own (section Q) -----------------------------------


@pytest.mark.parametrize("last_price,gate_volume,previous_close,adv", [
    (10.30, 60_000.0, 10.0, 1_000_000.0),      # gap 3%, ratio 6%: pass
    (10.10, 60_000.0, 10.0, 1_000_000.0),      # gap 1%: too low
    (12.00, 60_000.0, 10.0, 1_000_000.0),      # gap 20%: too high
    (10.30, 40_000.0, 10.0, 1_000_000.0),      # ratio 4%: too little volume
    (9.50, 600_000.0, 10.0, 1_000_000.0),      # gap down
    (10.20, 50_000.0, 10.0, 1_000_000.0),      # exactly on both thresholds
])
def test_the_gate_reading_matches_the_deployed_engine(last_price, gate_volume,
                                                      previous_close, adv):
    strategy = StrategyConfig()
    mine = gate_reading(last_price, gate_volume, previous_close, adv, strategy)
    theirs = StrategyV0Engine(strategy).premarket_gate(
        PremarketContext(Decimal(repr(previous_close)), Decimal(repr(last_price)),
                         Decimal(repr(gate_volume)), Decimal(repr(adv))),
        human_approved=True, shadow_mode=False)
    assert mine.both_pass is theirs.passed
    assert mine.gap_pct == pytest.approx(float(theirs.gap_pct))
    assert mine.volume_ratio == pytest.approx(float(theirs.volume_ratio))


def test_the_gate_reading_refuses_a_changed_gap_direction():
    """The measurement is the paper UP rule; a research direction must not pass silently."""
    with pytest.raises(ValueError):
        gate_reading(10.3, 60_000.0, 10.0, 1e6,
                     StrategyConfig(premarket_gap_direction=GapDirection.ANY))


def test_an_undefined_gate_input_reads_as_no_pass():
    reading = gate_reading(None, None, 10.0, 1e6, StrategyConfig())
    assert (reading.gap_pct, reading.volume_ratio, reading.both_pass) == (None, None, False)


# ---- universe hygiene (section C) --------------------------------------------------------


def _reference(*rows):
    return [U.ReferenceRow(ticker, cik, figi, None, True) for ticker, cik, figi in rows]


def test_the_non_common_rule_removes_preferred_shares():
    rows = _reference(("AGNC", "1", "BBG1"), ("AGNCL", "1", None), ("AGNCO", "1", None))
    assert U.non_common_tickers(rows) == frozenset({"AGNCL", "AGNCO"})


def test_the_non_common_rule_keeps_a_share_class_that_has_a_figi():
    rows = _reference(("GOOG", "1", "BBG1"), ("GOOGL", "1", "BBG2"))
    assert U.non_common_tickers(rows) == frozenset()


def test_the_non_common_rule_keeps_a_figi_less_common_with_no_shorter_sibling():
    rows = _reference(("ACN", "9", None), ("ACGL", "8", None))
    assert U.non_common_tickers(rows) == frozenset()


def test_a_split_session_is_removed_for_that_symbol_only():
    base = U.BaseUniverse(SESSION, frozenset({"AAA", "BBB"}), frozenset(), frozenset(), "x")
    splits = {"AAA": frozenset({SESSION})}
    assert U.eligible_symbols(base, ["AAA", "BBB"], splits, SESSION) == ("BBB",)
    other = date(2026, 6, 16)
    assert U.eligible_symbols(base, ["AAA", "BBB"], splits, other) == ("AAA", "BBB")


def test_universe_in_force_never_uses_a_later_cache():
    caches = [U.BaseUniverse(date(2026, 4, 1), frozenset({"A"}), frozenset(), frozenset(), "a"),
              U.BaseUniverse(date(2026, 7, 1), frozenset({"B"}), frozenset(), frozenset(), "b")]
    assert U.universe_for(caches, date(2026, 6, 30)).as_of == date(2026, 4, 1)
    assert U.universe_for(caches, date(2026, 7, 1)).as_of == date(2026, 7, 1)
    assert U.universe_for(caches, date(2026, 1, 1)) is None


# ---- comparison arithmetic ---------------------------------------------------------------


def test_turnover_counts_symbols_new_to_the_session():
    selections = [(date(2026, 6, 1), ("A", "B", "C", "D")),
                  (date(2026, 6, 2), ("A", "B", "X", "Y")),
                  (date(2026, 6, 3), ("A", "B", "X", "Y"))]
    assert C.turnover(selections) == pytest.approx(0.25)


def test_repeat_ratio_separates_a_fixed_list_from_a_rotating_one():
    described = {}
    fixed, rotating = [], []
    for index in range(10):
        day = date(2026, 6, 1) + __import__("datetime").timedelta(days=index)
        fixed.append((day, ("A", "B")))
        rotating.append((day, (f"R{index}", f"Q{index}")))
    summary_fixed = C.summarize_arm("fixed", fixed, described, {})
    summary_rotating = C.summarize_arm("rotating", rotating, described, {})
    assert summary_fixed.unique_symbols == 2
    assert summary_fixed.repeat_ratio == pytest.approx(0.9)
    assert summary_rotating.unique_symbols == 20
    assert summary_rotating.repeat_ratio == pytest.approx(0.0)


# ---- GPT handoff (section M) -------------------------------------------------------------


class _Repository:
    def __init__(self, candidates):
        self._candidates = candidates

    def get_top8(self, run_id):
        return self._candidates


@dataclass
class _Run:
    id: int
    trading_date: date
    status: str
    score_version: str


@dataclass
class _Candidate:
    scanner_run_id: int
    symbol: str
    rank: int
    score: float
    score_components_json: dict


def test_the_existing_prompt_renders_from_mover_scanner_rows_unchanged():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(30, config)
    top = scan_session(SESSION, symbols, panel, daily, config).top
    rows = H.candidate_rows(top, datetime(2026, 6, 15, 13, 15, tzinfo=timezone.utc))
    assert len(rows) == config.top_count
    assert all(row.is_top8 for row in rows)
    run = _Run(id=7, trading_date=SESSION, status="COMPLETED",
               score_version=config.score_version)
    candidates = [_Candidate(7, row.symbol, row.rank, row.score, row.score_components)
                  for row in rows]
    prompt = ResearchPromptService(_Repository(candidates)).generate_top_for_run(run)
    assert f"prompt_version: {TOP8_PROMPT_VERSION}" in prompt
    assert f"Include exactly the same {len(rows)} symbols" in prompt
    for row in rows:
        assert f'"symbol": "{row.symbol}"' in prompt
    assert "pm_dollar_volume" in prompt and "pm_rvol" in prompt


def test_a_short_output_renders_a_prompt_for_exactly_that_many_symbols():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(3, config)
    top = scan_session(SESSION, symbols, panel, daily, config).top
    rows = H.candidate_rows(top, datetime(2026, 6, 15, 13, 15, tzinfo=timezone.utc))
    run = _Run(id=1, trading_date=SESSION, status="COMPLETED",
               score_version=config.score_version)
    candidates = [_Candidate(1, row.symbol, row.rank, row.score, row.score_components)
                  for row in rows]
    prompt = ResearchPromptService(_Repository(candidates)).generate_top_for_run(run)
    assert "Include exactly the same 3 symbols" in prompt


def test_the_handoff_payload_names_the_rules_that_chose_the_symbols():
    config = MoverScannerConfig()
    symbols, panel, daily = _many_symbol_panel(30, config)
    top = scan_session(SESSION, symbols, panel, daily, config).top
    payload = H.handoff_payload(SESSION, top, config,
                                datetime(2026, 6, 15, 13, 15, tzinfo=timezone.utc))
    assert payload["rules_checksum"] == config.checksum
    assert payload["candidate_count"] == len(top)
    assert payload["top_count_maximum"] == config.top_count


# ---- the declaration -------------------------------------------------------------------


def test_the_checksum_moves_when_a_rule_moves():
    base = MoverScannerConfig()
    assert replace(base, scan_cut_minute=540).checksum != base.checksum
    assert MoverScannerConfig().checksum == base.checksum


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        MoverScannerConfig(opportunity_weights={"pm_dollar_volume": 0.5, "pm_rvol": 0.1,
                                                "gap_quality": 0.1, "pm_momentum": 0.1,
                                                "tradability": 0.1})


def test_the_cut_must_sit_inside_the_premarket():
    with pytest.raises(ValueError):
        MoverScannerConfig(scan_cut_minute=600)
    with pytest.raises(ValueError):
        MoverScannerConfig(scan_cut_minute=200)


def test_the_scan_time_is_configurable_and_changes_what_is_seen():
    early = MoverScannerConfig(scan_cut_minute=480)     # 08:00 ET
    bars = [(300, 10.0, 10.0, 10.0, 10.0, 50_000.0),
            (301, 10.0, 10.0, 10.0, 10.0, 50_000.0),
            (302, 10.0, 10.0, 10.0, 10.0, 50_000.0),
            (540, 10.0, 12.0, 10.0, 12.0, 90_000.0)]
    cache = FakeCache({("AAA", SESSION): bars}, [SESSION], ["AAA"])
    assert P.build_panel(cache, early).field("pm_bars", 0, 0) == 3
    assert P.build_panel(cache, MoverScannerConfig()).field("pm_bars", 0, 0) == 4
