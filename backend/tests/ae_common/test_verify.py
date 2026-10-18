"""The post-session audit on a real producer morning: E execution and book checks, per-group verdicts."""

import json

from app.ae_common import producer as P
from app.dev import verify_ae_shadow as V
from tests.ae_common.helpers import SESSION
from tests.ae_common.test_producer import paper_morning


def audited(tmp_path, monkeypatch, *, tamper=None, **kw):
    result, book_dir = paper_morning(tmp_path, monkeypatch, **kw)
    day = SESSION.isoformat()
    run = tmp_path / "run" / day
    P.write_json(run / "producer.json", result.report | {"single_app_key": True, "real_order_requests": 0})
    P.write_json(run / "e_shadow.json", result.e_shadow)
    P.write_json(run / "a_parity.json", result.a_parity)
    if tamper:
        path = book_dir / "sessions" / f"{day}.json"
        state = json.loads(path.read_text())
        tamper(state)
        path.write_text(json.dumps(state))
    return V.gate(SESSION, tmp_path / "run", tmp_path / "consumer", tmp_path / "snap",
                  e_book=tmp_path / "erun" / "paper_state_v2" / "OFFICIAL_KIWOOM_PAPER")


def test_an_executed_session_passes_every_e_execution_check(tmp_path, monkeypatch):
    body = audited(tmp_path, monkeypatch)
    for name in ("E_T0_at_0925", "E_T1_before_0930", "E_seal_before_0930", "E_cutoff_violation_zero",
                 "E_snapshot_0924_parity", "E_h5_r1_top3_b2", "E_no_real_orders", "E_book_session_recorded",
                 "E_book_version", "E_executed_decision_is_sealed_decision", "E_entry_0930_open",
                 "E_exit_exact_0934_close", "E_book_chain"):
        assert body["checks"][name]["pass"] is True, (name, body["checks"][name])
    assert body["verdicts"]["E"]["verdict"] == "PASS"
    assert body["verdicts"]["A"]["verdict"] == "FAIL"          # no A consumer ran in this fixture
    assert body["checks"]["E_book_version"]["initial_equity"] == "10000"


def test_an_exit_at_the_0935_open_fails_the_audit(tmp_path, monkeypatch):
    def at_0935(state):
        for exit_ in state["exits"].values():
            exit_["fill_bar"]["timestamp"] = exit_["fill_bar"]["timestamp"].replace("T09:34", "T09:35")
    body = audited(tmp_path, monkeypatch, tamper=at_0935)
    assert body["checks"]["E_exit_exact_0934_close"]["pass"] is False
    assert body["verdicts"]["E"]["verdict"] == "FAIL"


def test_a_broken_book_chain_fails_the_audit(tmp_path, monkeypatch):
    result, book_dir = paper_morning(tmp_path, monkeypatch)
    path = book_dir / "book.json"
    book = json.loads(path.read_text())
    book["sessions"][SESSION.isoformat()]["equity_after"] = "99999"
    path.write_text(json.dumps(book))
    checks = V.execution_checks(json.loads((book_dir / "sessions" / f"{SESSION.isoformat()}.json").read_text()),
                                book, SESSION, result.e_shadow["from_snapshot"]["decision"], True)
    assert checks["E_book_chain"]["pass"] is False


def test_an_unauthorized_session_is_audited_as_not_traded(tmp_path, monkeypatch):
    body = audited(tmp_path, monkeypatch, seeded=False)
    assert body["checks"]["E_unauthorized_not_traded"]["pass"] is True
    assert "E_entry_0930_open" not in body["checks"]
