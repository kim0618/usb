"""The B-E0 screening run: preflight, replay the window, measure, gate, write.

This module arranges and records. It never judges. Entries, exits and every threshold come
from the research layer and the session engine; the verdict comes from the contract's ranked
decision order. If something here looks like a decision about a trade, it is a defect.

Two invariants are enforced rather than assumed:

* a session is only ever handed the symbols that session's scope set contains, which is what
  keeps the universe's forward-looking union from becoming survivorship selection;
* in STRICT mode a failed preflight ends the run with DATASET_NOT_READY and no metrics, because
  a screening that silently skipped what it could not load reports a study that never happened.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Protocol

from app.backtest.engine.identity import source_provenance
from app.backtest.strategy_b.costs import CostModel, FillScenario
from app.backtest.strategy_b.engine import SessionReport, SymbolSession, run_session
from app.backtest.strategy_b.portfolio import Portfolio, Trade
from app.backtest.strategy_b_e0 import artifacts, identity as run_identity_module, lift, metrics
from app.backtest.strategy_b_e0.contract import Contract, RunSpec
from app.backtest.strategy_b_e0.gate import (
    GateResult, Interval, LiftOutcome, SampleCounts, Verdict, evaluate as evaluate_gate)
from app.backtest.strategy_b_e0.identity import RunMode
from app.backtest.strategy_b_e0.preflight import DatasetFacts, PreflightReport, run_preflight
from app.backtest.strategy_b_e0.universe import RunUniverse
from app.strategy_b.config import StrategyBConfig
# The EOD clamp is F0 9절's rule and exits.py owns it. Importing it keeps one definition of
# "when B is flat"; recomputing it here would let the two drift apart silently.
from app.strategy_b.exits import _eod_moment
from app.strategy_b.fsm import Candidate
from app.strategy_b.session import SessionBoundaries


class SessionSource(Protocol):
    """Where one session's inputs come from. A Protocol so the runner is testable with fixtures."""

    def boundaries(self, session: date) -> SessionBoundaries:
        ...

    def symbol_sessions(self, session: date, symbols: Sequence[str]) -> Sequence[SymbolSession]:
        """Loaded tapes for exactly the symbols asked for, in any order.

        The runner passes only symbols in that session's scope set, and the source must not add
        any of its own.
        """

    def dataset_identity(self) -> str:
        ...

    def dataset_digest(self) -> str:
        ...


@dataclass(slots=True)
class RunOutcome:
    run_id: str
    verdict: Verdict
    gate: GateResult
    preflight: PreflightReport
    sessions: list[SessionReport] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    candidates: list[tuple[Candidate, date]] = field(default_factory=list)
    anchors: list[lift.Anchor] = field(default_factory=list)
    lift_result: lift.LiftResult | None = None
    root: Path | None = None
    source_log: list[dict] = field(default_factory=list)
    starting_equity: float = 0.0


def cost_model(contract: Contract, spec: RunSpec) -> CostModel:
    level = contract.cost_level(spec.cost_level)
    return CostModel(fee_bps_per_side=level.commission_bps_per_side,
                     slippage_bps_per_side=level.execution_cost_bps_per_side)


class RunRefused(RuntimeError):
    """The run did not start: its identity, its dataset or its output location is not the one
    the contract allows. Nothing was replayed and nothing was written."""


def execute(contract: Contract, spec: RunSpec, universe: RunUniverse, source: SessionSource,
            facts: DatasetFacts, *, out_root: Path, config: StrategyBConfig | None = None,
            mode: RunMode = RunMode("STRICT"), backend_root: Path | None = None,
            repo_root: Path | None = None, contract_path: Path | None = None,
            sessions: Sequence[date] | None = None,
            expected_warnings: Sequence[str] | None = None,
            manifest_extra: Mapping[str, object] | None = None,
            code_identity: Mapping[str, object] | None = None) -> RunOutcome:
    """Run one pre-registered configuration end to end.

    ``sessions`` is only for SMOKE: an explicit, ordered subset of the universe's sessions. An
    authoritative (STRICT) run always replays every session. ``expected_warnings`` is the
    warning list the contract registered; a preflight that raises any other warning refuses the
    run, because a warning nobody reviewed is not a registered limitation.
    """
    config = config or StrategyBConfig()
    backend_root = backend_root or Path(__file__).resolve().parents[3]
    repo_root = repo_root or backend_root.parent
    replayed = _replayed_sessions(universe, mode, sessions)

    planned = {session: universe.symbols_for(session) for session in universe.sessions}
    report = run_preflight(contract, universe, facts, planned_symbols_by_session=planned,
                           contract_path=contract_path, config=config)

    code = run_identity_module.engine_code_digest(backend_root)
    run_id_value = run_identity_module.build(
        contract, spec, universe, dataset_identity=source.dataset_identity(),
        dataset_digest=source.dataset_digest(), code=code, mode=mode,
        config_fingerprint=config.fingerprint(),
        sessions=replayed if mode.name == "SMOKE" else None,
        working_tree_code_digest=None if code_identity is None else str(code_identity["code_digest"]))

    if mode.name == "PREFLIGHT_ONLY":
        verdict = evaluate_gate(contract, sample=SampleCounts(0, 0), mean_net_r=None, lift=None,
                                dataset_ready=report.ready)
        return RunOutcome(run_id_value.run_id, verdict.verdict, verdict, report)

    if report.ready and expected_warnings is not None:
        new = sorted(set(report.warnings) - set(expected_warnings))
        if new:
            raise RunRefused(f"the preflight raised warnings the contract did not register: {new}")
    artifacts.refuse_existing(out_root, run_id_value.run_id)

    if not report.ready:
        verdict = evaluate_gate(contract, sample=SampleCounts(0, 0), mean_net_r=None, lift=None,
                                dataset_ready=report.ready)
        outcome = RunOutcome(run_id_value.run_id, verdict.verdict, verdict, report)
        _write(contract, spec, universe, outcome, run_id_value, out_root, repo_root, code,
               mode=mode, replayed=replayed, config=config, manifest_extra=manifest_extra,
               code_identity=code_identity)
        return outcome

    outcome = RunOutcome(run_id_value.run_id, Verdict.ERROR,
                         evaluate_gate(contract, sample=SampleCounts(0, 0), mean_net_r=None,
                                       lift=None, error="not yet evaluated"), report)
    portfolio = Portfolio(equity=float(contract.initial_capital_usd),
                          costs=cost_model(contract, spec), risk=config.risk)
    outcome.starting_equity = portfolio.equity
    scenario = FillScenario(spec.fill_scenario)

    for session in replayed:
        symbols = universe.symbols_for(session)
        loaded = list(source.symbol_sessions(session, symbols))
        offenders = universe.membership_violations(session, [item.symbol for item in loaded])
        if offenders:
            raise ValueError(
                f"{session}: the source returned {offenders} which are not in that session's "
                "scope set; feeding the window-wide union into a session is look-ahead")
        boundaries = source.boundaries(session)
        session_report = run_session(loaded, config=config, portfolio=portfolio,
                                     scenario=scenario, boundaries=boundaries)
        outcome.sessions.append(session_report)
        outcome.trades.extend(session_report.trades)
        outcome.candidates.extend((candidate, session)
                                  for candidate in session_report.finished_candidates)
        if loaded:
            tapes = {item.symbol: item.tape for item in loaded}
            outcome.anchors.extend(lift.collect(
                session_report.finished_candidates, tapes.get, contract=contract,
                eod_exit_at=_eod_moment(loaded[0].tape, config.exit)))

    # What the source withheld and loaded per session (a LocalSessionSource keeps both), so the
    # run shows, beside its trades, every pair it did not replay and why.
    outcome.source_log = ([{"record": "session_load", **row} for row in getattr(source, "dynamic", ())]
                          + [{"record": "withheld", **row} for row in getattr(source, "withheld", ())])
    outcome.lift_result = lift.evaluate(outcome.anchors, replayed, contract)
    outcome.gate = _gate(contract, outcome, replayed)
    outcome.verdict = outcome.gate.verdict
    _write(contract, spec, universe, outcome, run_id_value, out_root, repo_root, code,
           mode=mode, replayed=replayed, config=config, manifest_extra=manifest_extra,
           code_identity=code_identity)
    return outcome


def _replayed_sessions(universe: RunUniverse, mode: RunMode,
                       sessions: Sequence[date] | None) -> tuple[date, ...]:
    if mode.name != "SMOKE":
        if sessions is not None:
            raise RunRefused(f"{mode.name} replays every session; a session subset is SMOKE only")
        return tuple(universe.sessions)
    if not sessions:
        raise RunRefused("a SMOKE run names the sessions it replays")
    chosen = tuple(sessions)
    unknown = [day for day in chosen if day not in set(universe.sessions)]
    if unknown or list(chosen) != sorted(set(chosen)):
        raise RunRefused(f"smoke sessions must be distinct universe sessions in order: {chosen}")
    return chosen


def _gate(contract: Contract, outcome: RunOutcome, sessions: Sequence[date]) -> GateResult:
    closed = [trade for trade in outcome.trades if trade.is_closed]
    by_session: dict[date, list[float]] = {}
    for trade in closed:
        by_session.setdefault(trade.session_date, []).append(trade.realized_r)
    sample = SampleCounts(closed_trades=len(closed), sessions_with_trades=len(by_session))

    interval: Interval | None = None
    if closed:
        interval = metrics.mean_interval(metrics.series(by_session, sessions),
                                         contract.statistics)
    lift_outcome = outcome.lift_result.outcome if outcome.lift_result else None
    return evaluate_gate(contract, sample=sample, mean_net_r=interval, lift=lift_outcome)


def _write(contract: Contract, spec: RunSpec, universe: RunUniverse, outcome: RunOutcome,
           run_id_value, out_root: Path, repo_root: Path, code: str, *, mode: RunMode,
           replayed: Sequence[date], config: StrategyBConfig,
           manifest_extra: Mapping[str, object] | None,
           code_identity: Mapping[str, object] | None = None) -> None:
    """Every artifact goes to a staging directory, then COMPLETE.json, then one rename.

    A crash leaves only the staging directory, which is never mistaken for a run: the run
    directory appears whole, with its marker, or not at all.
    """
    staging = artifacts.staging_root(out_root, outcome.run_id)
    writer = artifacts.RunWriter(staging)
    provenance = source_provenance(repo_root, [repo_root / "backend/app/backtest/strategy_b_e0",
                                               repo_root / "backend/app/backtest/strategy_b",
                                               repo_root / "backend/app/strategy_b"])

    if code_identity is not None:
        writer.write_json("code_identity.json", code_identity)
    writer.write_json("identity.json", run_identity_module.as_document(
        run_id_value, provenance, {"engine_code_digest": code, "run_mode": mode.name,
                                   "working_tree_code_digest": None if code_identity is None
                                   else code_identity["code_digest"],
                                   "verdict_eligible": mode.authoritative
                                   and spec.verdict_input}))
    writer.write_json("run_manifest.json", {
        "run_mode": mode.name,
        "verdict_eligible": mode.authoritative and spec.verdict_input,
        "contract_state": contract.raw.get("contract_state", "DRAFT_PENDING_APPROVAL"),
        "execution_model": contract.raw["execution_model"]["primary"],
        "signal_ttl_minutes": contract.raw["signal_ttl"]["signal_ttl_minutes"],
        "config_fingerprint": config.fingerprint(),
        "bootstrap_seed": contract.statistics.seed,
        "sessions_replayed": [day.isoformat() for day in replayed],
        "dataset_warnings": list(outcome.preflight.warnings),
        "registered_limitations": list(contract.raw["limitations_to_publish"]),
        **dict(manifest_extra or {}),
        "run_label": spec.label,
        "contract_id": contract.contract_id,
        "contract_canonical_checksum": contract.canonical_checksum,
        "rules_canonical_checksum": contract.rules_checksum,
        "universe_artifact": universe.path.name,
        "universe_sha256": universe.sha256,
        "universe_symbol_count": universe.symbol_count,
        "universe_exclusions": dict(sorted(universe.exclusions.items())),
        "scope_start": contract.scope_start,
        "scope_end": contract.scope_end,
        "sessions": len(universe.sessions),
        "fill_scenario": spec.fill_scenario,
        "cost_level": spec.cost_level,
        "initial_capital_usd": contract.initial_capital_usd,
        "fx_model": contract.fx_model,
        "verdict_input": spec.verdict_input,
        "preflight": outcome.preflight.as_dict(),
    })
    writer.write_jsonl("trades.jsonl", [artifacts.trade_row(trade) for trade in outcome.trades])
    writer.write_jsonl("candidates.jsonl",
                       [artifacts.candidate_row(candidate, session)
                        for candidate, session in outcome.candidates])
    writer.write_jsonl("sessions.jsonl",
                       [artifacts.session_row(report) for report in outcome.sessions])
    writer.write_json("metrics.json", _metrics_document(outcome))
    writer.write_json("gate_result.json", outcome.gate.as_dict())
    if outcome.lift_result is not None:
        writer.write_json("lift.json", outcome.lift_result.as_dict())
    writer.write_jsonl("daily_metrics.jsonl", _daily_rows(outcome, replayed))
    if outcome.source_log:
        writer.write_jsonl("source_log.jsonl", outcome.source_log)

    missing = writer.missing(contract.required_artifacts)
    if missing:
        raise RuntimeError(f"{outcome.run_id} is missing required artifacts {missing}")
    outcome.root = artifacts.finalize(staging, out_root, outcome.run_id,
                                      run_identity=run_id_value.digest)


def _daily_rows(outcome: RunOutcome, replayed: Sequence[date]) -> list[dict[str, object]]:
    """One row per replayed session: trades closed, net PnL and realised equity at its end.

    Equity is realised-only (Portfolio's own definition), carried forward across sessions, so
    the row sequence is the run's equity curve at session granularity.
    """
    by_day: dict[date, list[Trade]] = {}
    for trade in outcome.trades:
        by_day.setdefault(trade.session_date, []).append(trade)
    rows, equity = [], outcome.starting_equity
    for day in replayed:
        closed = [t for t in by_day.get(day, []) if t.is_closed]
        pnl = sum(t.net_pnl for t in closed)
        equity = equity + pnl
        rows.append({"session_date": day.isoformat(), "closed_trades": len(closed),
                     "net_pnl": pnl, "net_r": sum(t.realized_r for t in closed),
                     "equity_end": equity})
    return rows


def _metrics_document(outcome: RunOutcome) -> dict[str, object]:
    closed = [trade for trade in outcome.trades if trade.is_closed]
    values = [trade.realized_r for trade in closed]
    wins = [value for value in values if value > 0]
    refusals: dict[str, int] = {}
    for report in outcome.sessions:
        for reason, count in report.refusals.items():
            refusals[reason] = refusals.get(reason, 0) + count
    return {
        "closed_trades": len(closed),
        "open_trades_at_end": len(outcome.trades) - len(closed),
        "sessions": len(outcome.sessions),
        "sessions_with_trades": len({trade.session_date for trade in closed}),
        "mean_net_r": (sum(values) / len(values)) if values else None,
        "win_rate": (len(wins) / len(values)) if values else None,
        "total_net_pnl": sum(trade.net_pnl for trade in closed),
        "total_fees": sum(trade.fees for trade in closed),
        "candidates_finished": len(outcome.candidates),
        "refusals": dict(sorted(refusals.items())),
        "entries": sum(report.entries for report in outcome.sessions),
        "prefiltered_minutes": sum(report.prefiltered_minutes for report in outcome.sessions),
    }
