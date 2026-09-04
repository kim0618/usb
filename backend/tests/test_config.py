from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, Settings


def test_config_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_name == "usb"
    assert settings.app_env == "development"
    assert settings.market_timezone == "America/New_York"
    assert settings.resolved_database_url.startswith("sqlite:////")
    assert settings.resolved_data_dir.is_absolute()


def test_config_loads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("APP_NAME", "usb-test")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    settings = Settings(_env_file=None)
    assert settings.app_name == "usb-test"
    assert settings.resolved_data_dir == tmp_path


def test_config_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, market_timezone="Not/A_Timezone")


def test_real_market_operator_profile_selects_review_database() -> None:
    settings = Settings(
        _env_file=None,
        runtime_profile="real_market_operator",
        market_data_provider="kiwoom",
        broker_provider="simulation",
        kiwoom_mode="market_data_only",
    )

    assert settings.resolved_database_url == (
        f"sqlite:///{PROJECT_ROOT / 'data/runtime/usb_real_market_review.sqlite3'}"
    )


def test_real_market_operator_profile_requires_kiwoom_market_data() -> None:
    with pytest.raises(ValidationError, match="real_market_operator requires"):
        Settings(_env_file=None, runtime_profile="real_market_operator")
