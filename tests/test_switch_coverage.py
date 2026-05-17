"""Additional coverage for Electricity Planner switches."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.switch import (
    ArbitrageModeSwitch,
    BatteryChargingDisableSwitch,
    CarPermissiveModeSwitch,
    NegativeArbitrageBuyModeSwitch,
)


class _Coordinator(SimpleNamespace):
    def __init__(self, *, config=None, data=None):
        super().__init__(
            config=config or {},
            data=data or {},
            async_add_listener=lambda update_callback, context=None: lambda: None,
            _car_permissive_mode_active=False,
            _car_permissive_mode_has_persisted_state=False,
        )
        self.refreshes = 0
        self.calls = []
        self.manual_override = None
        self.arbitrage_state = None
        self.negative_state = None

    async def async_request_refresh(self):
        self.refreshes += 1

    async def async_set_car_permissive_mode(self, **kwargs):
        self.calls.append(("set_car", kwargs))
        self._car_permissive_mode_active = True

    async def async_clear_car_permissive_mode(self, **kwargs):
        self.calls.append(("clear_car", kwargs))
        self._car_permissive_mode_active = False

    def get_manual_override(self, target):
        return self.manual_override if target == "battery_grid_charging" else None

    async def async_set_manual_override(self, **kwargs):
        self.calls.append(("set_manual", kwargs))
        self.manual_override = {"value": kwargs["value"], "reason": kwargs["reason"]}

    async def async_clear_manual_override(self, **kwargs):
        self.calls.append(("clear_manual", kwargs))
        self.manual_override = None

    def is_arbitrage_mode_enabled(self):
        return self.arbitrage_state is not None

    def get_arbitrage_mode_state(self):
        return self.arbitrage_state

    async def async_set_arbitrage_mode(self, **kwargs):
        self.calls.append(("set_arbitrage", kwargs))
        self.arbitrage_state = {"reason": kwargs["reason"]}

    async def async_clear_arbitrage_mode(self):
        self.calls.append(("clear_arbitrage", {}))
        self.arbitrage_state = None

    def is_negative_buy_mode_enabled(self):
        return self.negative_state is not None

    def get_negative_buy_mode_state(self):
        return self.negative_state

    async def async_set_negative_buy_mode(self, **kwargs):
        self.calls.append(("set_negative", kwargs))
        self.negative_state = {"reason": kwargs["reason"]}

    async def async_clear_negative_buy_mode(self):
        self.calls.append(("clear_negative", {}))
        self.negative_state = None


def _entry():
    return MockConfigEntry(domain=DOMAIN, title="Planner", data={}, entry_id="entry1")


@pytest.mark.asyncio
async def test_car_permissive_switch_attributes_and_toggle_paths():
    coordinator = _Coordinator(config={"car_permissive_threshold_multiplier": 1.5})
    switch = CarPermissiveModeSwitch(coordinator, _entry())

    assert switch.is_on is False
    assert switch.extra_state_attributes["threshold_increase_percent"] == "50%"

    await switch.async_turn_on()
    assert switch.is_on is True
    assert coordinator.calls[-1][0] == "set_car"
    await switch.async_turn_off()
    assert switch.is_on is False
    assert coordinator.calls[-1][0] == "clear_car"
    assert coordinator.refreshes == 2


@pytest.mark.asyncio
async def test_battery_disable_switch_override_attributes_and_toggles():
    coordinator = _Coordinator()
    switch = BatteryChargingDisableSwitch(coordinator, _entry())

    assert switch.is_on is False
    assert switch.extra_state_attributes["override_active"] is False

    set_at = datetime(2026, 5, 17, 10, tzinfo=timezone.utc)
    coordinator.manual_override = {
        "value": False,
        "reason": "manual",
        "set_at": set_at,
        "expires_at": None,
    }
    assert switch.is_on is True
    attrs = switch.extra_state_attributes
    assert attrs["override_active"] is True
    assert attrs["set_at"] == set_at.isoformat()
    assert attrs["expires_at"] == "Never"

    await switch.async_turn_on()
    assert coordinator.calls[-1][0] == "set_manual"
    assert coordinator.calls[-1][1]["target"] == "battery"
    await switch.async_turn_off()
    assert coordinator.calls[-1] == ("clear_manual", {"target": "battery"})


@pytest.mark.asyncio
async def test_arbitrage_switch_uses_runtime_state_and_active_plan():
    set_at = datetime(2026, 5, 17, 11, tzinfo=timezone.utc)
    coordinator = _Coordinator(
        data={
            "arbitrage_mode_plan": {
                "enabled": True,
                "reason": "profitable",
                "reserve_soc": 35,
                "active": True,
                "arbitrage_price_threshold": 0.2,
                "current_slot_price": 0.23,
                "slots_cover_full_arbitrage": True,
                "selected_slots_count": 3,
                "selected_slots": ["slot"],
                "deadline": "today",
                "export_power": 2500,
                "configured_export_cap_w": 3000,
                "available_energy_kwh": 4.5,
            }
        }
    )
    coordinator.arbitrage_state = {"reason": "manual", "set_at": set_at}
    switch = ArbitrageModeSwitch(coordinator, _entry())

    assert switch.is_on is True
    attrs = switch.extra_state_attributes
    assert attrs["mode_enabled"] is True
    assert attrs["reason"] == "profitable"
    assert attrs["currently_exporting"] is True
    assert attrs["set_at"] == set_at.isoformat()

    await switch.async_turn_off()
    assert switch.is_on is False
    await switch.async_turn_on()
    assert coordinator.calls[-1][0] == "set_arbitrage"


def test_arbitrage_switch_ignores_stale_plan_when_disabled():
    coordinator = _Coordinator(
        data={"arbitrage_mode_plan": {"enabled": True, "reason": "stale"}}
    )
    switch = ArbitrageModeSwitch(coordinator, _entry())

    attrs = switch.extra_state_attributes
    assert attrs["mode_enabled"] is False
    assert attrs["reason"] == "Arbitrage mode disabled"
    assert attrs["selected_slots"] == []


@pytest.mark.asyncio
async def test_negative_buy_switch_attributes_and_toggle_paths():
    set_at = datetime(2026, 5, 17, 12, tzinfo=timezone.utc)
    coordinator = _Coordinator(
        data={
            "negative_buy_plan": {
                "enabled": True,
                "reason": "negative price",
                "threshold": -0.05,
                "active": True,
                "buy_price_threshold": -0.04,
                "current_slot_price": -0.08,
                "slots_cover_full_charge": False,
                "selected_slots_count": 2,
                "selected_slots": ["cheap"],
                "deadline": "soon",
                "import_power": 1800,
                "configured_import_cap_w": 2000,
                "required_energy_kwh": 3.2,
                "required_duration_hours": 1.5,
            }
        }
    )
    coordinator.negative_state = {"reason": "manual", "set_at": set_at}
    switch = NegativeArbitrageBuyModeSwitch(coordinator, _entry())

    assert switch.is_on is True
    attrs = switch.extra_state_attributes
    assert attrs["mode_enabled"] is True
    assert attrs["reason"] == "negative price"
    assert attrs["currently_buying"] is True
    assert attrs["set_at"] == set_at.isoformat()

    await switch.async_turn_off()
    assert switch.is_on is False
    await switch.async_turn_on()
    assert coordinator.calls[-1][0] == "set_negative"


def test_negative_buy_switch_disabled_state_uses_safe_defaults():
    coordinator = _Coordinator(
        data={"negative_buy_plan": {"enabled": True, "reason": "stale"}}
    )
    switch = NegativeArbitrageBuyModeSwitch(coordinator, _entry())

    attrs = switch.extra_state_attributes
    assert attrs["mode_enabled"] is False
    assert attrs["reason"] == "Negative Arbitrage Buy mode disabled"
    assert attrs["selected_slots_count"] == 0
