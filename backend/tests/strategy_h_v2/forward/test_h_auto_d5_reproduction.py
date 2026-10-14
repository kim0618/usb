"""The autonomous executor's price panel and D5 binding reproduce the launch valuation exactly.

Runs on the real local research data (the HIST-V1 mirror, the D2.1 launch packages and the
fundamentals roots) and skips where they are absent, e.g. on the production host. FG and VRRM are
the first refresh batch; both must come out of ``inputs.load_panel`` + ``inputs.d5_binding`` +
``build_panel`` + ``value_issuer(D5_D2R_V1)`` byte-identical to the stored D5-D2R artifact rows.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.strategies.h_auto import inputs as IN

REPO = IN.REPO_ROOT
PACKAGES = REPO / "data/runtime/strategy_h_v2/d2_1/D2_1-20260928T072430Z/packages"

pytestmark = pytest.mark.skipif(not (IN.MIRROR_DIR.is_dir() and PACKAGES.is_dir()),
                                reason="local research data (mirror, D2.1 packages) not present")


@pytest.fixture(scope="module")
def panel():
    return IN.load_panel(["FG", "VRRM"], date(2026, 9, 16), use_d7=False)


def test_panel_matches_the_published_loader(panel, monkeypatch):
    monkeypatch.chdir(REPO)
    from app.dev.run_strategy_h_v2_d4_1 import load_price_panel
    old = load_price_panel(["FG", "VRRM"])
    for ticker in ("FG", "VRRM", "SPY"):
        assert panel.closes(ticker) == dict(sorted(old[ticker].items()))


@pytest.mark.parametrize("ticker", ["FG", "VRRM"])
def test_bound_d5_reproduces_launch_row(panel, ticker, monkeypatch):
    monkeypatch.chdir(REPO)
    from app.backtest.strategy_h_v2.valuation.fair_value import WindowSelectionContract
    from app.dev import audit_strategy_h_v2_d5_p0_1 as P0
    from app.dev.run_h_v2_d7 import _sha, latest_d2r_artifact
    from app.dev.run_strategy_h_v2_d5_d2 import build_panel, value_issuer
    from app.dev.run_strategy_h_v2_d6 import METHOD_JUDGEMENTS

    artifact = json.loads((REPO / latest_d2r_artifact()).read_text())
    with IN.d5_binding(packages_dir=PACKAGES, panel=panel,
                       facts_roots=[REPO / r for r in P0.FACTS_ROOTS],
                       submission_roots=[REPO / r for r in P0.SUBMISSION_ROOTS]):
        observed, row, _ = build_panel(ticker, panel.closes(ticker), date(2026, 9, 16))
        valued = value_issuer(ticker, observed, row, judgements=METHOD_JUDGEMENTS,
                              window_contract=WindowSelectionContract.D5_D2R_V1)
    assert _sha(valued) == _sha(artifact["rows_new"][ticker])
    from app.dev import audit_strategy_h_v2_d5_p1 as P1
    assert P1.FACTS_ROOTS == P0.FACTS_ROOTS          # the binding restored every name it touched


def test_price_base_replaces_the_mirror_on_a_host_without_it(panel, tmp_path, monkeypatch):
    """trader-j has no HIST-V1 mirror: the pinned cohort extraction must give the same panel."""
    monkeypatch.setenv("STRATEGY_H_FORWARD_DIR", str(tmp_path / "d7"))
    built = IN.build_price_base(["FG", "VRRM"])
    assert built["last_session"] == "2026-09-16"
    hostlike = IN.load_panel(["FG", "VRRM"], date(2026, 9, 16), mirror=tmp_path / "no-mirror",
                             grouped=tmp_path / "no-grouped", use_d7=False)
    for ticker in ("FG", "VRRM", "SPY"):
        assert hostlike.closes(ticker) == panel.closes(ticker)
    (tmp_path / "d7/auto/price_base/H_PRICE_BASE_V1.json.sha256").write_text("0" * 64 + "\n")
    with pytest.raises(IN.InputsNotReady, match="PRICE_BASE_CHECKSUM_MISMATCH"):
        IN.load_panel(["FG"], date(2026, 9, 16), mirror=tmp_path / "no-mirror", grouped=tmp_path / "x", use_d7=False)
