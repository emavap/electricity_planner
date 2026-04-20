"""Tests for Nord Pool Prices Sensor."""
from __future__ import annotations

import pytest
import pytz
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.util import dt as dt_util

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.helpers import (
    extract_price_from_interval,
    resolve_transport_cost_from_lookup,
)
from custom_components.electricity_planner.sensor import NordPoolPricesSensor


class FakeCoordinator:
    """Fake coordinator for testing."""

    def __init__(self, data=None):
        self.data = data or {}
        self.config = {
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
        self.builtin_transport_cost = 0.0

    @staticmethod
    def async_add_listener(callback):
        """Mock listener."""
        pass

    def _has_builtin_transport_cost(self) -> bool:
        """Check if built-in transport cost components are configured."""
        return self.config.get("transport_cost_day") is not None

    def _resolve_transport_cost(self, transport_lookup, start_time_utc, reference_now=None):
        """Resolve transport cost using built-in or legacy test behavior."""
        if self._has_builtin_transport_cost():
            return self.builtin_transport_cost
        return resolve_transport_cost_from_lookup(
            transport_lookup, start_time_utc, reference_now=reference_now
        )


@pytest.fixture
def fake_coordinator():
    """Provide a fake coordinator."""
    return FakeCoordinator()


@pytest.fixture
def fake_entry():
    """Provide a fake config entry."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={},
        entry_id="test_entry_id"
    )


def test_extract_price_value_handles_price_key():
    """Test that extract_price_from_interval extracts from 'price' key."""
    data = {"price": 104.85}
    assert extract_price_from_interval(data) == 104.85


def test_extract_price_value_handles_value_key():
    """Test that extract_price_from_interval extracts from 'value' key."""
    data = {"value": 104.85}
    assert extract_price_from_interval(data) == 104.85


def test_extract_price_value_handles_value_exc_vat_key():
    """Test that extract_price_from_interval extracts from 'value_exc_vat' key."""
    data = {"value_exc_vat": 104.85}
    assert extract_price_from_interval(data) == 104.85


def test_extract_price_value_handles_string_value():
    """Test that extract_price_from_interval handles string values."""
    data = {"price": "104.85"}
    assert extract_price_from_interval(data) == 104.85


def test_extract_price_value_returns_none_for_invalid():
    """Test that extract_price_from_interval returns None for invalid data."""
    # No recognized keys
    assert extract_price_from_interval({"foo": 104.85}) is None

    # Invalid string
    assert extract_price_from_interval({"price": "not a number"}) is None

    # Empty dict
    assert extract_price_from_interval({}) is None


def test_normalize_price_interval_adds_price_key(fake_coordinator, fake_entry):
    """Test that _normalize_price_interval adds 'price' key and converts to €/kWh."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    interval = {
        "start": "2025-10-14T00:00:00+00:00",
        "end": "2025-10-14T00:15:00+00:00",
        "value": 104.85  # In €/MWh
    }

    # No transport cost lookup provided
    result = sensor._normalize_price_interval(interval, transport_cost_lookup=None)

    assert result is not None
    assert result["price"] == 0.10485  # Converted to €/kWh (divided by 1000)
    assert result["transport_cost"] == 0.0  # No transport cost
    assert result["raw_price"] == 0.10485
    assert result["adjusted_energy_price"] == 0.10485
    assert result["contract_adjustment"] == 0.0
    assert result["start"] == "2025-10-14T00:00:00+00:00"
    assert result["end"] == "2025-10-14T00:15:00+00:00"
    assert "value" in result  # Original key preserved


def test_normalize_price_interval_returns_none_for_invalid(fake_coordinator, fake_entry):
    """Test that _normalize_price_interval returns None for invalid data."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    # Not a dict
    assert sensor._normalize_price_interval("not a dict") is None
    assert sensor._normalize_price_interval(None) is None
    assert sensor._normalize_price_interval([]) is None

    # Missing price data
    assert sensor._normalize_price_interval({"start": "2025-10-14T00:00:00+00:00"}) is None


def test_normalize_price_interval_applies_adjustments(fake_coordinator, fake_entry):
    """Test that _normalize_price_interval applies multiplier and offset."""
    # Configure adjustment: price × 1.21 + 0.05 (e.g., VAT + surcharge)
    fake_coordinator.config = {
        "price_adjustment_multiplier": 1.21,
        "price_adjustment_offset": 0.05
    }
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    interval = {
        "start": "2025-10-14T00:00:00+00:00",
        "end": "2025-10-14T00:15:00+00:00",
        "value": 100.0  # 100 €/MWh = 0.1 €/kWh
    }

    result = sensor._normalize_price_interval(interval, transport_cost_lookup=None)

    assert result is not None
    # Expected: (100/1000 × 1.21) + 0.05 ≈ 0.171
    assert result["price"] == pytest.approx(0.171, rel=1e-6)
    assert result["transport_cost"] == 0.0
    assert result["raw_price"] == pytest.approx(0.1, rel=1e-6)
    assert result["adjusted_energy_price"] == pytest.approx(0.171, rel=1e-6)
    assert result["contract_adjustment"] == pytest.approx(0.071, rel=1e-6)


def test_normalize_price_interval_applies_transport_cost(fake_coordinator, fake_entry, monkeypatch):
    """Test that _normalize_price_interval applies week-old transport costs for future times."""
    from datetime import datetime, timezone

    # Freeze time to 2025-10-13 12:00 UTC so that 2025-10-14 intervals are in the future
    frozen_now = datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dt_util, "utcnow", lambda: frozen_now)

    fake_coordinator.config = {
        "price_adjustment_multiplier": 1.0,
        "price_adjustment_offset": 0.0
    }
    fake_coordinator.data = {}  # Empty data to avoid interference
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    # Transport cost history from a week ago (2025-10-07)
    # 0.03 at night (00:00), 0.05 during day (14:00)
    transport_lookup = [
        {"start": "2025-10-07T00:00:00+00:00", "cost": 0.03},
        {"start": "2025-10-07T14:00:00+00:00", "cost": 0.05},
    ]

    # Future night interval (2025-10-14, should use week-old pattern)
    night_interval = {
        "start": "2025-10-14T00:00:00+00:00",
        "end": "2025-10-14T00:15:00+00:00",
        "value": 100.0  # 100 €/MWh = 0.1 €/kWh
    }

    result_night = sensor._normalize_price_interval(night_interval, transport_lookup)
    assert result_night is not None
    assert result_night["price"] == pytest.approx(0.13, rel=1e-6)  # 0.1 + 0.03
    assert result_night["transport_cost"] == 0.03

    # Future day interval (2025-10-14, should use week-old pattern)
    day_interval = {
        "start": "2025-10-14T14:00:00+00:00",
        "end": "2025-10-14T14:15:00+00:00",
        "value": 100.0  # 100 €/MWh = 0.1 €/kWh
    }

    result_day = sensor._normalize_price_interval(day_interval, transport_lookup)
    assert result_day is not None
    assert result_day["price"] == pytest.approx(0.15, rel=1e-6)  # 0.1 + 0.05
    assert result_day["transport_cost"] == 0.05


def test_normalize_price_interval_uses_local_time_zone(fake_coordinator, fake_entry, monkeypatch):
    """Transport cost lookup should work correctly regardless of timezone."""
    from datetime import datetime, timezone

    # Freeze time to 2025-10-13 12:00 UTC so that 2025-10-14 intervals are in the future
    frozen_now = datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dt_util, "utcnow", lambda: frozen_now)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Rome"))
    try:
        fake_coordinator.config = {
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
        fake_coordinator.data = {}
        sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

        # Future interval (2025-10-14 00:00 UTC)
        interval = {
            "start": "2025-10-14T00:00:00+00:00",
            "end": "2025-10-14T00:15:00+00:00",
            "value": 100.0,
        }

        # Week-old transport cost at the same time
        transport_lookup = [
            {
                "start": "2025-10-07T00:00:00+00:00",
                "cost": 0.04,
            }
        ]

        result = sensor._normalize_price_interval(interval, transport_lookup)

        assert result is not None
        assert result["transport_cost"] == pytest.approx(0.04, rel=1e-6)
        # Final price should be base 0.1 + 0.04 transport
        assert result["price"] == pytest.approx(0.14, rel=1e-6)
    finally:
        dt_util.set_default_time_zone(original_tz)


def test_normalize_price_interval_matches_future_transport_by_local_week(fake_coordinator, fake_entry, monkeypatch):
    """DST transitions should reuse the same local tariff slot, not the same UTC instant."""
    from datetime import datetime, timezone

    frozen_now = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(dt_util, "utcnow", lambda: frozen_now)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))
    try:
        fake_coordinator.config = {
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
        fake_coordinator.data = {}
        sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

        interval = {
            "start": "2026-04-05T00:30:00+00:00",
            "end": "2026-04-05T00:45:00+00:00",
            "value": 100.0,
        }
        transport_lookup = [
            {"start": "2026-03-29T00:00:00+00:00", "cost": 0.02},
            {"start": "2026-03-29T01:00:00+00:00", "cost": 0.07},
        ]

        result = sensor._normalize_price_interval(interval, transport_lookup)

        assert result is not None
        assert result["transport_cost"] == pytest.approx(0.07, rel=1e-6)
        assert result["price"] == pytest.approx(0.17, rel=1e-6)
    finally:
        dt_util.set_default_time_zone(original_tz)


def test_native_value_unavailable_when_no_data(fake_coordinator, fake_entry):
    """Test that sensor shows unavailable when no data."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    assert sensor.native_value == "unavailable"


def test_native_value_shows_today_only(fake_coordinator, fake_entry):
    """Test that sensor shows 'today only' when tomorrow unavailable."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {"start": "2025-10-14T00:00:00+00:00", "end": "2025-10-14T00:15:00+00:00", "price": 104.85},
                {"start": "2025-10-14T00:15:00+00:00", "end": "2025-10-14T00:30:00+00:00", "price": 97.53},
            ]
        },
        "nordpool_prices_tomorrow": None
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    assert sensor.native_value == "today only (2 intervals)"


def test_native_value_shows_today_and_tomorrow(fake_coordinator, fake_entry):
    """Test that sensor shows 'today+tomorrow' when both available."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {"start": "2025-10-14T00:00:00+00:00", "end": "2025-10-14T00:15:00+00:00", "price": 104.85},
            ]
        },
        "nordpool_prices_tomorrow": {
            "BE": [
                {"start": "2025-10-15T00:00:00+00:00", "end": "2025-10-15T00:15:00+00:00", "price": 110.0},
            ]
        }
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    assert sensor.native_value == "today+tomorrow (2 intervals)"


def test_extra_state_attributes_combines_prices(fake_coordinator, fake_entry):
    """Test that extra_state_attributes combines today and tomorrow prices."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {"start": "2025-10-14T10:00:00+00:00", "end": "2025-10-14T10:15:00+00:00", "price": 100.0},  # In €/MWh
            ]
        },
        "nordpool_prices_tomorrow": {
            "BE": [
                {"start": "2025-10-15T10:00:00+00:00", "end": "2025-10-15T10:15:00+00:00", "price": 110.0},  # In €/MWh
            ]
        },
        "transport_cost_lookup": [],
        "transport_cost_status": "not_configured",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    assert len(attrs["data"]) == 2
    assert attrs["data"][0]["price"] == 0.1  # Converted to €/kWh
    assert attrs["data"][1]["price"] == 0.11  # Converted to €/kWh
    assert attrs["today_available"] is True
    assert attrs["tomorrow_available"] is True
    assert attrs["total_intervals"] == 2
    assert attrs["min_price"] == 0.1
    assert attrs["max_price"] == 0.11
    assert attrs["avg_price"] == 0.105
    assert attrs["price_range"] == 0.01
    assert attrs["transport_cost_applied"] is None
    assert attrs["transport_cost_status"] == "not_configured"
    # Recorder-friendly payload: only fields consumed by the dashboard chart
    # are published as attributes so we stay under the 16 KB recorder limit.
    assert set(attrs["data"][0]) == {"start", "price", "raw_price", "transport_cost"}
    assert set(attrs["data"][1]) == {"start", "price", "raw_price", "transport_cost"}


def test_extra_state_attributes_handles_different_price_keys(fake_coordinator, fake_entry):
    """Test that attributes work with different price key formats."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {"start": "2025-10-14T10:00:00+00:00", "end": "2025-10-14T10:15:00+00:00", "value": 100.0},  # In €/MWh
                {"start": "2025-10-14T10:15:00+00:00", "end": "2025-10-14T10:30:00+00:00", "value_exc_vat": 90.0},  # In €/MWh
            ]
        },
        "nordpool_prices_tomorrow": None,
        "transport_cost_lookup": [],
        "transport_cost_status": "pending_history",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    # Both should be normalized to "price" key and converted to €/kWh
    assert len(attrs["data"]) == 2
    assert attrs["data"][0]["price"] == 0.1  # Converted to €/kWh
    assert attrs["data"][1]["price"] == 0.09  # Converted to €/kWh
    assert attrs["min_price"] == 0.09
    assert attrs["max_price"] == 0.1
    assert attrs["transport_cost_applied"] is False
    assert attrs["transport_cost_status"] == "pending_history"


def test_extra_state_attributes_marks_builtin_transport_cost_as_applied(fake_coordinator, fake_entry):
    """Built-in transport cost mode should be reported as applied."""
    fake_coordinator.config.update(
        {
            "transport_cost_day": 0.0599,
            "transport_cost_night": 0.0498,
        }
    )
    fake_coordinator.builtin_transport_cost = 0.127871
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {
                    "start": "2025-10-14T10:00:00+00:00",
                    "end": "2025-10-14T10:15:00+00:00",
                    "value": 100.0,
                },
            ]
        },
        "nordpool_prices_tomorrow": None,
        "transport_cost_lookup": [],
        "transport_cost_status": "builtin",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    assert attrs["transport_cost_applied"] is True
    assert attrs["transport_cost_status"] == "builtin"
    assert attrs["data"][0]["transport_cost"] == pytest.approx(0.127871, rel=1e-6)
    assert attrs["data"][0]["price"] == pytest.approx(0.227871, rel=1e-6)


def test_extra_state_attributes_compacts_interval_payload(fake_coordinator, fake_entry):
    """Only expose the fields the dashboard needs to avoid recorder bloat."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {
                    "start": "2025-10-14T10:00:00+00:00",
                    "end": "2025-10-14T10:15:00+00:00",
                    "value": 100.0,
                    "currency": "EUR",
                    "source": "nordpool",
                    "metadata": {"foo": "bar"},
                },
            ]
        },
        "nordpool_prices_tomorrow": None,
        "transport_cost_lookup": [],
        "transport_cost_status": "not_configured",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    assert attrs["data"] == [
        {
            "start": "2025-10-14T10:00:00+00:00",
            "price": 0.1,
            "raw_price": 0.1,
            "transport_cost": 0.0,
        }
    ]


def test_extra_state_attributes_stays_under_recorder_limit(fake_coordinator, fake_entry):
    """Full 48h of 15-minute Nord Pool intervals must fit under HA's 16 KB
    recorder attribute limit. Regression guard for the ``State attributes
    ... exceed maximum size of 16384 bytes`` Recorder warning observed on
    live installs before the per-interval payload was trimmed.
    """
    import json
    from datetime import datetime, timedelta, timezone

    # Build 192 intervals: 96 per day (15-min) x 2 days.
    today_intervals = []
    tomorrow_intervals = []
    base_today = datetime(2025, 10, 14, 0, 0, tzinfo=timezone.utc)
    base_tomorrow = datetime(2025, 10, 15, 0, 0, tzinfo=timezone.utc)
    for i in range(96):
        start_t = base_today + timedelta(minutes=15 * i)
        end_t = start_t + timedelta(minutes=15)
        today_intervals.append(
            {
                "start": start_t.isoformat(),
                "end": end_t.isoformat(),
                "price": 100.0 + i * 0.5,  # €/MWh
            }
        )
        start_tm = base_tomorrow + timedelta(minutes=15 * i)
        end_tm = start_tm + timedelta(minutes=15)
        tomorrow_intervals.append(
            {
                "start": start_tm.isoformat(),
                "end": end_tm.isoformat(),
                "price": 120.0 + i * 0.5,
            }
        )

    fake_coordinator.data = {
        "nordpool_prices_today": {"BE": today_intervals},
        "nordpool_prices_tomorrow": {"BE": tomorrow_intervals},
        "transport_cost_lookup": [],
        "transport_cost_status": "not_configured",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    payload = json.dumps(attrs)
    assert len(payload) < 16384, (
        f"Attribute payload is {len(payload)} bytes - exceeds the 16 KB "
        "recorder limit. Trim per-interval fields in _compact_price_interval."
    )
    # Sanity: the dashboard-critical fields are still present so the chart
    # keeps working.
    assert attrs["data"], "At least one interval must survive the history trim"
    for sample in attrs["data"][:3]:
        assert set(sample) == {"start", "price", "raw_price", "transport_cost"}


def test_extra_state_attributes_skips_invalid_intervals(fake_coordinator, fake_entry):
    """Test that invalid intervals are skipped gracefully."""
    fake_coordinator.data = {
        "nordpool_prices_today": {
            "BE": [
                {"start": "2025-10-14T10:00:00+00:00", "end": "2025-10-14T10:15:00+00:00", "price": 100.0},  # In €/MWh
                {"start": "2025-10-14T10:15:00+00:00", "end": "2025-10-14T10:30:00+00:00"},  # Missing price
                {"start": "2025-10-14T10:30:00+00:00", "end": "2025-10-14T10:45:00+00:00", "price": 110.0},  # In €/MWh
            ]
        },
        "nordpool_prices_tomorrow": None,
        "transport_cost_lookup": [],
        "transport_cost_status": "not_configured",
    }

    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")
    attrs = sensor.extra_state_attributes

    # Should only include valid intervals, converted to €/kWh
    assert len(attrs["data"]) == 2
    assert attrs["data"][0]["price"] == 0.1  # Converted to €/kWh
    assert attrs["data"][1]["price"] == 0.11  # Converted to €/kWh
    assert attrs["total_intervals"] == 2
    assert attrs["transport_cost_applied"] is None
