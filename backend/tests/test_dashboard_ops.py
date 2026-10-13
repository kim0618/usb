from datetime import datetime, timezone

from app.strategies import dashboard_ops as D

NOW = datetime(2026, 10, 13, 1, 0, tzinfo=timezone.utc)  # 10:00 KST


def test_operating_day_counts_kst_calendar_days_with_start_as_day_one():
    assert D.operating_day("2026-09-28", NOW) == 16
    assert D.operating_day("2026-10-13", NOW) == 1
    assert D.operating_day("2026-10-14", NOW) is None
    assert D.operating_day(None, NOW) is None
    assert D.operating_day("garbage", NOW) is None


def test_operating_day_reads_a_timestamp_in_kst():
    # 2026-10-12 16:00Z is already 10/13 01:00 KST -> day 1 on 10/13
    assert D.operating_day("2026-10-12T16:00:00+00:00", NOW) == 1


def test_status_folds_raw_states_to_one_value():
    run = lambda op, rt=None, w=False: D.normalize_status({"operational_status": op, "runtime_status": rt}, waiting_signal_is_running=w)
    assert run("POSITION_OPEN", "RUNNING") == (D.RUNNING, None)
    assert run("WAITING_SIGNAL", "RUNNING", True) == (D.RUNNING, None)
    assert run("WAITING_SIGNAL", "COMPLETE", False) == (D.WAITING, None)
    assert run("PAUSED", "NOT_LAUNCHED") == (D.WAITING, None)
    assert run("PAUSED", "NO_ACTIVE_SIM_BROKER") == (D.ISSUE, "시뮬레이션 러너 없음")
    assert run("DATA_ERROR", "NO_DECISION") == (D.ISSUE, "결정 없음")
    assert run("DATA_ERROR", "WHATEVER") == (D.ISSUE, "데이터 오류")
    assert run("???")[0] == D.ISSUE


def test_reason_only_travels_with_issue():
    out = D.block(mode="PAPER", status=(D.RUNNING, "x"), since=None, sessions=None, session_kind="공식",
                  last_run_at=None, last_decision=None, now=NOW)
    assert out["status_reason"] is None


def test_mode_is_one_word():
    card = {"lifecycle": "PAPER", "paper_clock": {"status": "STARTED"}}
    assert D.mode_label(card, shadow=False, official_evidence=True) == "OFFICIAL PAPER"
    assert D.mode_label(card, shadow=False, official_evidence=False) == "PAPER"
    assert D.mode_label(card, shadow=True, official_evidence=False) == "SHADOW"
    assert D.mode_label({"lifecycle": "RETIRED"}, shadow=False, official_evidence=True) == "RETIRED"


def test_a_fresh_runner_heartbeat_keeps_the_state_and_a_stale_one_is_an_issue():
    beat = "2026-10-13T00:59:00+00:00"                      # 1 minute before NOW
    assert D.with_heartbeat((D.RUNNING, None), beat, stale_seconds=180, now=NOW) == (D.RUNNING, None)
    exactly = "2026-10-13T00:57:00+00:00"                   # 180 s: the threshold itself is not stale
    assert D.with_heartbeat((D.RUNNING, None), exactly, stale_seconds=180, now=NOW) == (D.RUNNING, None)
    state, reason = D.with_heartbeat((D.RUNNING, None), "2026-10-13T00:50:00+00:00", stale_seconds=180, now=NOW)
    assert state == D.ISSUE and reason == "러너 정상 실행 지연 · 10분"
    state, reason = D.with_heartbeat((D.WAITING, None), "2026-10-12T20:00:00+00:00", stale_seconds=180, now=NOW)
    assert state == D.ISSUE and reason == "러너 정상 실행 지연 · 5시간"


def test_a_missing_heartbeat_is_not_evidence_and_an_existing_issue_is_kept():
    assert D.with_heartbeat((D.RUNNING, None), None, stale_seconds=180, now=NOW) == (D.RUNNING, None)
    assert D.with_heartbeat((D.ISSUE, "결정 없음"), "2020-01-01T00:00:00+00:00",
                            stale_seconds=180, now=NOW) == (D.ISSUE, "결정 없음")
    assert D.with_heartbeat((D.RUNNING, None), "not-a-time", stale_seconds=180, now=NOW) == (D.RUNNING, None)


def test_a_card_reads_the_runner_heartbeat_and_nothing_else(tmp_path):
    from sqlalchemy.orm import sessionmaker

    from app.api.strategies import _a_last_run
    from app.core.database import Base, create_db_engine
    from app.monitoring.service import RuntimeHealthService

    engine = create_db_engine(f"sqlite:///{tmp_path / 'hb.sqlite3'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        assert _a_last_run(session) is None                   # no cycle yet: a dash, not a guess
        at = datetime(2026, 10, 13, 3, 4, tzinfo=timezone.utc)
        RuntimeHealthService(session).record_heartbeat(at)
        assert _a_last_run(session) == at.isoformat()
    engine.dispose()
