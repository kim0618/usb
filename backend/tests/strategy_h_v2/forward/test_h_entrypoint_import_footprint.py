"""P2.1: the H entrypoint does not load pyarrow, only when it is the entrypoint, and MarketCalendar answers
the same with and without it. Each check runs in a fresh interpreter, as systemd starts the unit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3]

_ENTRYPOINT = """
import json, runpy, sys
sys.argv = ["run_h_v2_d7", "--help"]
try:
    runpy.run_module("app.dev.run_h_v2_d7", run_name="__main__", alter_sys=True)
except SystemExit:
    pass
import pandas.compat.pyarrow as compat
print(json.dumps({"pyarrow": repr(sys.modules.get("pyarrow", "absent")),
                  "submodules": sorted(m for m in sys.modules if m.startswith("pyarrow.")),
                  "pandas_has_pyarrow": compat.HAS_PYARROW, "calendar_loaded": "app.market.calendar" in sys.modules}))
"""

_CALENDAR = """
import hashlib, json, sys
if sys.argv[1] == "blocked":
    sys.modules.setdefault("pyarrow", None)
from datetime import date, timedelta
from app.market.calendar import MarketCalendar
def call(f, *a):
    try:
        v = f(*a)
    except Exception as e:
        return f"EXC {type(e).__name__}: {e}"
    if v is not None and hasattr(v, "session_date"):
        return [v.session_date.isoformat(), v.market_open.isoformat(), v.market_close.isoformat(), v.is_early_close]
    return v.isoformat() if hasattr(v, "isoformat") else v
c = MarketCalendar()
rows = []
for n in range(0, 1461):
    d = date(2024, 1, 1) + timedelta(n)
    rows.append([d.isoformat(), call(c.is_trading_day, d), call(c.is_holiday, d), call(c.session, d),
                 call(c.next_trading_day, d), call(c.previous_trading_day, d), call(c.holding_day_number, d, d + timedelta(30))])
print(json.dumps({"hash": hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
                  "pyarrow": sys.modules.get("pyarrow") is not None,
                  "thanksgiving_2026": rows[(date(2026, 11, 26) - date(2024, 1, 1)).days][1:4],
                  "black_friday_2026": rows[(date(2026, 11, 27) - date(2024, 1, 1)).days][3]}))
"""


def _run(code: str, *args: str) -> dict:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(BACKEND), "HOME": str(BACKEND)}
    done = subprocess.run([sys.executable, "-c", code, *args], cwd=BACKEND, env=env, capture_output=True,
                          text=True, timeout=120, check=True)
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_entrypoint_does_not_load_pyarrow():
    state = _run(_ENTRYPOINT)
    assert state["calendar_loaded"]                     # the import path that used to pull pyarrow did run
    assert state["pyarrow"] == "None" and state["submodules"] == []
    assert state["pandas_has_pyarrow"] is False


def test_library_import_is_untouched():
    # the control: the same detection sees pyarrow when the module is imported, not run (backend, tests)
    state = _run("import json, sys, app.dev.run_h_v2_d7\n"
                 "print(json.dumps({'pyarrow': sys.modules.get('pyarrow') is not None}))")
    assert state["pyarrow"] is True


def test_market_calendar_is_identical_without_pyarrow():
    loaded, blocked = _run(_CALENDAR, "loaded"), _run(_CALENDAR, "blocked")
    assert loaded["pyarrow"] is True and blocked["pyarrow"] is False
    assert blocked["hash"] == loaded["hash"]
    assert blocked["thanksgiving_2026"][:2] == [False, True]          # a holiday, not silently a session
    assert blocked["black_friday_2026"][3] is True                    # early close still detected
