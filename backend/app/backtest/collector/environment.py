"""The collector runs on a development PC, or it does not run.

Collection is a local, operator-driven job: it reaches an external API for minutes, holds
a workspace writer lock, and writes files that Google Drive replicates. None of that
belongs on the 1 GB production host, where the same process once took the live site down
with it, and none of it belongs in a unit or a timer. The checks below are cheap and
refuse early, before an HTTP client exists.
"""

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from app.backtest.collector.errors import ProductionEnvironment
from app.core.config import PROJECT_ROOT, Settings


# Where the production checkout lives, and the system directories a service would run
# from. A development checkout under /home or /mnt never matches one of these.
PRODUCTION_PATH_PREFIXES = (Path("/root"), Path("/srv"), Path("/usr/local/lib"), Path("/opt"))
PRODUCTION_APP_ENVS = frozenset({"production", "prod", "staging"})
# systemd exports both to every service it starts; either one means this is not a shell.
SERVICE_MANAGER_VARIABLES = ("INVOCATION_ID", "JOURNAL_STREAM")


@dataclass(frozen=True)
class EnvironmentReport:
    checks: tuple[str, ...]


def assert_local_environment(*, settings: Settings, project_root: Path = PROJECT_ROOT,
                             environ: Mapping[str, str] | None = None) -> EnvironmentReport:
    """Refuse anywhere that looks like production, a service manager, or a non-checkout."""
    env = os.environ if environ is None else environ
    root = Path(project_root).resolve()
    for prefix in PRODUCTION_PATH_PREFIXES:
        if root == prefix or root.is_relative_to(prefix):
            raise ProductionEnvironment(
                f"repository root {root} is under the production path {prefix}; the historical "
                "collector runs on a development PC only")
    app_env = (settings.app_env or "").strip().lower()
    if app_env in PRODUCTION_APP_ENVS:
        raise ProductionEnvironment(
            f"APP_ENV={app_env} is not a development environment")
    for variable in SERVICE_MANAGER_VARIABLES:
        if env.get(variable):
            raise ProductionEnvironment(
                f"{variable} is set, so this process was started by a service manager; the "
                "collector is never run from a unit or a timer")
    if not (root / ".git").exists():
        raise ProductionEnvironment(
            f"{root} is not a git checkout; the collector runs from the development repository")
    return EnvironmentReport((
        f"repository_root={root}",
        f"app_env={app_env or 'unset'}",
        "service_manager=none",
        "production_database=not used by this collector",
    ))
