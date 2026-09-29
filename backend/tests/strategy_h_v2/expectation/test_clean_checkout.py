"""D4.2 Defect 4: D4 must not need an uncommitted working tree to import.

D4's package imported `valid_evidence_ids` and `ConflictResolution` from two files that carry
uncommitted edits, so a clean checkout of the D4 commit did not import at all. D4.1 did not create
that and D4.2 may not fix it by editing those files - they are the user's unsaved work (brief §14).
So D4 now gets what it needs from a D4-owned committed module, and this file proves two separate
things about that:

  the D4-owned definitions AGREE with the working-tree originals, so routing around them did not
  fork the contract - these tests run in the developer's tree, where both versions exist;

  and the clean-tree import itself, measured by `app.dev.d4_clean_checkout`, which exports the git
  INDEX to a temporary directory and imports there. That probe reports blockers rather than
  asserting success, because there is one left and it is not D4's to remove.
"""

from __future__ import annotations

import shutil

import pytest
from d4_helpers import chunk, package

from app.backtest.strategy_h_v2.expectation.d4_inputs import (
    ConflictResolution,
    D4RepairReason,
    classify_d4_repair_reason,
    package_evidence_ids,
)

pytestmark = pytest.mark.filterwarnings("ignore")

GIT = shutil.which("git")
NEEDS_GIT = pytest.mark.skipif(GIT is None, reason="the clean-checkout probe shells out to git")


# --- the D4-owned definitions do the job --------------------------------------------------------

def test_package_evidence_ids_returns_this_candidates_chunk_ids():
    pkg = package(chunks=[chunk("one", index=0), chunk("two", index=1)])
    assert package_evidence_ids(pkg) == {c.evidence_id for c in pkg.chunks}


def test_package_evidence_ids_excludes_everything_not_in_this_package():
    """The property D3.1 §2.1 relies on: a cross-company citation fails for the same reason an
    invented chunk index does, because the other company's ids are simply not in the set."""
    pkg = package(chunks=[chunk("one", index=0)])
    assert "SEC:999:OTHER:CHUNK:0" not in package_evidence_ids(pkg)


# --- no drift from the working-tree originals ---------------------------------------------------

def test_package_evidence_ids_agrees_with_the_working_tree_original():
    original = pytest.importorskip(
        "app.backtest.strategy_h_v2.research.validate", reason="research validate unavailable")
    if not hasattr(original, "valid_evidence_ids"):
        pytest.skip("valid_evidence_ids is the uncommitted symbol this module exists to replace")
    pkg = package(chunks=[chunk("one", index=0), chunk("two", index=1)])
    assert package_evidence_ids(pkg) == original.valid_evidence_ids(pkg)


def test_conflict_resolution_agrees_with_the_working_tree_original():
    original = pytest.importorskip("app.backtest.strategy_h_v2.research.schema")
    if not hasattr(original, "ConflictResolution"):
        pytest.skip("ConflictResolution is the uncommitted symbol this module exists to replace")
    assert ({c.value for c in ConflictResolution}
            == {c.value for c in original.ConflictResolution})


REPAIR_CORPUS = [
    ["JSON_PARSE_ERROR: expecting value at line 1"],
    ["unknown evidence_id ['SEC:1:A-1:CHUNK:9'] in conflict 'guidance'"],
    ["Field required @ market_expectation_evidence"],
    ["Input should be 'STRENGTHENED', 'WEAKENED' @ direction"],
    ["prohibited investment language 'fair value' @ limitations"],
    ["priced_in_assessment=LIKELY_PRICED requires at least 1 evidence id(s) @ x"],
    ["state does not match the evidence bundle @ fundamental_changes"],
    ["the response hit max_tokens"],
    ["something entirely unclassified"],
]


@pytest.mark.parametrize("errors", REPAIR_CORPUS)
def test_repair_classification_agrees_with_the_working_tree_original(errors):
    """A fork that silently disagreed would make a D4.2 telemetry record unreadable next to a D4.1
    one, which is the only thing the duplication could actually cost."""
    original = pytest.importorskip("app.backtest.strategy_h_v2.research.repair")
    assert (classify_d4_repair_reason(errors).value
            == original.classify_repair_reason(errors).value)


def test_repair_reason_members_match_the_working_tree_original():
    original = pytest.importorskip("app.backtest.strategy_h_v2.research.repair")
    assert ({r.value for r in D4RepairReason} == {r.value for r in original.RepairReason})


# --- D4 no longer names an uncommitted symbol ----------------------------------------------------

def test_the_d4_package_does_not_import_the_uncommitted_symbols():
    """Source-level, because an import that resolves in THIS tree proves nothing about a clean
    one. `Confidence` and `FutureBusinessStage` are still imported from `research.schema` and that
    is fine - both are committed."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[4] / "app/backtest/strategy_h_v2/expectation"
    offenders = []
    for path in sorted(root.glob("*.py")):
        text = path.read_text()
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith(("from ", "import ")):
                continue
            if "research.validate import" in stripped and "valid_evidence_ids" in stripped:
                offenders.append(f"{path.name}: {stripped}")
            if "research.schema import" in stripped and "ConflictResolution" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert offenders == []


# --- the gate itself -------------------------------------------------------------------------------

@NEEDS_GIT
def test_the_clean_checkout_probe_reports_a_real_measurement():
    """The probe must run, must not touch the working tree, and must report per-target results.

    It is NOT asserted to pass. One blocker remains and it is outside D4's package: committed
    `research/schema_v2.py` imports `ConflictResolution` and `_check_source_ids` from
    `research/schema.py`, and only an uncommitted edit defines them. D4.2 cannot repair that
    without modifying a dirty file, which brief §14 forbids. Asserting `passed` here would either
    fail for a reason D4.2 does not own, or tempt someone to weaken the probe until it passed.
    """
    from app.dev.d4_clean_checkout import CLEAN_IMPORT_TARGETS, probe

    report = probe()
    assert len(report.results) == len(CLEAN_IMPORT_TARGETS)
    assert {r.name for r in report.results} == {name for name, _ in CLEAN_IMPORT_TARGETS}


@NEEDS_GIT
def test_the_d4_owned_modules_import_from_a_clean_tree():
    """The part D4.2 does own: its own new modules, plus the gap contract and the evidence builder,
    import with no help from an unstaged edit."""
    from app.dev.d4_clean_checkout import probe

    report = probe()
    by_name = {r.name: r for r in report.results}
    for name in ("d4_contract_v2", "d4_consensus_language", "d4_inputs", "d4_gap_contract",
                 "d4_2_contract", "expectation_evidence_build"):
        assert by_name[name].ok, f"{name} failed to import from a clean tree: {by_name[name].error}"


@NEEDS_GIT
def test_any_remaining_blocker_is_outside_the_d4_package():
    """If a blocker ever appears whose missing symbol is one D4 itself names, this fails - which is
    the regression this whole defect class needs a guard against."""
    from app.dev.d4_clean_checkout import probe

    for blocker in probe().blockers():
        assert "expectation/" not in blocker.error, blocker.error
        assert "research/schema_v2.py" in blocker.error or "research.schema" in blocker.error, (
            f"unexpected clean-checkout blocker: {blocker.name} -> {blocker.error}")
