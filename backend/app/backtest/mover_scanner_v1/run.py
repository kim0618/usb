"""Run the mover scanner over a window and compare it with the deployed scanner. Research only.

Nothing here opens the paper database, reaches a provider, or writes outside the report
directory. The deployed arm is read from the frozen ``historical-daily-top8-v1`` artifact and
its stored checksum is verified before it is used, so the comparison cannot silently drift onto
a recomputed baseline.

The window is bounded by what the data can answer rather than by preference. Premarket relative
volume needs a 20-session premarket baseline, and the broad minute tape starts 2026-04-20, so
the first 20 sessions of the tape can be a baseline but not a scan. The frozen arm ends
2026-09-15. The intersection is the comparison window, and it is reported as such.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.backtest.collector.range import sessions_between
from app.backtest.mover_scanner_v1 import compare as C
from app.backtest.mover_scanner_v1 import daily as D
from app.backtest.mover_scanner_v1 import handoff as H
from app.backtest.mover_scanner_v1 import premarket as P
from app.backtest.mover_scanner_v1 import universe as U
from app.backtest.mover_scanner_v1.config import MoverScannerConfig
from app.backtest.mover_scanner_v1.scan import Rejection, SessionScan, scan_session
from app.backtest.strategy_b_e0.session_cache import SessionCache
from app.market.calendar import MarketCalendar
from app.strategy.config import StrategyConfig

CACHE_ROOT = "data/runtime/strategy_b_e0/cache"
CURRENT_ARM_PATH = "data/runtime/research_reports/historical_top8/year_b_top8.json"
#: Frozen 2026-09-23 by the historical TOP8 reconstruction; the comparison refuses another value.
CURRENT_ARM_CHECKSUM = "671f42438950690afd9e77a537604eaa2e1916ac4564357020dcef04ffcb2916"
REPORT_DIR = "data/runtime/research_reports/mover_scanner_v1"


class StudyHardFail(RuntimeError):
    """A precondition is not met; the run stops rather than reporting a degraded answer."""


@dataclass(frozen=True)
class SelectedCache:
    directory: Path
    digest: str
    sessions: int
    symbols: int
    rows: int


def select_cache(repo: Path, explicit: Path | None = None) -> SelectedCache:
    """The B-E0 minute cache to read: the complete one, named explicitly or discovered.

    A cache with uncovered symbol-sessions would make a quiet session and an unfetched one
    indistinguishable in the relative-volume baseline, so only a cache with none is accepted.
    """
    candidates = [explicit] if explicit else sorted((repo / CACHE_ROOT).glob("*/"))
    complete: list[SelectedCache] = []
    for directory in candidates:
        meta_path = directory / "cache_meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("uncovered_sessions"):
            continue
        complete.append(SelectedCache(directory, meta["cache_digest"], len(meta["sessions"]),
                                      len(meta["symbols"]), int(meta["rows"])))
    if not complete:
        raise StudyHardFail(f"no minute cache under {CACHE_ROOT} is free of uncovered sessions")
    complete.sort(key=lambda item: (-item.sessions, -item.rows))
    return complete[0]


def load_current_arm(repo: Path) -> tuple[list[dict], dict]:
    """The frozen deployed-scanner TOP8 rows, with their checksum verified."""
    path = repo / CURRENT_ARM_PATH
    if not path.is_file():
        raise StudyHardFail(f"the frozen current-scanner artifact is missing at {CURRENT_ARM_PATH}")
    body = json.loads(path.read_text(encoding="utf-8"))
    stored = body.get("dataset_checksum")
    if stored != CURRENT_ARM_CHECKSUM:
        raise StudyHardFail(
            f"current-scanner artifact checksum {stored} is not the frozen "
            f"{CURRENT_ARM_CHECKSUM}; the comparison baseline must not move")
    return body["rows"], body


def market_caps_at(repo: Path, as_of: date) -> dict[str, float]:
    """Dated market caps in force at ``as_of``; a symbol with only later caches is unknown."""
    from app.dev.build_historical_top8 import load_market_caps
    out: dict[str, float] = {}
    for symbol, rows in load_market_caps(repo).items():
        prior = [row for row in rows if row[0] <= as_of]
        if prior:
            out[symbol] = prior[-1][1]
    return out


@dataclass(frozen=True)
class StudyResult:
    config: MoverScannerConfig
    cache: SelectedCache
    #: Tape sessions a scan could be taken on; only ``comparison_sessions`` were scanned.
    scan_sessions: tuple[date, ...]
    comparison_sessions: tuple[date, ...]
    scans: tuple[SessionScan, ...]
    new_arm: C.ArmSummary
    current_arm: C.ArmSummary
    pool: Mapping[str, Any]
    universe_facts: Mapping[str, Any]
    current_arm_window: Mapping[str, Any]
    #: Kept so a later stage can rank a pick inside the same cross-section this run used,
    #: rather than recomputing a second distribution. ``report()`` does not read them.
    addv_percentiles: Mapping[date, Mapping[str, float]] = field(default_factory=dict)
    market_caps: Mapping[str, float] = field(default_factory=dict)

    def rows(self) -> list[dict]:
        scan_time = f"{self.config.scan_cut_minute // 60:02d}:" \
                    f"{self.config.scan_cut_minute % 60:02d} ET"
        return [item.row(scan_time) for scan in self.scans for item in scan.top]

    def pool_rows(self) -> list[dict]:
        scan_time = f"{self.config.scan_cut_minute // 60:02d}:" \
                    f"{self.config.scan_cut_minute % 60:02d} ET"
        return [item.row(scan_time) for scan in self.scans for item in scan.pool]

    def report(self) -> dict[str, Any]:
        rejections: dict[str, int] = {}
        for scan in self.scans:
            for reason, count in scan.rejections.items():
                rejections[str(reason)] = rejections.get(str(reason), 0) + count
        dominance_keys = sorted(self.config.opportunity_weights)
        dominance = {key: float(sum(scan.dominance.get(key, 0.0) for scan in self.scans)
                                / max(1, len(self.scans))) for key in dominance_keys}
        return {
            "contract_version": self.config.contract_version,
            "rules_checksum": self.config.checksum,
            "rules": self.config.declaration(),
            "network_calls": 0,
            "replays": 0,
            "pnl_computed": False,
            "production_changes": 0,
            "minute_cache": {"directory": str(self.cache.directory),
                             "cache_digest": self.cache.digest,
                             "sessions": self.cache.sessions,
                             "symbols": self.cache.symbols, "rows": self.cache.rows},
            "scannable_sessions": {
                "count": len(self.scan_sessions),
                "first": self.scan_sessions[0].isoformat() if self.scan_sessions else None,
                "last": self.scan_sessions[-1].isoformat() if self.scan_sessions else None,
                "rule": "tape sessions with a full premarket relative-volume baseline behind them",
            },
            "scanned_sessions": [day.isoformat() for day in self.comparison_sessions],
            "universe": dict(self.universe_facts),
            "current_arm_source": dict(self.current_arm_window),
            "pool_and_output": dict(self.pool),
            "component_dominance_mean": dominance,
            "rejections_total": dict(sorted(rejections.items(), key=lambda kv: -kv[1])),
            "arms": {"CURRENT_SCANNER": self.current_arm.as_dict(),
                     "NEW_MOVER_SCANNER": self.new_arm.as_dict()},
            "entry_gate_measured": {
                "gap_min_pct": float(StrategyConfig().premarket_gap_min_pct),
                "gap_max_pct": float(StrategyConfig().premarket_gap_max_pct),
                "volume_ratio_min": float(StrategyConfig().premarket_volume_ratio_min),
                "direction": str(StrategyConfig().premarket_gap_direction),
                "window": "04:00-09:30 ET, the gate's own window, measurement only",
            },
        }


def run_study(repo: Path, *, config: MoverScannerConfig | None = None,
              cache_directory: Path | None = None, limit_sessions: int | None = None,
              progress=None) -> StudyResult:
    config = config or MoverScannerConfig()
    strategy = StrategyConfig()
    calendar = MarketCalendar()
    selected = select_cache(repo, cache_directory)
    cache = SessionCache(selected.directory)

    baseline = config.premarket_rvol_baseline_sessions
    if len(cache.sessions) <= baseline:
        raise StudyHardFail("the tape is shorter than the relative-volume baseline")
    scan_sessions = list(cache.sessions[baseline:])
    current_rows, current_body = load_current_arm(repo)
    current_sessions = {str(row["session_date"]) for row in current_rows}
    comparison = [day for day in scan_sessions if day.isoformat() in current_sessions]
    if not comparison:
        raise StudyHardFail("the tape and the frozen current-scanner artifact do not overlap")
    if limit_sessions is not None:
        comparison = comparison[-limit_sessions:]

    if progress:
        progress("premarket panel", 0, len(cache.sessions))
    premarket = P.build_panel(cache, config, progress=(
        None if progress is None else lambda done, total: progress("premarket panel", done, total)))

    grid_start = comparison[0] - timedelta(days=D.DAILY_LOOKBACK_DAYS + 10)
    grid = [item.session_date for item in sessions_between(calendar, grid_start, comparison[-1])]
    if progress:
        progress("daily panel", 0, len(grid))
    daily = D.load_panel(repo, grid)

    universes = U.load_universes(repo, exclude_non_common=config.exclude_non_common_by_cik_prefix)
    splits = U.split_sessions(repo)
    caps = market_caps_at(repo, comparison[0])

    current_picks = C.current_selections(current_rows, comparison)
    current_by_session = dict(current_picks)

    scans: list[SessionScan] = []
    described: dict[tuple[date, str], C.Described] = {}
    percentiles: dict[date, Mapping[str, float]] = {}
    split_removals = 0
    universe_sizes: list[int] = []
    caches_in_force: set[str] = set()
    for position, session in enumerate(comparison, start=1):
        base = U.universe_for(universes, session)
        if base is None:
            raise StudyHardFail(f"no reference cache is in force on {session.isoformat()}")
        unfiltered = tuple(sorted(symbol for symbol in cache.symbols if symbol in base.symbols))
        symbols = U.eligible_symbols(
            base, cache.symbols, splits, session,
            exclude_split_sessions=config.exclude_split_execution_sessions)
        split_removals += len(unfiltered) - len(symbols)
        universe_sizes.append(len(symbols))
        caches_in_force.add(base.as_of.isoformat())
        scan = scan_session(session, symbols, premarket, daily, config, strategy)
        scans.append(scan)
        # The liquidity percentile is a property of the session's own cross-section, so both
        # arms' picks are ranked inside one distribution: the scanned universe plus any
        # current-arm pick that the universe rule left out.
        current_symbols = current_by_session.get(session, ())
        percentiles[session] = C.addv_percentiles(
            tuple(sorted(set(symbols) | set(current_symbols))), session, daily, config)
        for symbol in {item.symbol for item in scan.top} | set(current_symbols):
            described[(session, symbol)] = C.describe_features(
                symbol, session, premarket, daily, config, strategy)
        if progress:
            progress("scan", position, len(comparison))

    new_picks = C.new_selections(scans)

    return StudyResult(
        config=config, cache=selected, scan_sessions=tuple(scan_sessions),
        comparison_sessions=tuple(comparison), scans=tuple(scans),
        new_arm=C.summarize_arm("NEW_MOVER_SCANNER", new_picks, described, percentiles, caps),
        current_arm=C.summarize_arm("CURRENT_SCANNER", current_picks, described, percentiles, caps),
        pool=C.pool_summary(scans),
        universe_facts={
            "reference_caches_used": sorted(caches_in_force),
            "average_scanned_universe": None if not universe_sizes else
            sum(universe_sizes) / len(universe_sizes),
            "non_common_rule": U.NON_COMMON_RULE,
            "non_common_excluded_latest": sorted(
                U.universe_for(universes, comparison[-1]).excluded_non_common),
            "split_session_removals": split_removals,
            "market_cap_known_symbols": len(caps),
        },
        current_arm_window={
            "path": CURRENT_ARM_PATH,
            "dataset_checksum": current_body.get("dataset_checksum"),
            "contract_version": current_body.get("contract_version"),
            "window": f"{current_body.get('window_start')}..{current_body.get('window_end')}",
            "universe_rule": current_body.get("universe_rule"),
            "trade_value_proxy": current_body.get("trade_value_proxy"),
        },
        addv_percentiles=percentiles, market_caps=caps)


def write_artifacts(repo: Path, result: StudyResult, handoff_session: date | None = None,
                    ) -> dict[str, str]:
    """Write the report, the output rows, the pool rows and one GPT handoff sample."""
    out = repo / REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}
    payloads: dict[str, Any] = {
        "mover_scanner_v1_report.json": result.report(),
        "mover_top8_rows.json": {"contract_version": result.config.contract_version,
                                 "rules_checksum": result.config.checksum,
                                 "rows": result.rows()},
        "mover_pool_rows.json": {"contract_version": result.config.contract_version,
                                 "rules_checksum": result.config.checksum,
                                 "rows": result.pool_rows()},
    }
    session = handoff_session or (result.comparison_sessions[-1]
                                  if result.comparison_sessions else None)
    if session is not None:
        chosen = next(scan for scan in result.scans if scan.session_date == session)
        payloads["gpt_handoff_sample.json"] = H.handoff_payload(
            session, chosen.top, result.config, datetime.now(timezone.utc))
    for name, payload in payloads.items():
        body = json.dumps(payload, indent=1, sort_keys=True, default=str) + "\n"
        (out / name).write_text(body, encoding="utf-8")
        written[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return written
