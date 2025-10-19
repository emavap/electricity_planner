"""Edge case tests for three-phase electrical system support."""
from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASES,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    DOMAIN,
    PHASE_MODE_THREE,
)


class FakeState:
    def __init__(self, state: str):
        self.state = state


class FakeStates:
    def __init__(self):
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, value: str) -> None:
        self._states[entity_id] = FakeState(str(value))

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self):
        self.states = FakeStates()
        self.data: dict = {}


@pytest.fixture
def fake_hass():
    return FakeHass()


def _create_coordinator(fake_hass, config, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    return coordinator


@pytest.mark.asyncio
async def test_three_phase_with_all_sensors_none(fake_hass, monkeypatch):
    """Test three-phase mode when no sensors are configured (all None)."""
    config = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: [],
        CONF_PHASE_MODE: PHASE_MODE_THREE,
        CONF_PHASES: {},  # Empty phases config
    }

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set price sensors
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")

    data = await coordinator._fetch_all_data()

    # Should handle gracefully
    assert data["phase_mode"] == PHASE_MODE_THREE
    assert data.get("phase_details") == {} or data.get("phase_details") is None
    assert data.get("solar_production") is None
    assert data.get("house_consumption") is None


@pytest.mark.asyncio
async def test_three_phase_with_only_battery_power_sensors(fake_hass, monkeypatch):
    """Test three-phase mode with only battery power sensors configured."""
    config = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc_1"],
        CONF_PHASE_MODE: PHASE_MODE_THREE,
        CONF_PHASES: {
            "phase_1": {
                "name": "Phase 1",
                "solar_entity": None,
                "consumption_entity": None,
                "battery_power_entity": "sensor.battery_power_l1",
            }
        },
        CONF_BATTERY_CAPACITIES: {"sensor.battery_soc_1": 10.0},
        CONF_BATTERY_PHASE_ASSIGNMENTS: {"sensor.battery_soc_1": ["phase_1"]},
    }

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set sensors
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")
    fake_hass.states.set("sensor.battery_soc_1", "50")
    fake_hass.states.set("sensor.battery_power_l1", "-1000")  # Charging

    data = await coordinator._fetch_all_data()

    # Should have phase details with battery power
    phase_details = data.get("phase_details", {})
    assert "phase_1" in phase_details
    assert phase_details["phase_1"]["battery_power"] == pytest.approx(-1000.0)
    assert phase_details["phase_1"]["solar_production"] is None
    assert phase_details["phase_1"]["house_consumption"] is None


@pytest.mark.asyncio
async def test_three_phase_mixed_configuration(fake_hass, monkeypatch):
    """Test three-phase mode with mixed configurations across phases."""
    config = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc_1"],
        CONF_PHASE_MODE: PHASE_MODE_THREE,
        CONF_PHASES: {
            # Phase 1: Full configuration
            "phase_1": {
                "name": "Phase 1",
                "solar_entity": "sensor.solar_l1",
                "consumption_entity": "sensor.load_l1",
                "car_entity": "sensor.car_l1",
                "battery_power_entity": "sensor.battery_power_l1",
            },
            # Phase 2: Only consumption (minimal viable)
            "phase_2": {
                "name": "Phase 2",
                "solar_entity": None,
                "consumption_entity": "sensor.load_l2",
            },
            # Phase 3: Not configured (would not exist after config flow fix)
        },
        CONF_BATTERY_CAPACITIES: {"sensor.battery_soc_1": 10.0},
        CONF_BATTERY_PHASE_ASSIGNMENTS: {"sensor.battery_soc_1": ["phase_1"]},
    }

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set all sensors
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")
    fake_hass.states.set("sensor.battery_soc_1", "50")
    fake_hass.states.set("sensor.solar_l1", "2000")
    fake_hass.states.set("sensor.load_l1", "1500")
    fake_hass.states.set("sensor.car_l1", "500")
    fake_hass.states.set("sensor.battery_power_l1", "-300")
    fake_hass.states.set("sensor.load_l2", "800")

    data = await coordinator._fetch_all_data()

    # Should have both configured phases
    phase_details = data.get("phase_details", {})
    assert "phase_1" in phase_details
    assert "phase_2" in phase_details
    assert "phase_3" not in phase_details

    # Phase 1 should have all values
    assert phase_details["phase_1"]["solar_production"] == pytest.approx(2000.0)
    assert phase_details["phase_1"]["house_consumption"] == pytest.approx(1500.0)
    assert phase_details["phase_1"]["car_charging_power"] == pytest.approx(500.0)
    assert phase_details["phase_1"]["battery_power"] == pytest.approx(-300.0)

    # Phase 2 should have only consumption
    assert phase_details["phase_2"]["solar_production"] is None
    assert phase_details["phase_2"]["house_consumption"] == pytest.approx(800.0)
    assert phase_details["phase_2"]["car_charging_power"] is None
    assert phase_details["phase_2"]["battery_power"] is None

    # Aggregated totals should still work
    assert data["solar_production"] == pytest.approx(2000.0)
    assert data["house_consumption"] == pytest.approx(2300.0)


@pytest.mark.asyncio
async def test_three_phase_partial_solar_surplus_calculation(fake_hass, monkeypatch):
    """Test solar surplus calculation when only some phases have solar."""
    config = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: [],
        CONF_PHASE_MODE: PHASE_MODE_THREE,
        CONF_PHASES: {
            "phase_1": {
                "name": "Phase 1",
                "solar_entity": "sensor.solar_l1",
                "consumption_entity": "sensor.load_l1",
            },
            "phase_2": {
                "name": "Phase 2",
                "solar_entity": None,  # No solar
                "consumption_entity": "sensor.load_l2",
            },
        },
    }

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")
    fake_hass.states.set("sensor.solar_l1", "3000")
    fake_hass.states.set("sensor.load_l1", "1000")
    fake_hass.states.set("sensor.load_l2", "500")

    data = await coordinator._fetch_all_data()

    phase_details = data.get("phase_details", {})

    # Phase 1 should have surplus
    assert phase_details["phase_1"]["solar_surplus"] == pytest.approx(2000.0)

    # Phase 2 should have None surplus (no solar)
    assert phase_details["phase_2"]["solar_surplus"] is None or phase_details["phase_2"]["solar_surplus"] == 0

    # Overall surplus should be calculated correctly
    assert data["solar_surplus"] == pytest.approx(1500.0)  # 3000 - (1000 + 500)
