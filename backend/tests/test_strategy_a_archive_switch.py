"""Strategy A archive switch (A_PAPER_RUNTIME_ENABLED): off stops A's owners and research writes only."""

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.strategy_a_mover_live import config as CFG


@pytest.mark.parametrize("raw,expected", [(None, True), ("", True), ("true", True), ("1", True),
                                          ("garbage", True), ("false", False), ("0", False),
                                          ("off", False), (" No ", False)])
def test_only_a_false_word_archives_a(raw, expected) -> None:
    environ = {} if raw is None else {CFG.ENV_PAPER_RUNTIME_FLAG: raw}
    assert CFG.paper_runtime_enabled(environ) is expected


def test_require_paper_runtime_refuses_only_when_archived() -> None:
    CFG.require_paper_runtime({})
    with pytest.raises(ValueError, match="archived"):
        CFG.require_paper_runtime({CFG.ENV_PAPER_RUNTIME_FLAG: "false"})


def _patched_owners(monkeypatch) -> list[str]:
    started: list[str] = []
    # E-MAX switched on, so the lifespan reaches its runtime: the archive must leave E's start alone.
    # Off, the runtime is not imported at all (tests/test_backend_startup_imports.py).
    monkeypatch.setenv("STRATEGY_E_MAX_ENABLED", "true")
    monkeypatch.setattr(main, "activate_operator_simulation_runtime", lambda *a, **k: object())
    monkeypatch.setattr(main.mover_live_entry, "start", lambda *a, **k: started.append("entry"))
    monkeypatch.setattr(main, "start_position_management_runtime", lambda *a, **k: started.append("position"))
    monkeypatch.setattr(main, "start_end_of_day_runtime", lambda *a, **k: started.append("eod"))
    monkeypatch.setattr(main.strategy_e_max_runtime, "start_from_env", lambda *a, **k: started.append("e_max"))
    monkeypatch.setattr(main, "clear_active_sim_broker", lambda: None)
    return started


def test_archived_a_starts_no_a_owner_but_keeps_e(monkeypatch) -> None:
    monkeypatch.setenv(CFG.ENV_PAPER_RUNTIME_FLAG, "false")
    started = _patched_owners(monkeypatch)
    with TestClient(main.create_app()) as client:
        assert client.get("/health").status_code == 200
    assert started == ["e_max"]


def test_unset_flag_keeps_the_deployed_startup(monkeypatch) -> None:
    monkeypatch.delenv(CFG.ENV_PAPER_RUNTIME_FLAG, raising=False)
    started = _patched_owners(monkeypatch)
    with TestClient(main.create_app()) as client:
        assert client.get("/health").status_code == 200
    assert started == ["entry", "position", "eod", "e_max"]


@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/v1/research/import", {"raw_json": "{}"}),
    ("put", "/api/v1/research/1/activate", None),
    ("put", "/api/v1/research/1/decisions/AAPL", {"decision": "APPROVE"}),
])
def test_archived_a_refuses_research_writes(monkeypatch, method, path, body) -> None:
    monkeypatch.setenv(CFG.ENV_PAPER_RUNTIME_FLAG, "false")
    with TestClient(main.create_app()) as client:
        response = getattr(client, method)(path, **({} if body is None else {"json": body}))
    assert response.status_code == 409
    assert "archived" in response.json()["error"]["message"]
