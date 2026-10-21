"""Frozen package integrity tests, independent of local market-data stores."""
import shutil

import pytest

from app.strategies.i_paper import view as V


def test_package_is_runtime_owned_and_hash_pinned():
    assert V.RESEARCH == __import__('pathlib').Path(V.__file__).resolve().parent / 'frozen'
    V.verify_package()


@pytest.mark.parametrize('name', ['manifest.json', 'FACTOR_V1_IMPLEMENTATION_CONTRACT.json',
                                  'implementation_census_v1.py',
                                  'risk_overlay_d17r_exec/inputs/fund_sic.parquet'])
def test_tampered_package_refused_before_execution(tmp_path, monkeypatch, name):
    root = tmp_path / 'frozen'
    shutil.copytree(V.RESEARCH, root)
    monkeypatch.setattr(V, 'RESEARCH', root)
    with (root / name).open('ab') as f:
        f.write(b'tampered')
    with pytest.raises(V.ViewError, match='changed'):
        V.verify_package()
