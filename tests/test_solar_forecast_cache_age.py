"""Solar forecast cache must refer to the charging day's forecast."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.electricity_planner.solar_forecast import SolarForecastService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cache_age,expected", [(0, None), (1, 14.0), (2, None), (3, None)]
)
async def test_overnight_forecast_cache_age(cache_age, expected, monkeypatch):
    now = datetime(2026, 10, 24, 3, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "custom_components.electricity_planner.solar_forecast.dt_util.now", lambda: now
    )
    coordinator = SimpleNamespace(
        config={"solar_forecast_start_hour": 20},
        _cached_solar_forecast=14.0,
        _solar_forecast_cache_date=now.date() - timedelta(days=cache_age),
        _solar_forecast_cache_hour=21,
        _get_state_value=AsyncMock(return_value=None),
    )
    assert await SolarForecastService(coordinator).resolve() == expected
    assert coordinator._solar_forecast_source == (
        "overnight_cache" if cache_age == 1 else "none_before_start"
    )
    assert coordinator._cached_solar_forecast == expected
