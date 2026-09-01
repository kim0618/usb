from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.core.exceptions import DataError
from app.core.timezone import convert_timezone


def test_et_to_kst_conversion_tracks_dst() -> None:
    winter = datetime(2024, 1, 3, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    summer = datetime(2024, 7, 3, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert convert_timezone(winter, "Asia/Seoul").hour == 23
    assert convert_timezone(summer, "Asia/Seoul").hour == 22


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(DataError):
        convert_timezone(datetime(2024, 1, 3, 9, 30), "Asia/Seoul")

