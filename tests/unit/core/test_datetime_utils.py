from datetime import datetime, timezone, timedelta

import pytest

from app.core import datetime_utils as dt_utils


pytestmark = pytest.mark.asyncio


async def test_get_now_returns_beijing_timezone():
    now = dt_utils.get_now()

    assert now.tzinfo == dt_utils.BEIJING_TZ


async def test_get_now_naive_strips_timezone():
    now = dt_utils.get_now_naive()

    assert now.tzinfo is None


async def test_convert_to_beijing_time_handles_none_and_existing_beijing():
    assert dt_utils.convert_to_beijing_time(None) is None

    beijing_dt = datetime(2026, 5, 27, 12, 0, tzinfo=dt_utils.BEIJING_TZ)
    result = dt_utils.convert_to_beijing_time(beijing_dt)

    assert result == datetime(2026, 5, 27, 12, 0)


async def test_convert_to_beijing_time_converts_naive_and_utc_datetimes():
    naive_dt = datetime(2026, 5, 27, 8, 0)
    utc_dt = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)

    naive_result = dt_utils.convert_to_beijing_time(naive_dt)
    utc_result = dt_utils.convert_to_beijing_time(utc_dt)

    assert naive_result == datetime(2026, 5, 27, 16, 0)
    assert utc_result == datetime(2026, 5, 27, 8, 0)


async def test_parse_datetime_to_beijing_naive_supports_naive_and_timezone_aware_strings():
    assert dt_utils.parse_datetime_to_beijing_naive("2026-05-27T09:30:00") == datetime(2026, 5, 27, 9, 30)
    assert dt_utils.parse_datetime_to_beijing_naive("2026-05-27T01:30:00+00:00") == datetime(2026, 5, 27, 9, 30)


async def test_utc_to_beijing_and_today_helpers(monkeypatch):
    fixed_now = datetime(2026, 5, 27, 10, 15, 30)
    monkeypatch.setattr(dt_utils, "get_now_naive", lambda: fixed_now)

    assert dt_utils.utc_to_beijing(datetime(2026, 5, 27, 2, 15, tzinfo=timezone.utc)) == datetime(2026, 5, 27, 10, 15)
    assert dt_utils.get_today() == fixed_now.date()
    assert dt_utils.beijing_now_for_model() == fixed_now