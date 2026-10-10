"""H-V2-D7 operations: what the shipped systemd unit and timer are allowed to say.

The forward shadow stopped at 2026-10-02 for one reason - nothing ran it - and the fix is a timer.
A timer is configuration, so the ways it can be wrong are configuration-shaped, and three of them
would be invisible at runtime:

* the unit could run from ``backend/`` instead of the repository root, which leaves the append-only
  store correct (it resolves from the module's own path) while the refresh scan silently loses every
  SEC submissions cache and rewrites eight real states as ``NO_SUBMISSIONS_CACHE``;
* the unit could pull ``usb-grouped-daily.service`` in with ``Wants=``, so that running H would run
  a Strategy A collection - a side effect on A that H is not allowed to have;
* the unit could run ``launch`` instead of ``update``, which is the one command that writes the
  immutable snapshot and the ledger.

None of these fails loudly, so they are asserted here rather than left to a reading of the file.
Nothing in this module touches the real store, the network, or A's and E's files.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.dev import run_h_v2_d7 as D7

REPO_ROOT = Path(__file__).resolve().parents[4]
UNITS = REPO_ROOT / "deploy/systemd"
SERVICE = UNITS / "usb-h-forward.service"
TIMER = UNITS / "usb-h-forward.timer"

#: The companion unit H is scheduled behind, and the hour it runs at.
GROUPED_DAILY_TIMER = UNITS / "usb-grouped-daily.timer"


def directives(path: Path, key: str) -> list[str]:
    """Every value a unit file gives for one directive, continuation lines joined.

    systemd allows a directive to repeat (``After=`` here does), so this collects all of them
    rather than letting a last-one-wins parser hide one.
    """
    text = re.sub(r"\\\n\s*", " ", path.read_text(encoding="utf-8"))
    return [m.group(1).strip() for m in re.finditer(rf"^{key}=(.*)$", text, re.MULTILINE)]


def one(path: Path, key: str) -> str:
    values = directives(path, key)
    assert len(values) == 1, f"{path.name} gives {key}= {len(values)} times: {values}"
    return values[0]


def test_both_units_are_shipped_in_the_repository() -> None:
    assert SERVICE.is_file() and TIMER.is_file()


def test_the_service_runs_from_the_repository_root() -> None:
    """Kept at the repository root, where every usb unit runs and the EnvironmentFiles live."""
    assert one(SERVICE, "WorkingDirectory") == "/root/usb"
    assert "PYTHONPATH=/root/usb/backend" in directives(SERVICE, "Environment")


def test_the_refresh_cache_no_longer_depends_on_the_working_directory(tmp_path, monkeypatch) -> None:
    """The 2026-10-10 trap: repository-relative SEC paths read as "no cache" from backend/."""
    from app.strategies.h_forward import sec_refresh as SR
    monkeypatch.delenv("STRATEGY_H_FORWARD_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    assert SR.sec_root().is_absolute()
    assert not hasattr(D7, "SUBMISSION_ROOTS"), "the scan must not read repository-relative caches"


def test_the_timer_runs_the_update_command_and_can_never_relaunch() -> None:
    exec_start = one(SERVICE, "ExecStart")
    assert exec_start.endswith("-m app.dev.run_h_v2_d7 update")
    assert "launch" not in exec_start
    assert "/root/usb/.venv/bin/python" in exec_start


def test_the_service_orders_itself_after_grouped_daily_without_pulling_it_in() -> None:
    assert "usb-grouped-daily.service" in directives(SERVICE, "After")
    # Wants=/Requires= on grouped-daily would make an H run execute a Strategy A collection.
    for key in ("Wants", "Requires", "BindsTo", "Requisite"):
        assert not any("grouped-daily" in value for value in directives(SERVICE, key)), key


def test_a_failure_is_one_visible_failure_and_not_a_restart_loop() -> None:
    assert one(SERVICE, "Type") == "oneshot"
    assert directives(SERVICE, "Restart") == []
    assert int(one(SERVICE, "StartLimitBurst")) <= 3


def test_the_update_is_serialised_against_a_hand_run_catch_up() -> None:
    exec_start = one(SERVICE, "ExecStart")
    assert "flock" in exec_start, "a hand-run catch-up could collide with a timer firing"
    # The lock lives where nothing unlinks it: flock locks an inode, not a path.
    assert "/run/usb-h-forward.lock" in exec_start


def test_the_schedule_is_a_market_timezone_and_never_a_hardcoded_kst_hour() -> None:
    calendar = one(TIMER, "OnCalendar")
    assert "America/New_York" in calendar
    for forbidden in ("Asia/Seoul", "KST", "UTC"):
        assert forbidden not in calendar, f"{forbidden} in OnCalendar"


def test_the_cadence_matches_the_grouped_daily_source_it_shadows() -> None:
    assert one(TIMER, "OnCalendar").startswith("Mon..Fri ")
    assert one(GROUPED_DAILY_TIMER, "OnCalendar").startswith("Mon..Fri ")


def test_h_runs_after_grouped_daily_in_the_day_not_before_it() -> None:
    """Same timezone, so the two hours are directly comparable."""
    def hour_minute(path: Path) -> tuple[int, int]:
        calendar = one(path, "OnCalendar")
        match = re.search(r"(\d{2}):(\d{2})", calendar)
        assert match, calendar
        return int(match.group(1)), int(match.group(2))

    assert "America/New_York" in one(GROUPED_DAILY_TIMER, "OnCalendar")
    assert hour_minute(TIMER) > hour_minute(GROUPED_DAILY_TIMER)


def test_the_timer_survives_a_reboot_and_catches_up_a_missed_run() -> None:
    assert one(TIMER, "WantedBy") == "timers.target"
    assert one(TIMER, "Persistent").lower() == "true"
    assert one(TIMER, "Unit") == "usb-h-forward.service"


@pytest.mark.parametrize("key", ["MemoryMax", "TimeoutStartSec"])
def test_the_run_is_bounded_in_memory_and_in_time(key: str) -> None:
    assert one(SERVICE, key)
