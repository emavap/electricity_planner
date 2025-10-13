"""Tests for Nord Pool Prices Sensor."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.sensor import NordPoolPricesSensor


class FakeCoordinator:
    """Fake coordinator for testing."""

    def __init__(self, data=None):
        self.data = data or {}
        self.config = {}

    @staticmethod
    def async_add_listener(callback):
        """Mock listener."""
        pass


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


def test_extract_price_value_handles_price_key(fake_coordinator, fake_entry):
    """Test that _extract_price_value extracts from 'price' key."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    data = {"price": 104.85}
    assert sensor._extract_price_value(data) == 104.85


def test_extract_price_value_handles_value_key(fake_coordinator, fake_entry):
    """Test that _extract_price_value extracts from 'value' key."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    data = {"value": 104.85}
    assert sensor._extract_price_value(data) == 104.85


def test_extract_price_value_handles_value_exc_vat_key(fake_coordinator, fake_entry):
    """Test that _extract_price_value extracts from 'value_exc_vat' key."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    data = {"value_exc_vat": 104.85}
    assert sensor._extract_price_value(data) == 104.85


def test_extract_price_value_handles_string_value(fake_coordinator, fake_entry):
    """Test that _extract_price_value handles string values."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    data = {"price": "104.85"}
    assert sensor._extract_price_value(data) == 104.85


def test_extract_price_value_returns_none_for_invalid(fake_coordinator, fake_entry):
    """Test that _extract_price_value returns None for invalid data."""
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    # No recognized keys
    assert sensor._extract_price_value({"foo": 104.85}) is None

    # Invalid string
    assert sensor._extract_price_value({"price": "not a number"}) is None

    # Empty dict
    assert sensor._extract_price_value({}) is None


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


def test_normalize_price_interval_applies_transport_cost(fake_coordinator, fake_entry):
    """Test that _normalize_price_interval applies transport cost based on hour."""
    fake_coordinator.config = {
        "price_adjustment_multiplier": 1.0,
        "price_adjustment_offset": 0.0
    }
    sensor = NordPoolPricesSensor(fake_coordinator, fake_entry, "_diagnostic")

    # Transport cost: 0.03 at night (00:00), 0.05 during day (14:00)
    transport_lookup = {
        0: 0.03,  # Night rate
        14: 0.05  # Day rate
    }

    # Night interval
    night_interval = {
        "start": "2025-10-14T00:00:00+00:00",
        "end": "2025-10-14T00:15:00+00:00",
        "value": 100.0  # 100 €/MWh = 0.1 €/kWh
    }

    result_night = sensor._normalize_price_interval(night_interval, transport_lookup)
    assert result_night is not None
    assert result_night["price"] == pytest.approx(0.13, rel=1e-6)  # 0.1 + 0.03
    assert result_night["transport_cost"] == 0.03

    # Day interval
    day_interval = {
        "start": "2025-10-14T14:00:00+00:00",
        "end": "2025-10-14T14:15:00+00:00",
        "value": 100.0  # 100 €/MWh = 0.1 €/kWh
    }

    result_day = sensor._normalize_price_interval(day_interval, transport_lookup)
    assert result_day is not None
    assert result_day["price"] == pytest.approx(0.15, rel=1e-6)  # 0.1 + 0.05
    assert result_day["transport_cost"] == 0.05


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
        "transport_cost_lookup": {},
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
        "transport_cost_lookup": {},
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
        "transport_cost_lookup": {},
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
