from datetime import datetime, timezone

from app.backtest.strategy_h0.pilot import acceptance_index, deterministic_ciks


def test_acceptance_index_requires_submission_knowledge_time() -> None:
    document = {"filings": {"recent": {
        "accessionNumber": ["one", "two"],
        "acceptanceDateTime": ["2023-05-01T20:00:00+00:00", ""],
    }}}
    assert acceptance_index(document) == {
        "one": datetime(2023, 5, 1, 20, tzinfo=timezone.utc),
    }


def test_deterministic_pilot_selection(tmp_path) -> None:
    for number in range(35):
        folder = tmp_path / f"CIK{number:010d}"
        folder.mkdir()
        (folder / f"CIK{number:010d}.json.gz").touch()
    selected = deterministic_ciks(tmp_path, 30)
    assert selected == [f"{number:010d}" for number in range(30)]
    assert deterministic_ciks(tmp_path, 30) == selected
