"""Backend startup must not load the heavy research stacks it does not use.

A default start (Strategy A archived, E-MAX off) answered the market calendar through pandas and
imported the A mover contract and the E-MAX runtime for nothing, which kept pandas, pyarrow and the
mover/E research modules resident in the backend. These tests run the real entry point in a fresh
interpreter so an import that creeps back fails here, and pin what the deferred imports replaced.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

BACKEND = Path(__file__).resolve().parents[1]
HEAVY = ("pandas", "exchange_calendars", "pyarrow")
LEGACY = ("app.strategy_a_mover_live.contract", "app.strategy_a_mover_live.paper_adapter",
          "app.services.candidate_source", "app.backtest.mover_scanner_v1.contract",
          "app.strategy_e_max_rt.runtime", "app.strategy_e_max_rt.config", "app.strategy_e_max.v1")
#: Modules a start must load; they prove the probe sees sys.modules at all.
CONTROLS = ("app.main", "app.market.calendar", "app.research.current_run", "sqlalchemy",
            "app.strategy_e_max_rt.readback")

PROBE = r"""
import json, sys
if sys.argv[2] == "fake_e_max":
    # Stand-in runtime so the enabled lifespan can be entered without a market-data provider.
    import importlib.abc, importlib.machinery, types
    calls = []
    class FakeRuntime(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, name, path, target=None):
            if name == "app.strategy_e_max_rt.runtime":
                return importlib.machinery.ModuleSpec(name, self)
        def create_module(self, spec):
            module = types.ModuleType(spec.name)
            module.start_from_env = lambda factory: calls.append("start_from_env") or False
            async def stop():
                calls.append("stop")
            module.stop = stop
            return module
        def exec_module(self, module):
            pass
    sys.meta_path.insert(0, FakeRuntime())
import app.main
from app.core.database import Base, engine
import importlib, pkgutil, app.models
for module in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module("app.models." + module.name)
assert engine.url.database.endswith("probe.sqlite3"), engine.url
Base.metadata.create_all(engine)
names = json.loads(sys.argv[1])
report = {"startup": {n: n in sys.modules for n in names}, "database": engine.url.database}
if sys.argv[2] == "fake_e_max":
    from fastapi.testclient import TestClient
    with TestClient(app.main.create_app()) as client:
        client.get("/health")
    report["e_max_calls"] = calls
    report["after_requests"] = {n: n in sys.modules for n in names}
if sys.argv[2] == "requests":
    from fastapi.testclient import TestClient
    with TestClient(app.main.create_app()) as client:
        codes = {}
        for path in json.loads(sys.argv[3]):
            for _ in range(3):
                codes[path] = client.get(path).status_code
    report["codes"] = codes
    report["after_requests"] = {n: n in sys.modules for n in names}
print("REPORT" + json.dumps(report))
"""

NORMAL_REQUESTS = [
    "/health", "/api/v1/dashboard", "/api/v1/strategies", "/api/v1/strategies/cards",
    "/api/v1/strategies/daily", "/api/v1/strategies/performance", "/api/v1/strategies/portfolio",
    "/api/v1/strategies/ledger", "/api/v1/strategy/evaluations", "/api/v1/strategy-i/paper",
    "/api/v1/strategies/STRATEGY_E_MAX_V1/status", "/api/v1/strategies/STRATEGY_E_MAX_V1/account",
    "/api/v1/strategies/STRATEGY_E_MAX_V1/equity", "/api/v1/strategies/STRATEGY_E_MAX_V1/positions",
    "/api/v1/strategies/STRATEGY_H_V2/status", "/api/v1/strategies/STRATEGY_H_V2/account",
    "/api/v1/strategies/STRATEGY_H_V2/equity", "/api/v1/trading/entry-board",
]


def _probe(tmp_path, mode: str, extra_env: dict[str, str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STRATEGY_E_MAX", "A_", "USB_STRATEGY_I"))}
    # A archived and E-MAX unset, as on the production host.
    # Both URLs point at a scratch file: the default profile ignores PAPER_DATABASE_URL, and the
    # probe creates tables, so it must never reach the repository's data/runtime database.
    database = "sqlite:///%s" % (tmp_path / "probe.sqlite3")
    env.update(PYTHONPATH=str(BACKEND), A_PAPER_RUNTIME_ENABLED="false",
               DATABASE_URL=database, PAPER_DATABASE_URL=database,
               USB_STRATEGY_I_PAPER_STORE=str(tmp_path / "no_i_store"), **(extra_env or {}))
    names = list(HEAVY + LEGACY + CONTROLS + ("numpy",))
    done = subprocess.run([sys.executable, "-c", PROBE, json.dumps(names), mode, json.dumps(NORMAL_REQUESTS)],
                          cwd=BACKEND.parent, env=env, capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr[-2000:]
    line = next(l for l in done.stdout.splitlines() if l.startswith("REPORT"))
    report = json.loads(line[len("REPORT"):])
    assert report["database"] == str(tmp_path / "probe.sqlite3"), report["database"]
    return report


def test_default_startup_loads_no_heavy_or_legacy_module(tmp_path) -> None:
    loaded = _probe(tmp_path, "startup")["startup"]
    assert all(loaded[n] for n in CONTROLS), loaded
    assert [n for n in HEAVY + LEGACY if loaded[n]] == []


def test_normal_requests_do_not_bring_them_back(tmp_path) -> None:
    report = _probe(tmp_path, "requests")
    assert report["codes"] == {path: 200 for path in NORMAL_REQUESTS}
    assert all(report["after_requests"][n] for n in CONTROLS)
    assert [n for n in HEAVY + LEGACY if report["after_requests"][n]] == []


def test_disabled_e_max_runtime_is_neither_imported_nor_started(tmp_path) -> None:
    report = _probe(tmp_path, "fake_e_max")
    assert report["e_max_calls"] == [] and report["after_requests"]["app.strategy_e_max_rt.runtime"] is False


def test_enabled_e_max_runtime_is_imported_started_and_stopped(tmp_path) -> None:
    # Control for the gate: switched on, the lifespan imports and drives the runtime as before.
    report = _probe(tmp_path, "fake_e_max", {"STRATEGY_E_MAX_ENABLED": "true"})
    assert report["startup"]["app.strategy_e_max_rt.runtime"] is False
    assert report["e_max_calls"] == ["start_from_env", "stop"]
    assert report["after_requests"]["app.strategy_e_max_rt.runtime"] is True


def test_live_run_stamp_matches_the_a_contract() -> None:
    from app.research import current_run as CR
    from app.strategy_a_mover_live import contract as LC

    assert (CR.LIVE_RUN_SCORE_VERSION, CR.LIVE_RUN_PROVIDER) == (LC.RUN_SCORE_VERSION, LC.RUN_PROVIDER)


@pytest.mark.parametrize("raw", [None, "", "1", "true", "TRUE", " yes ", "Yes", "0", "false", "no", "on",
                                 "enabled", " 1 ", "y", "t"])
def test_e_max_gate_reads_the_flag_like_the_runtime_config(raw) -> None:
    import app.main as main
    from app.strategy_e_max_rt import config as CFG

    environ = {} if raw is None else {CFG.ENABLED_ENV: raw}
    assert CFG.ENABLED_ENV == "STRATEGY_E_MAX_ENABLED"
    assert main.e_max_enabled(environ) is CFG.from_env(environ).enabled


def test_deferred_owner_names_resolve_to_the_runtime_modules() -> None:
    import app.main as main
    from app.strategy_a_mover_live import paper_adapter
    from app.strategy_e_max_rt import runtime

    assert main.mover_live_entry is paper_adapter and main.strategy_e_max_runtime is runtime
    with pytest.raises(AttributeError):
        main.not_an_owner  # noqa: B018
