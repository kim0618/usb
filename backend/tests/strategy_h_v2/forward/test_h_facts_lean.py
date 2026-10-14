"""The SCAN's lean companyfacts reader keeps exactly what ``extract_companyfacts`` reads, so the canonical
facts are identical to the full-DOM path (strategy output unchanged, lower peak)."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from app.backtest.strategy_eqm_v0 import xbrl_store
from app.backtest.strategy_h0.facts import FIELD_SPECS, extract_companyfacts
from app.strategies.h_auto.facts_cache import read_facts_lean

TAGS = {t for spec in FIELD_SPECS.values() for t in spec.tags}
REPO = Path(__file__).resolve().parents[4]
D1_ROOT = REPO / "data/runtime/strategy_h_v2/d1_1/sec_raw"


def _write(root: Path, cik: str, body: str) -> None:
    path = xbrl_store.facts_path(root, cik)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body.encode()))
    xbrl_store.ledger_path(path).write_text("{}")


def test_lean_reader_keeps_only_listed_concepts_and_every_top_level_member(tmp_path):
    tag = sorted(TAGS)[0]
    doc = {"cik": 1, "entityName": "X é", "facts": {
        "dei": {"EntityPublicFloatX": {"units": {"USD": [{"val": 1}]}}},
        "us-gaap": {tag: {"label": "a \"q\"", "units": {"USD": [{"val": 2, "accn": "A"}]}},
                    "Unrelated": {"units": {"USD": [{"val": 3}]}}},
        "empty": {}}}
    _write(tmp_path, "0000000001", json.dumps(doc, indent=2))       # whitespace exercised
    lean = read_facts_lean(tmp_path, "0000000001", TAGS)
    assert lean["cik"] == 1 and lean["entityName"] == "X é"
    assert lean["facts"] == {"dei": {}, "us-gaap": {tag: doc["facts"]["us-gaap"][tag]}, "empty": {}}
    assert read_facts_lean(tmp_path, "0000000002", TAGS) is None


@pytest.mark.skipif(not (D1_ROOT / "companyfacts").is_dir(), reason="research companyfacts store not on this host")
def test_lean_and_full_give_identical_canonical_facts_on_the_largest_stored_documents():
    from app.backtest.strategy_h0.pilot import acceptance_index
    files = sorted((D1_ROOT / "companyfacts").glob("CIK*.json.gz"), key=lambda p: -p.stat().st_size)
    checked = 0
    for path in files[:40]:
        cik = path.name[3:13]
        sub = D1_ROOT / "submissions" / f"CIK{cik}" / f"CIK{cik}.json.gz"
        if not sub.exists() or not xbrl_store.ledger_path(path).exists():
            continue
        accepted = acceptance_index(json.loads(gzip.decompress(sub.read_bytes())))
        assert extract_companyfacts(read_facts_lean(D1_ROOT, cik, TAGS), accepted) == \
            extract_companyfacts(xbrl_store.read_facts(D1_ROOT, cik), accepted), cik
        checked += 1
    assert checked >= 10
