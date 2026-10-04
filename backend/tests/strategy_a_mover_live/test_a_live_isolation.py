"""A's failure boundary inside E's worker, and the refusal of a stale E staging artifact.

Two operational risks are covered, and only those two. No strategy, scanner, risk, entry, exit,
GPT or baseline rule is exercised differently here than it is elsewhere in this suite.

**A attach failure isolation.** A's cut runs inside E's acquisition worker because a second
process cannot have either Kiwoom lane. ``run_cut`` was already behind a boundary; ``attach``
was not, and ``attach`` is where A reads its data authority, so a data defect there unwound E's
worker - which is official paper trading. The tests assert the two directions that matter: A
fails closed with a named reason and zero candidates, and every E expression around it keeps
the value it has when A is off.

**Stale E staging artifact.** A read E's universe as "the newest artifact dated on or before
the session". E's own worker refuses any artifact whose ``target_session`` is not the session it
is running. The tests assert A now applies E's rule, that the filename is not the authority,
and that a refusal is recorded rather than silently substituted.
"""

from datetime import date, datetime, time, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.services.mover_scanner_source import DataUnavailable, MoverDataUnavailableError
from app.strategy_e_max_rt import finalizer as FZ
from app.strategy_e_max_rt import universe_build as UB
from app.strategy_a_mover_live import config as CFG
from app.strategy_a_mover_live import contract as LC
from app.strategy_a_mover_live import features as FEAT
from app.strategy_a_mover_live import integration as INT
from app.strategy_a_mover_live import isolation as ISO
from app.strategy_a_mover_live import schedule as SCHED
from app.strategy_a_mover_live import universe as UNI
from tests.strategy_a_mover_live.fixtures import ET, SESSION, VirtualClock, cache

ON = {CFG.ENV_FLAG: "true"}
OFF: dict[str, str] = {}
A_CUT_AT = datetime.combine(SESSION, time(9, 15), tzinfo=ET)
LIVE = date(2026, 10, 5)


def attach(tmp_path, *, environ=ON, strict=False, caches=None, **kwargs):
    """``attach_isolated`` with the arguments E's worker passes, over an empty repo."""
    clock = VirtualClock(A_CUT_AT)
    return INT.attach_isolated(
        session=kwargs.pop("session", SESSION), repo=tmp_path,
        caches=caches if caches is not None else {"EEE": cache("EEE")},
        shard_minute=("EEE",), shard_tick=(), lane_minute=None, lane_tick=None, now=clock,
        exchanges={"EEE": "ND"}, log=lambda text: None, environ=environ, strict=strict,
        **kwargs)


def raising(error):
    def _raise(*_args, **_kwargs):
        raise error
    return _raise


# -- section J1, J6: A off and A attached leave E's own expressions alone ---------------------

def test_a_off_touches_nothing_and_adds_no_key_to_e_report(tmp_path):
    """Section J6. Disabled A must not even leave an audit file: that would be a change."""
    isolated = attach(tmp_path, environ=OFF)
    assert isolated.active is False and isolated.handle is None
    order = ["SPY", "AAA"]
    resolved = isolated.handle.rolling_order(order) if isolated.handle is not None else order
    deadline = (isolated.handle.rolling_until() if isolated.handle is not None
                else datetime.combine(SESSION, FZ.REFRESH_B_AT, tzinfo=ET))
    assert resolved is order                             # E's own list object, not a copy
    assert deadline.time() == FZ.REFRESH_B_AT
    assert list(tmp_path.rglob("*")) == []               # not one file, not one directory
    report: dict = {}
    if isolated.active:                                  # the worker's own guard
        report["a_mover_live"] = isolated.run_cut()
    assert report == {}


def test_a_attached_successfully_leaves_no_failure_record(tmp_path, monkeypatch):
    """Section J1. A success writes no refusal, and E's cache dictionary is untouched."""
    caches = {"EEE": cache("EEE")}
    monkeypatch.setattr(INT, "attach", _fake_attach(caches))
    isolated = attach(tmp_path, caches=caches)
    assert isolated.active is True and isolated.handle is not None and isolated.status is None
    assert ISO.read_status(tmp_path, SESSION) is None
    assert set(caches) == {"EEE"}


def _fake_attach(caches):
    def _attach(**kwargs):
        return INT.SharedCollectorIntegration(
            session=kwargs["session"], repo=Path(kwargs["repo"]), caches=caches,
            shard_minute=("EEE",), shard_tick=(), lane_minute=None, lane_tick=None,
            now=kwargs["now"],
            union=UNI.UnionUniverse(session=kwargs["session"], a_symbols=("EEE",),
                                    e_symbols=("EEE",), union=("EEE",),
                                    reference_as_of=date(2026, 7, 1), reference_checksum="x",
                                    reference_active_rows=1, pruned_no_daily_baseline=0,
                                    pruned_split_session=0, e_artifact=None),
            exchanges={"EEE": "ND"}, log=lambda text: None)
    return _attach


# -- section J2: a real data refusal in attach, fail closed ----------------------------------

def test_an_absent_reference_cache_fails_a_closed_and_names_the_reason(tmp_path):
    """Section J2. The real ``UNI.build`` over an empty repo: no monkeypatching."""
    isolated = attach(tmp_path)
    assert isolated.active is True                       # A was asked to run
    assert isolated.handle is None                       # and did not
    status = isolated.status
    assert status["status"] == "FAILED"
    assert status["phase"] == str(ISO.Phase.ATTACH)
    assert status["refusal"] == str(CFG.Refusal.DATA_UNAVAILABLE)
    assert status["reason"] == str(ISO.Reason.REFERENCE_UNAVAILABLE)
    assert status["expected_failure"] is True
    assert (status["candidates"], status["gpt_calls"], status["paper_injections"]) == (0, 0, 0)
    assert status["fallback"] == "NONE"
    assert status["e_run_continues"] is True
    assert isolated.run_cut() == status                  # the cut repeats the recorded refusal


def test_the_recorded_refusal_names_every_substitution_it_did_not_make(tmp_path):
    """Section C. A failed A must not be served by anything else, and says which things."""
    status = attach(tmp_path).status
    assert set(status["fallbacks_disabled"]) == {
        "LEGACY_QUANT_V0_SCANNER", "PREVIOUS_SESSION_CANDIDATES",
        "MASSIVE_CURRENT_SESSION_PREMARKET", "STALE_SNAPSHOT", "EMPTY_SUCCESS"}
    assert status["status"] != "OK"                      # never an empty success


# -- section J3, J4, J5: the named data and authority failures -------------------------------

def test_a_missing_grouped_daily_is_classified_from_the_real_error(tmp_path):
    """Section J3. The error object is the one ``live_daily_panel`` itself raises."""
    with pytest.raises(MoverDataUnavailableError) as raised:
        FEAT.live_daily_panel(tmp_path, LIVE, ["AAA"])
    assert raised.value.reason is DataUnavailable.NO_GROUPED_DAILY
    failure = ISO.classify(raised.value, phase=ISO.Phase.ATTACH)
    assert failure.refusal is CFG.Refusal.DATA_UNAVAILABLE
    assert failure.reason is ISO.Reason.NO_GROUPED_DAILY
    assert failure.expected is True and failure.traceback is None


def test_a_missing_grouped_daily_in_attach_isolates_exactly_as_the_reference_case(tmp_path,
                                                                                  monkeypatch):
    """Section J3. Same isolation, through the attach seam."""
    monkeypatch.setattr(INT, "attach", raising(
        UNI.UniverseUnavailable("grouped daily is missing for 2026-09-17",
                                reason="NO_GROUPED_DAILY")))
    status = attach(tmp_path).status
    assert status["refusal"] == str(CFG.Refusal.DATA_UNAVAILABLE)
    assert status["reason"] == "NO_GROUPED_DAILY"
    assert (status["candidates"], status["gpt_calls"], status["paper_injections"]) == (0, 0, 0)


def test_a_baseline_refusal_is_classified_as_baseline_unavailable(tmp_path, monkeypatch):
    """Section J4. Both the tape bootstrap refusal and the named data reason map to one word."""
    from app.strategy_a_mover_live import bootstrap as BOOT
    assert ISO.classify(BOOT.TapeUnavailable("no complete tape"),
                        phase=ISO.Phase.ATTACH).reason is ISO.Reason.BASELINE_UNAVAILABLE
    assert ISO.classify(MoverDataUnavailableError(
        DataUnavailable.PREMARKET_BASELINE_TOO_SHORT, "12 of 20"),
        phase=ISO.Phase.CUT).reason is ISO.Reason.BASELINE_UNAVAILABLE
    monkeypatch.setattr(INT, "attach", raising(BOOT.TapeUnavailable("no complete tape")))
    status = attach(tmp_path).status
    assert status["reason"] == str(ISO.Reason.BASELINE_UNAVAILABLE)
    assert status["refusal"] == str(CFG.Refusal.DATA_UNAVAILABLE)
    assert status["candidates"] == 0


def test_contract_drift_means_a_did_not_run(tmp_path, monkeypatch):
    """Section J5. A drifted parent is an authority failure, not a data one."""
    monkeypatch.setattr(LC.K, "verify",
                        lambda: (_ for _ in ()).throw(LC.K.ContractDrift("threshold moved")))
    with pytest.raises(LC.LiveContractDrift) as raised:
        LC.verify()
    failure = ISO.classify(raised.value, phase=ISO.Phase.ATTACH)
    assert failure.refusal is CFG.Refusal.CONTRACT_DRIFT
    assert failure.reason is ISO.Reason.CONTRACT_DRIFT
    monkeypatch.setattr(INT, "attach", raising(raised.value))
    status = attach(tmp_path).status
    assert status["refusal"] == str(CFG.Refusal.CONTRACT_DRIFT)
    assert status["candidates"] == 0 and status["gpt_calls"] == 0


def test_a_data_integrity_conflict_is_its_own_reason(tmp_path):
    from app.strategy_a_mover_live import raw_store as RAW
    from app.strategy_a_mover_live import snapshot as SNAP
    for error in (RAW.RawConflict("AAA 09:10: two values"), SNAP.SnapshotIncomplete("short")):
        failure = ISO.classify(error, phase=ISO.Phase.CUT)
        assert failure.reason is ISO.Reason.DATA_INTEGRITY_ERROR
        assert failure.expected is True


# -- section E: the boundary does not swallow programming bugs -------------------------------

def test_an_unexpected_error_is_recorded_with_a_traceback_and_not_as_a_data_refusal(tmp_path,
                                                                                   monkeypatch):
    monkeypatch.setattr(INT, "attach", raising(TypeError("union() takes 2 arguments")))
    isolated = attach(tmp_path)
    status = isolated.status
    assert status["refusal"] == str(CFG.Refusal.ATTACH_FAILED)
    assert status["reason"] == str(ISO.Reason.UNEXPECTED_ERROR)
    assert status["expected_failure"] is False
    assert status["boundary"] == "AUDITED_NOT_RERAISED_TO_PROTECT_E"
    entry = ISO.read_status(tmp_path, SESSION)["entries"][0]
    assert "TypeError" in entry["traceback"] and "union() takes 2 arguments" in entry["error"]


def test_strict_mode_reraises_a_programming_bug_but_not_a_data_refusal(tmp_path, monkeypatch):
    """The dry run and the tests want the bug; the live worker wants E to keep trading."""
    monkeypatch.setattr(INT, "attach", raising(TypeError("bad call")))
    with pytest.raises(TypeError):
        attach(tmp_path, strict=True)
    monkeypatch.setattr(INT, "attach", raising(
        UNI.UniverseUnavailable("no reference", reason="REFERENCE_UNAVAILABLE")))
    assert attach(tmp_path, strict=True).status["refusal"] == str(CFG.Refusal.DATA_UNAVAILABLE)


def test_the_boundary_never_catches_an_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(INT, "attach", raising(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        attach(tmp_path)


def test_a_cut_that_raises_is_classified_and_returned_rather_than_propagated(tmp_path):
    class Breaking:
        def run_cut(self):
            raise MoverDataUnavailableError(DataUnavailable.NO_GROUPED_DAILY, "2026-09-17")

    isolated = ISO.IsolatedAttach(active=True, handle=Breaking(), session=SESSION,
                                  repo=tmp_path, log=lambda text: None)
    body = isolated.run_cut()
    assert body["phase"] == str(ISO.Phase.CUT)
    assert body["reason"] == str(ISO.Reason.NO_GROUPED_DAILY)
    assert body["candidates"] == 0 and body["e_run_continues"] is True


def test_an_unwritable_audit_does_not_take_the_run_down(tmp_path, monkeypatch):
    monkeypatch.setattr(INT, "attach", raising(
        UNI.UniverseUnavailable("no reference", reason="REFERENCE_UNAVAILABLE")))
    monkeypatch.setattr(ISO, "record", raising(OSError("read-only file system")))
    status = attach(tmp_path).status
    assert status["isolation"]["audit"] is None
    assert "read-only file system" in status["isolation"]["audit_error"]
    assert status["status"] == "FAILED"


# -- section J7: a repeated failure is one audit entry, counted ------------------------------

def test_repeating_the_same_failure_is_idempotent_in_the_audit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(INT, "attach", raising(
        UNI.UniverseUnavailable("no dated reference cache", reason="REFERENCE_UNAVAILABLE")))
    for _ in range(3):
        attach(tmp_path)
    body = ISO.read_status(tmp_path, SESSION)
    assert len(body["entries"]) == 1
    assert body["entries"][0]["occurrences"] == 3
    assert body["latest"]["refusal"] == str(CFG.Refusal.DATA_UNAVAILABLE)
    monkeypatch.setattr(INT, "attach", raising(
        UNI.UniverseUnavailable("grouped daily missing", reason="NO_GROUPED_DAILY")))
    attach(tmp_path)                                     # a *different* failure is its own row
    body = ISO.read_status(tmp_path, SESSION)
    assert [entry["reason"] for entry in body["entries"]] == ["REFERENCE_UNAVAILABLE",
                                                             "NO_GROUPED_DAILY"]
    assert body["entries"][0]["occurrences"] == 3 and body["entries"][1]["occurrences"] == 1


def test_a_truncated_audit_file_is_replaced_rather_than_appended_to(tmp_path):
    path = ISO.status_path(tmp_path, SESSION)
    path.parent.mkdir(parents=True)
    path.write_text('{"format": "a-mover-live-status-v1", "entries": [', encoding="utf-8")
    ISO.record(tmp_path, SESSION, {"phase": "ATTACH", "refusal": "DATA_UNAVAILABLE",
                                   "reason": "REFERENCE_UNAVAILABLE", "detail": "x"})
    assert len(ISO.read_status(tmp_path, SESSION)["entries"]) == 1


def test_each_session_has_its_own_audit_file(tmp_path, monkeypatch):
    monkeypatch.setattr(INT, "attach", raising(UNI.UniverseUnavailable("no reference")))
    attach(tmp_path, session=SESSION)
    attach(tmp_path, session=LIVE)
    assert ISO.status_path(tmp_path, SESSION).is_file()
    assert ISO.status_path(tmp_path, LIVE).is_file()


# -- section D: E continues, with the worker's own expressions -------------------------------

def e_worker_seam(isolated, *, order):
    """Lines 261-300 of ``run_e_rt2_dryrun.run``, with E's phases as recorded stubs.

    The point is the control flow, not the Kiwoom calls: A's failure must not skip, reorder or
    shorten any of E's four phases, and must not move E's rolling deadline.
    """
    ran: list[str] = []
    report: dict = {}
    resolved = isolated.handle.rolling_order(order) if isolated.handle is not None else order
    deadline = (isolated.handle.rolling_until() if isolated.handle is not None
                else datetime.combine(SESSION, FZ.REFRESH_B_AT, tzinfo=ET))
    ran.append("acquisition")
    if isolated.active:
        report["a_mover_live"] = isolated.run_cut()
    for phase in ("tick_shard_refresh", "finalization", "paper"):
        ran.append(phase)
    return {"ran": tuple(ran), "order": resolved, "rolling_deadline": deadline,
            "report": report}


@pytest.mark.parametrize("error", [
    UNI.UniverseUnavailable("no dated reference cache", reason="REFERENCE_UNAVAILABLE"),
    UNI.UniverseUnavailable("grouped daily missing", reason="NO_GROUPED_DAILY"),
    MoverDataUnavailableError(DataUnavailable.PREMARKET_BASELINE_TOO_SHORT, "12 of 20"),
    LC.LiveContractDrift("the handoff checksum moved"),
    TypeError("a programming bug on A's side"),
])
def test_every_a_failure_leaves_e_phases_and_deadline_identical_to_a_off(tmp_path, monkeypatch,
                                                                        error):
    """Section D, J2-J5. One parametrised proof for data, authority and unexpected failures."""
    order = ["SPY", "AAA", "BBB"]
    off = e_worker_seam(attach(tmp_path, environ=OFF), order=order)
    monkeypatch.setattr(INT, "attach", raising(error))
    failed = e_worker_seam(attach(tmp_path), order=order)
    assert failed["ran"] == off["ran"] == ("acquisition", "tick_shard_refresh",
                                          "finalization", "paper")
    assert failed["order"] is order and off["order"] is order
    assert failed["rolling_deadline"] == off["rolling_deadline"]
    assert failed["rolling_deadline"].time() == FZ.REFRESH_B_AT
    assert failed["report"]["a_mover_live"]["status"] == "FAILED"
    assert off["report"] == {}                           # A off adds no key at all


def test_the_worker_calls_the_isolated_attach_and_no_longer_wraps_it_itself(tmp_path):
    """A regression guard on the seam: an unguarded ``attach`` there is the original defect."""
    source = (Path(INT.__file__).resolve().parents[1] / "dev" / "run_e_rt2_dryrun.py"
              ).read_text(encoding="utf-8")
    assert "A_LIVE.attach_isolated(" in source
    assert "A_LIVE.attach(" not in source
    assert "if a_live.active:" in source


# -- section C: nothing is substituted for a failed A ----------------------------------------

def test_a_failed_morning_cannot_inject_a_previous_session_candidate(tmp_path):
    """Paper injection is zero by construction: the run query is dated with the entry session."""
    from sqlalchemy.orm import sessionmaker
    from app.core.database import Base, create_db_engine
    from app.strategy_a_mover_live import paper_adapter as PA
    engine = create_db_engine(f"sqlite:///{tmp_path / 'paper.sqlite3'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as session:
            assert PA.live_run_for(session, LIVE) is None
            assert PA.load_live_approved_candidates(session, LIVE) == ()
    finally:
        engine.dispose()


def test_the_refusal_vocabulary_admits_no_gpt_call(tmp_path):
    """Every refusal, the new one included, is in the no-GPT set."""
    assert CFG.Refusal.ATTACH_FAILED in CFG.NO_GPT_CALL
    assert set(CFG.NO_GPT_CALL) == set(CFG.Refusal)


# ============================================================================================
# Section K: the stale E staging artifact
# ============================================================================================

E_DIR = UNI.E_UNIVERSE_DIR


def artifact(repo: Path, target: date, *, asof: date | None = None, symbols=("EEE", "ZZZ"),
             name: str | None = None, body: dict | None = None) -> Path:
    """One E staging artifact in the pre-v2 shape the deployed stager writes."""
    from app.market.calendar import MarketCalendar
    asof = asof or MarketCalendar("America/New_York").previous_trading_day(target)
    path = Path(repo) / E_DIR / (name or f"universe_{target.isoformat()}.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = body if body is not None else {
        "format": "e-rt2-canonical-universe-v1", "session": target.isoformat(),
        "d_minus_1": asof.isoformat(), "symbols": list(symbols), "digest": "d"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_k1_the_session_own_artifact_is_read(tmp_path):
    artifact(tmp_path, LIVE)
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.status == "STAGED"
    assert staged.artifact == f"universe_{LIVE.isoformat()}.json"
    assert staged.symbols == ("EEE", "ZZZ")
    assert staged.rejected is None


def test_k2_a_previous_session_artifact_is_refused_by_name(tmp_path):
    """The reported production case: 2026-09-22 on disk, 2026-10-05 expected."""
    artifact(tmp_path, date(2026, 9, 22))
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.symbols == ()
    assert staged.artifact is None
    assert staged.status == "STALE_STAGING_ARTIFACT"
    assert staged.rejected == "universe_2026-09-22.json"
    assert "is not reused" in staged.detail


def test_k3_with_both_present_only_the_current_one_is_used(tmp_path):
    artifact(tmp_path, date(2026, 9, 22), symbols=("OLD1", "OLD2", "OLD3"))
    artifact(tmp_path, LIVE, symbols=("NEW1",))
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.artifact == f"universe_{LIVE.isoformat()}.json"
    assert staged.symbols == ("NEW1",)
    assert staged.rejected is None


def test_k4_a_previous_only_directory_never_silently_falls_back(tmp_path):
    for day in (date(2026, 9, 18), date(2026, 9, 21), date(2026, 9, 22)):
        artifact(tmp_path, day, symbols=(f"S{day.day}",))
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.symbols == ()
    assert staged.rejected == "universe_2026-09-22.json"  # the newest, named only to refuse it
    assert UNI.e_universe_path(tmp_path, LIVE) is None


def test_k5_a_malformed_or_undated_artifact_is_refused_explicitly(tmp_path):
    path = artifact(tmp_path, LIVE)
    path.write_text("{not json", encoding="utf-8")
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.status == "MALFORMED_STAGING_ARTIFACT" and staged.symbols == ()

    artifact(tmp_path, LIVE, body={"format": "e-rt2-canonical-universe-v1",
                                   "symbols": ["EEE"]})
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.status == "MALFORMED_STAGING_ARTIFACT"
    assert "declares no target or as-of session" in staged.detail

    artifact(tmp_path, LIVE, body=["EEE", "ZZZ"])
    assert UNI.e_staging(tmp_path, LIVE).status == "MALFORMED_STAGING_ARTIFACT"


def test_the_filename_is_not_the_authority(tmp_path):
    """A file named for this session whose payload targets another is still refused."""
    artifact(tmp_path, date(2026, 9, 22), name=f"universe_{LIVE.isoformat()}.json")
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.status == "STALE_STAGING_ARTIFACT"
    assert "artifact targets 2026-09-22" in staged.detail


def test_a_wrong_as_of_session_is_refused_by_e_own_rule(tmp_path):
    artifact(tmp_path, LIVE, asof=date(2026, 9, 30))
    staged = UNI.e_staging(tmp_path, LIVE)
    assert staged.status == "STALE_STAGING_ARTIFACT"
    assert "as-of" in staged.detail


def test_the_rule_applied_is_e_own_identity_function(tmp_path):
    """Not a second rule beside E's: the same function E's worker refuses with."""
    from app.market.calendar import MarketCalendar
    cal = MarketCalendar("America/New_York")
    stale = json.loads(artifact(tmp_path, date(2026, 9, 22)).read_text(encoding="utf-8"))
    assert UB.d_minus_1_identity(stale, LIVE, cal)[0] is False
    assert UB.d_minus_1_identity(stale, date(2026, 9, 22), cal)[0] is True
    assert "d_minus_1_identity" in UNI.E_IDENTITY_RULE


def test_k6_the_declaration_persists_which_artifact_was_used_and_which_was_refused():
    used = UNI.UnionUniverse(
        session=LIVE, a_symbols=("AAA",), e_symbols=("EEE",), union=("AAA", "EEE"),
        reference_as_of=date(2026, 10, 1), reference_checksum="c", reference_active_rows=1,
        pruned_no_daily_baseline=0, pruned_split_session=0,
        e_artifact=f"universe_{LIVE.isoformat()}.json").declaration()
    assert used["e_side_status"] == "STAGED"
    assert used["e_artifact"] == f"universe_{LIVE.isoformat()}.json"
    assert used["e_artifact_rejected"] is None
    assert used["e_previous_session_fallback"] == "DISABLED"
    assert "d_minus_1_identity" in used["e_artifact_identity_rule"]

    refused = UNI.UnionUniverse(
        session=LIVE, a_symbols=("AAA",), e_symbols=(), union=("AAA",),
        reference_as_of=date(2026, 10, 1), reference_checksum="c", reference_active_rows=1,
        pruned_no_daily_baseline=0, pruned_split_session=0, e_artifact=None,
        e_artifact_rejected="universe_2026-09-22.json",
        e_artifact_rejection_reason="STALE_STAGING_ARTIFACT",
        e_artifact_rejection_detail="not reused").declaration()
    assert refused["e_side_status"] == "STALE_STAGING_ARTIFACT"
    assert refused["e_artifact_rejected"] == "universe_2026-09-22.json"
    assert refused["e_symbols"] == 0 and refused["union_symbols"] == 1


def test_an_absent_e_directory_is_not_a_rejection():
    """"E staged nothing" and "a stale file was refused" must stay different facts."""
    staged = UNI.e_staging(Path("/nonexistent-repo-for-this-test"), LIVE)
    assert staged.status == "NOT_STAGED_FOR_THIS_SESSION"
    assert staged.rejected is None and staged.symbols == ()


def test_a_refused_e_side_does_not_stop_a(tmp_path):
    """A's authority is A's reference cache; E's side is additive, so a refusal is additive."""
    artifact(tmp_path, date(2026, 9, 22))
    union = UNI.UnionUniverse(
        session=LIVE, a_symbols=("AAA", "BBB"), e_symbols=UNI.e_staging(tmp_path, LIVE).symbols,
        union=("AAA", "BBB"), reference_as_of=date(2026, 10, 1), reference_checksum="c",
        reference_active_rows=2, pruned_no_daily_baseline=0, pruned_split_session=0,
        e_artifact=None, e_artifact_rejected="universe_2026-09-22.json",
        e_artifact_rejection_reason="STALE_STAGING_ARTIFACT")
    assert union.union == ("AAA", "BBB")                 # A's own universe, undiminished
    assert union.e_only == ()


def test_current_session_generation_persists_identity_and_is_read_by_a(tmp_path, monkeypatch):
    """Exercise E's builder and writer, then A's actual reader; no prewritten identity."""
    import gzip
    from app.strategy_e_max_forward import forward_daily as FD, storage as ST
    from app.market.calendar import MarketCalendar

    previous = MarketCalendar().previous_trading_day(LIVE)
    splits = ST.splits_asof_path(tmp_path, previous)
    grouped = ST.grouped_path(tmp_path, previous)
    for path, body in (
        (splits, {"results": []}),
        (grouped, {"body": {"results": [
            {"T": "AAA", "c": 100, "v": 1_000_000},
            {"T": "SPY", "c": 500, "v": 10_000_000}]}}),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(body).encode()))
    monkeypatch.setattr(FD, "eligible_universe", lambda *args, **kwargs: (("AAA",), []))
    body = UB.build(LIVE, base=None, root=tmp_path)
    path = tmp_path / E_DIR / f"universe_{LIVE.isoformat()}.json"
    UB.write(body, path)
    persisted = UB.load(path)
    assert persisted["target_session"] == LIVE.isoformat()
    assert persisted["asof_session"] == previous.isoformat()
    assert UB.d_minus_1_identity(persisted, LIVE)[0]
    assert UNI.e_staging(tmp_path, LIVE).symbols == ("AAA",)


@pytest.mark.parametrize("error", [
    UNI.UniverseUnavailable("missing daily", reason="NO_GROUPED_DAILY"),
    TypeError("unexpected attach defect"),
])
def test_real_e_worker_finalizes_and_runs_paper_after_a_attach_failure(tmp_path, monkeypatch,
                                                                    error):
    """Run the worker itself with offline lanes; compare every deterministic E artifact."""
    from types import SimpleNamespace
    from app.dev import run_e_rt2_dryrun as worker
    from app.core import config as core_config
    from app.integrations.kiwoom import auth

    secret = SimpleNamespace(get_secret_value=lambda: "offline-fixture")
    monkeypatch.setattr(core_config, "get_settings", lambda: SimpleNamespace(
        kiwoom_base_url="https://offline.invalid", kiwoom_app_key=secret,
        kiwoom_app_secret=secret))
    monkeypatch.setattr(auth, "KiwoomAuthClient", lambda **kwargs: object())
    monkeypatch.setattr(worker, "safety_checks", lambda: {"offline": True})
    monkeypatch.setattr(worker, "a_health", lambda: {"status": "fixture"})
    monkeypatch.setattr(worker, "log", lambda text: None)

    class Client:
        request_counts = {}
        order_request_count = 0

        def __init__(self, **kwargs):
            pass

        def _collect(self, *args, **kwargs):
            return [{"stk_cd": symbol} for symbol in ("AAA", "BBB", "SPY")]

    monkeypatch.setattr(FZ, "ChartLaneClient", Client)
    path = artifact(tmp_path, LIVE, symbols=("AAA", "BBB"))
    body = json.loads(path.read_text()) | {
        "close_d_minus_1": {"AAA": 100, "BBB": 100},
        "dollar_volume_d_minus_1": {"AAA": 100_000_000, "BBB": 100_000_000},
        "spy_close_d_minus_1": 100}
    path.write_text(json.dumps(body))

    results = []
    for enabled in (False, True):
        clock = VirtualClock(datetime.combine(LIVE, time(9, 20, 39), tzinfo=ET))
        monkeypatch.setattr(worker, "now", clock)
        monkeypatch.setattr(worker.time, "sleep", clock.tick)
        phases = []

        def refresh(lane, item, session, now):
            phases.append("refresh")
            clock.tick(1)

        def finalize(lane, item, session, now):
            phases.append("finalize")
            lane.calls += 1
            item.finalized_at = now()
            item.contiguous = True
            item.complete_through = 9 * 60 + 24
            item.data_source = "KIWOOM_" + lane.api_id

        def paper(*args, **kwargs):
            phases.append("paper")
            return {"ran": True, "entries": 0, "fills": 0, "real_orders": 0}

        monkeypatch.setattr(FZ, "refresh", refresh)
        monkeypatch.setattr(FZ, "finalize_minute", finalize)
        monkeypatch.setattr(FZ, "finalize_ticks", finalize)
        monkeypatch.setattr(worker, "_paper_stage", paper)
        monkeypatch.setattr(INT, "attach", raising(error))
        monkeypatch.setenv(CFG.ENV_FLAG, "true" if enabled else "false")
        original = INT.attach_isolated

        def isolated_attach(**kwargs):
            return original(**(kwargs | {"repo": tmp_path}))

        # Restore this wrapper after each run so the second run cannot wrap itself.
        with monkeypatch.context() as local:
            local.setattr(INT, "attach_isolated", isolated_attach)
            out = tmp_path / ("failed" if enabled else "off")
            assert worker.run(path, out, paper=True,
                              rvol_store_path=tmp_path / "rvol.sqlite3") == 0
        root = out / LIVE.isoformat()
        report = json.loads((root / "run.json").read_text())
        assert report["status"] == "COMPLETE"
        assert report["capacity"]["status_counts"]["SPARSE_NO_PREMARKET"] == 2
        assert phases.count("finalize") == 3  # both E symbols and SPY
        assert phases[-1] == "paper"
        results.append((report, phases, root))
    off, failed = results
    assert off[1] == failed[1]
    for name in ("capacity", "lanes", "rolling", "availability", "orders", "session_record"):
        assert off[0][name] == failed[0][name]
    for name in ("symbol_status.csv", "lane_stats.json", "cutoff_audit.json"):
        assert (off[2] / name).read_bytes() == (failed[2] / name).read_bytes()
    assert "a_mover_live" not in off[0]
    assert failed[0]["a_mover_live"]["status"] == "FAILED"
    assert failed[0]["a_mover_live"]["paper_injections"] == 0


# -- section L, M: nothing about E's SLA or A's rules moved ----------------------------------

def test_e_sla_constants_are_untouched():
    assert FZ.FINALIZE_AT == time(9, 25)
    assert FZ.DEADLINE == time(9, 29, 45)
    assert FZ.REFRESH_B_AT == time(9, 20, 40)
    assert FZ.CUTOFF_LAST == time(9, 24)
    assert (FZ.MINUTE_API, FZ.TICK_API) == ("usa06011", "usa06010")
    schedule = SCHED.SharedSchedule(SESSION)
    assert schedule.a_cut.time() == time(9, 15)
    assert schedule.e_cut.time() == time(9, 25)
    assert SCHED.E_CUTOFF_LAST_MINUTE == 9 * 60 + 24


def test_a_rules_are_untouched():
    from app.backtest.mover_scanner_v1 import contract as K
    contract = LC.current()
    assert contract.pool_size == 35 and contract.top_count == 8
    assert contract.scan_cut_minute == K.SCAN_CUT_MINUTE == 9 * 60 + 15
    assert contract.research_parent_checksum == K.HANDOFF_CHECKSUM
    assert LC.verify()["research_parent"]["scanner_checksum"] == K.HANDOFF_CHECKSUM
    assert contract.parity_is_a_production_gate is False


def test_the_prune_rules_and_their_rejections_are_unchanged():
    assert UNI.PRUNE_RULES == ("ACTIVE_COMMON_STOCK_REFERENCE_CACHE",
                               "FULL_DAILY_VOLUME_BASELINE_AT_D_MINUS_1",
                               "NO_SPLIT_EXECUTING_THIS_SESSION")
    assert set(UNI.PRUNE_REJECTED) == {"D_MINUS_1_CLOSE_FLOOR",
                                       "PRIOR_SESSION_PREMARKET_ACTIVITY"}
