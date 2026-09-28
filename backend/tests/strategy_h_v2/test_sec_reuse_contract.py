"""D1.1's acquisition script does not add any new HTTP logic; it only decides which CIKs to ask
for and reuses `strategy_c_e0.sec_store` / `strategy_eqm_v0.xbrl_store` unmodified. These tests
exercise, offline (no network), the two guarantees D1.1 depends on from that reused code: an
already-cached CIK is never re-requested, and a failed/missing fetch never fabricates a value.
"""

from __future__ import annotations

from pathlib import Path

from app.backtest.strategy_c_e0.sec_store import ledger_path
from app.backtest.strategy_eqm_v0.xbrl_store import fetch_cik, facts_path, read_facts


def test_cached_companyfacts_is_never_refetched(tmp_path: Path):
    cik = "0000000001"
    path = facts_path(tmp_path, cik)
    path.parent.mkdir(parents=True, exist_ok=True)
    import gzip
    with gzip.open(path, "wb") as handle:
        handle.write(b'{"facts": {}}')
    ledger_path(path).write_text('{"status": 200}')

    # `client=None` would crash if this path actually tried to make an HTTP request; the CACHED
    # short-circuit must fire before the client is ever touched.
    outcome = fetch_cik(None, tmp_path, cik)
    assert outcome == "CACHED"


def test_missing_local_companyfacts_reads_back_as_none_not_a_fabricated_value(tmp_path: Path):
    assert read_facts(tmp_path, "0000000002") is None


def test_uncached_cik_with_no_client_is_reported_missing_not_an_error(tmp_path: Path):
    """A rerun with no client configured (e.g. a dry pass) must report an absent cache honestly as
    MISSING, never raise, and never write a fabricated placeholder file."""
    outcome = fetch_cik(None, tmp_path, "0000000003")
    assert outcome == "MISSING"
    assert not facts_path(tmp_path, "0000000003").exists()
