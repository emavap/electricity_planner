"""Unit tests for helper utilities used by Electricity Planner."""
from datetime import datetime, timezone

import pytest

from custom_components.electricity_planner import helpers


class TestDataValidator:
    """Tests for DataValidator helper."""

    def test_validate_power_value_clamps_bounds(self):
        validator = helpers.DataValidator()

        assert validator.validate_power_value(-50, min_value=0, name="test") == 0
        assert validator.validate_power_value(5000, max_value=3000, name="test") == 3000
        assert validator.validate_power_value(1500, min_value=0, max_value=3000, name="test") == 1500

    def test_validate_battery_data_counts_valid(self, caplog):
        validator = helpers.DataValidator()

        ok, reason = validator.validate_battery_data([])
        assert ok is False
        assert "No battery entities" in reason

        batteries = [
            {"soc": None},
            {"soc": -5},
            {"soc": 45},
            {"soc": 110},
        ]
        with caplog.at_level("WARNING"):
            ok, reason = validator.validate_battery_data(batteries)

        assert ok is True
        assert reason.startswith("1/")
        assert "battery sensors unavailable" in caplog.text

    def test_sanitize_config_value_handles_invalid(self, caplog):
        validator = helpers.DataValidator()

        assert validator.sanitize_config_value("10", 0, 20, 5, name="test") == 10.0
        assert validator.sanitize_config_value(50, 0, 40, 5, name="test") == 5
        with caplog.at_level("ERROR"):
            assert validator.sanitize_config_value("bad", 0, 10, 3, name="test") == 3
        assert validator.is_valid_state(10)
        assert not validator.is_valid_state(None)
        assert not validator.is_valid_state("")


class TestPriceCalculator:
    """Tests for price analysis helpers."""

    def test_calculate_price_position_handles_flat_range(self):
        calc = helpers.PriceCalculator()
        assert calc.calculate_price_position(0.10, 0.10, 0.10) == pytest.approx(0.5)

    def test_calculate_price_position_normalises(self):
        calc = helpers.PriceCalculator()
        pos = calc.calculate_price_position(0.12, 0.20, 0.05)
        assert pos == pytest.approx((0.12 - 0.05) / (0.20 - 0.05))

    @pytest.mark.parametrize(
        ("current", "next_price", "expected"),
        [
            (0.20, 0.15, True),
            (0.20, 0.19, False),
            (0.20, None, False),
            (0, 0.01, False),
        ],
    )
    def test_is_significant_price_drop(self, current, next_price, expected):
        assert (
            helpers.PriceCalculator.is_significant_price_drop(current, next_price, 0.1)
            == expected
        )


class TestTimeHelpers:
    """Tests for time helpers."""

    def test_time_context_uses_monkeypatched_now(self, monkeypatch):
        fake_now = datetime(2024, 1, 1, 11, 30, tzinfo=timezone.utc)
        monkeypatch.setattr(helpers.dt_util, "now", lambda: fake_now)

        context = helpers.TimeContext.get_current_context()

        assert context["current_hour"] == 11
        assert "timestamp" in context


class TestPowerAllocationValidator:
    """Tests for power allocation validation."""

    def test_allocation_checks_limits(self):
        validator = helpers.PowerAllocationValidator()
        is_valid, reason = validator.validate_allocation(
            {
                "solar_for_batteries": 4000,
                "solar_for_car": 0,
                "car_current_solar_usage": 0,
                "total_allocated": 4000,
            },
            available_solar=5000,
            max_battery_power=3000,
            max_car_power=7000,
        )
        assert is_valid is False
        assert "exceeds limit" in reason

        is_valid, reason = validator.validate_allocation(
            {
                "solar_for_batteries": 2000,
                "solar_for_car": 1500,
                "car_current_solar_usage": 300,
                "total_allocated": 3800,
            },
            available_solar=4000,
            max_battery_power=3000,
            max_car_power=7000,
        )
        assert is_valid is True
        assert reason is None

    def test_allocation_detects_mismatched_total(self):
        validator = helpers.PowerAllocationValidator()
        is_valid, reason = validator.validate_allocation(
            {
                "solar_for_batteries": 1000,
                "solar_for_car": 500,
                "car_current_solar_usage": 200,
                "total_allocated": 1200,
            },
            available_solar=3000,
            max_battery_power=3000,
            max_car_power=7000,
        )
        assert is_valid is False
        assert "mismatch" in reason


def test_apply_price_adjustment():
    assert helpers.apply_price_adjustment(0.10, 1.12, 0.008) == pytest.approx(0.10 * 1.12 + 0.008)
    assert helpers.apply_price_adjustment(None, 1.1, 0.0) is None


def test_format_reason_formats_details():
    reason = helpers.format_reason(
        "Charge",
        "threshold met",
        {
            "current_price": 0.1234,
            "battery_soc": 48.6,
            "solar_surplus": 1523.2,
            "note": "ok",
        },
    )

    assert reason.startswith("Charge: threshold met")
    assert "current_price=0.123€/kWh" in reason
    assert "battery_soc=49%" in reason
    assert "solar_surplus=1523W" in reason
    assert "note=ok" in reason


def test_format_reason_with_numeric_details():
    reason = helpers.format_reason(
        "Charge Batteries",
        "Low price detected",
        {"price": 0.075, "battery_soc": 45.6, "surplus": 1234.4, "note": "ok"},
    )
    assert "Charge Batteries: Low price detected" in reason
    assert "price=0.075€/kWh" in reason
    assert "battery_soc=46%" in reason
    assert "surplus=1234W" in reason
    assert "note=ok" in reason
