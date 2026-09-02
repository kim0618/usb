"""Generate the user-readable runtime smoke report."""

from pathlib import Path

from app.replay_smoke.runner import ReplaySmokeRunner


if __name__ == "__main__":
    target = Path("data/runtime/replay_smoke_report.json")
    result = ReplaySmokeRunner().validate(report_path=target)
    print(f"wrote {target}: {len(result.trading_days)} sessions, {len(result.shadow_results)} paths")
