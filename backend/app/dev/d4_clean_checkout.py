"""Gate M12: does the D4 package import from a tree that contains only committed files?

D4.1 did not create this problem and D4.2 cannot fully fix it, so the probe is built to MEASURE it
rather than to assert it away. What it does is export the git index as a tree - HEAD's content for
every file plus whatever D4.2 has staged, and nothing from an unstaged working-tree edit - into a
temporary directory, and import the D4 entry points there in a subprocess.

`git write-tree` is used deliberately: it reads the index and writes a tree object, touching
neither the working tree nor HEAD. No stash, no checkout, no branch. The user's own uncommitted
work is never at risk from running this, which matters because the whole point is that their
uncommitted work is currently load-bearing.

The probe reports per-entry-point results and, for a failure, the import chain and the missing
symbol. A blocker inside `research/` is reported as such rather than folded into a D4 verdict line:
"D4 does not import" and "a module D4 imports does not import" are different findings with
different owners.

D4.3R repair (brief §12/§13). Two defects found by inspection, not by any test failing:

  `smoke_ok` was initialised `True` and only ever set `False` inside the `if all(...ok...)` branch,
  so a run where the imports themselves failed - which skips the smoke entirely - still reported
  `smoke_ok: true`. A check that never ran is indistinguishable, in that output, from one that ran
  and passed. `smoke_executed` is now tracked separately and `passed` requires it to be true; a
  skipped smoke can never read as a passed one again.

  The probe used to run under `sys.executable`, whatever interpreter happened to invoke it. Under
  the repository's system Python that resolves imports against a different site-packages path
  (`HOME` is pinned to the temp checkout, which drops `~/.local/lib`) and 6 of 10 targets fail with
  `ModuleNotFoundError` - a real environment difference, not a dependency-closure defect, but one
  the smoke-skip bug above then hid entirely. `probe()` now resolves the project's own
  `.venv/bin/python` by default and reports whether it found it (`official_interpreter`); a caller
  that got a non-project interpreter knows not to trust the result as M12 evidence rather than
  discovering it from a stack trace two files away.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import subprocess
import sys
import tempfile

#: What brief §15 requires to work from a clean tree, in the order a run would touch them.
CLEAN_IMPORT_TARGETS: tuple[tuple[str, str], ...] = (
    ("d4_contract_v2", "app.backtest.strategy_h_v2.expectation.contract_v2"),
    ("d4_consensus_language", "app.backtest.strategy_h_v2.expectation.consensus_language"),
    ("d4_inputs", "app.backtest.strategy_h_v2.expectation.d4_inputs"),
    ("d4_gap_contract", "app.backtest.strategy_h_v2.expectation.gap_contract"),
    ("d4_2_contract", "app.backtest.strategy_h_v2.expectation.d4_2_contract"),
    ("expectation_evidence_build", "app.backtest.strategy_h_v2.expectation.evidence_builder"),
    ("d4_output_schema", "app.backtest.strategy_h_v2.expectation.analysis_schema"),
    ("d4_schema_generation", "app.backtest.strategy_h_v2.expectation.prompt"),
    ("d4_validator", "app.backtest.strategy_h_v2.expectation.validate"),
    ("tier_a_runner", "app.dev.run_strategy_h_v2_d4_2"),
)

#: Run after the imports succeed: the two things §15 names that are calls rather than imports.
_SMOKE = """
from app.backtest.strategy_h_v2.expectation.prompt import content_only_schema
schema = content_only_schema()
assert schema["properties"], "schema generation produced no content fields"
from app.backtest.strategy_h_v2.expectation.contract_v2 import ManagementSignalDirection
members = set()
for definition in (schema.get("$defs") or {}).values():
    if sorted(definition.get("enum") or []) == sorted(d.value for d in ManagementSignalDirection):
        members = set(definition["enum"])
assert members, "the direction enum is not visible in the generated schema"
"""


@dataclass(frozen=True)
class ImportProbeResult:
    name: str
    module: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "module": self.module, "ok": self.ok, "error": self.error}


@dataclass(frozen=True)
class CleanCheckoutReport:
    tree: str
    results: list[ImportProbeResult]
    smoke_executed: bool
    smoke_ok: bool
    smoke_error: str = ""
    interpreter: str = ""
    official_interpreter: bool = True

    @property
    def passed(self) -> bool:
        return all(r.ok for r in self.results) and self.smoke_executed and self.smoke_ok

    def blockers(self) -> list[ImportProbeResult]:
        return [r for r in self.results if not r.ok]

    def to_dict(self) -> dict:
        return {
            "gate": "M12", "tree": self.tree, "passed": self.passed,
            "smoke_executed": self.smoke_executed, "smoke_ok": self.smoke_ok,
            "smoke_error": self.smoke_error, "interpreter": self.interpreter,
            "official_interpreter": self.official_interpreter,
            "results": [r.to_dict() for r in self.results],
        }


def export_index_tree(repo_root: Path, destination: Path) -> str:
    """Materialize the git INDEX into `destination`. Returns the tree object id.

    The index, not the working tree: a file the user has edited but not staged contributes its
    HEAD content here, which is exactly the tree a fresh clone of a D4.2 commit would have.
    """
    tree = subprocess.run(
        ["git", "write-tree"], cwd=repo_root, capture_output=True, text=True, check=True,
    ).stdout.strip()
    destination.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(
        ["git", "archive", tree], cwd=repo_root, capture_output=True, check=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(destination)], input=archive, check=True)
    return tree


def official_python(repo_root: Path) -> tuple[Path, bool]:
    """The project venv interpreter, brief §13: "시스템 Python 실행 결과를 official M12 evidence로
    사용하지 않는다." Falls back to whatever invoked this process only if the venv is genuinely
    absent (e.g. a checkout without it materialized yet), and says so via the second element rather
    than silently passing a non-project interpreter's result off as official."""
    venv_python = repo_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python, True
    return Path(sys.executable), False


def probe(repo_root: Path | None = None, *, python: Path | None = None) -> CleanCheckoutReport:
    repo_root = repo_root or Path(__file__).resolve().parents[3]
    if python is not None:
        interpreter, is_official = python, python == official_python(repo_root)[0]
    else:
        interpreter, is_official = official_python(repo_root)
    with tempfile.TemporaryDirectory(prefix="d4_clean_checkout_") as tmp:
        destination = Path(tmp)
        tree = export_index_tree(repo_root, destination)
        script = (
            "import importlib, json, sys\n"
            f"targets = {list(CLEAN_IMPORT_TARGETS)!r}\n"
            "out = []\n"
            "for name, module in targets:\n"
            "    try:\n"
            "        importlib.import_module(module)\n"
            "        out.append({'name': name, 'module': module, 'ok': True, 'error': ''})\n"
            "    except Exception as error:\n"
            "        out.append({'name': name, 'module': module, 'ok': False,\n"
            "                    'error': type(error).__name__ + ': ' + str(error)})\n"
            "smoke_executed, smoke_ok, smoke_error = False, False, ''\n"
            "if all(item['ok'] for item in out):\n"
            "    smoke_executed = True\n"
            "    try:\n"
            f"        exec(compile({_SMOKE!r}, '<smoke>', 'exec'), {{}})\n"
            "        smoke_ok = True\n"
            "    except Exception as error:\n"
            "        smoke_ok = False\n"
            "        smoke_error = type(error).__name__ + ': ' + str(error)\n"
            "print(json.dumps({'results': out, 'smoke_executed': smoke_executed,"
            " 'smoke_ok': smoke_ok, 'smoke_error': smoke_error}))\n"
        )
        completed = subprocess.run(
            [str(interpreter), "-c", script], cwd=destination, capture_output=True, text=True,
            env={"PYTHONPATH": str(destination / "backend"), "PATH": "/usr/bin:/bin",
                 "HOME": str(destination)},
        )
        if completed.returncode != 0:
            return CleanCheckoutReport(
                tree=tree,
                results=[ImportProbeResult(name, module, False, completed.stderr.strip()[-2000:])
                         for name, module in CLEAN_IMPORT_TARGETS],
                smoke_executed=False, smoke_ok=False, smoke_error="probe subprocess failed",
                interpreter=str(interpreter), official_interpreter=is_official,
            )
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    return CleanCheckoutReport(
        tree=tree,
        results=[ImportProbeResult(**item) for item in payload["results"]],
        smoke_executed=payload["smoke_executed"], smoke_ok=payload["smoke_ok"],
        smoke_error=payload["smoke_error"],
        interpreter=str(interpreter), official_interpreter=is_official,
    )


def main() -> None:
    report = probe()
    print(json.dumps(report.to_dict(), indent=2))


if __name__ == "__main__":
    main()
