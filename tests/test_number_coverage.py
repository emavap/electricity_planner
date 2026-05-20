"""Additional coverage for Electricity Planner number entities."""

from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import number as number_module
from custom_components.electricity_planner.const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
)
from custom_components.electricity_planner.number import (
    ArbitrageModeDeadlineHourNumber,
    ArbitrageModeReserveSocNumber,
    MaxSocThresholdNumber,
    MaxSocThresholdSolarNumber,
    MaxSocThresholdSunnyNumber,
    NegativeBuyThresholdNumber,
    SunnyForecastThresholdNumber,
    _parse_state_as_float,
)


class _ConfigEntries:
    def __init__(self, entry):
        self.entry = entry
        self.updated = []

    def async_get_entry(self, entry_id):
        return self.entry if entry_id == self.entry.entry_id else None

    def async_update_entry(self, entry, **kwargs):
        self.updated.append((entry, kwargs))


class _States:
    def __init__(self, states=None):
        self._states = states or {}

    def get(self, entity_id):
        value = self._states.get(entity_id)
        return None if value is None else SimpleNamespace(state=value)


class _DecisionEngine:
    def __init__(self):
        self.refreshed_with = None

    def refresh_settings(self, config):
        self.refreshed_with = dict(config)


class _Coordinator(SimpleNamespace):
    def __init__(self, *, config=None, data=None, states=None):
        self.decision_engine = _DecisionEngine()
        self.refreshes = 0
        super().__init__(
            config=config or {},
            data=data or {},
            async_add_listener=lambda update_callback, context=None: lambda: None,
            hass=SimpleNamespace(states=_States(states)),
            decision_engine=self.decision_engine,
        )

    async def async_request_refresh(self):
        self.refreshes += 1


def _entry(data=None, options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        title="Planner",
        data=data or {},
        options=options or {},
        entry_id="entry1",
    )


def _attach_hass(entity, entry):
    hass = SimpleNamespace(config_entries=_ConfigEntries(entry))
    entity.hass = hass
    return hass


def test_parse_state_as_float_accepts_localized_and_rejects_invalid_values():
    assert _parse_state_as_float("12.5") == 12.5
    assert _parse_state_as_float(" 12,75 kWh") == 12.75
    assert _parse_state_as_float(None) is None
    assert _parse_state_as_float("unknown") is None


@pytest.mark.asyncio
async def test_soc_number_attributes_validation_and_persistence():
    entry = _entry(
        data={CONF_MAX_SOC_THRESHOLD: 80}, options={CONF_MAX_SOC_THRESHOLD_SUNNY: 60}
    )
    coordinator = _Coordinator(
        config={CONF_MAX_SOC_THRESHOLD: 80, CONF_MAX_SOC_THRESHOLD_SUNNY: 60},
        data={"battery_analysis": {"average_soc": 42.5}},
    )
    number = MaxSocThresholdNumber(coordinator, entry)
    hass = _attach_hass(number, entry)

    assert number.native_value == 80
    attrs = number.extra_state_attributes
    assert attrs["current_battery_soc"] == "42.5%"
    assert attrs["remaining_to_threshold"] == "37.5%"

    await number.async_set_native_value(85)
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD] == 85
    assert coordinator.refreshes == 1
    assert hass.config_entries.updated[-1][1]["options"][CONF_MAX_SOC_THRESHOLD] == 85

    with pytest.raises(HomeAssistantError):
        await number.async_set_native_value(55)


@pytest.mark.asyncio
async def test_sunny_and_solar_numbers_cover_forecast_and_validation_paths():
    entry = _entry(
        data={CONF_MAX_SOC_THRESHOLD: 90},
        options={CONF_MAX_SOC_THRESHOLD_SUNNY: 50, CONF_MAX_SOC_THRESHOLD_SOLAR: 95},
    )
    coordinator = _Coordinator(
        config={
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_MAX_SOC_THRESHOLD_SOLAR: 95,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 20,
        },
        data={
            "sunny_day_active": True,
            "solar_forecast_production": 24.4,
            "battery_analysis": {"average_soc": 80},
        },
    )

    sunny = MaxSocThresholdSunnyNumber(coordinator, entry)
    _attach_hass(sunny, entry)
    assert sunny.native_value == 50
    assert sunny.extra_state_attributes["solar_forecast_kwh"] == "24.4 kWh"
    await sunny.async_set_native_value(70)
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD_SUNNY] == 70
    with pytest.raises(HomeAssistantError):
        await sunny.async_set_native_value(95)

    solar = MaxSocThresholdSolarNumber(coordinator, entry)
    _attach_hass(solar, entry)
    assert solar.native_value == 95
    assert solar.extra_state_attributes["solar_absorbing"] is True
    await solar.async_set_native_value(88)
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD_SOLAR] == 88


@pytest.mark.asyncio
async def test_sunny_forecast_threshold_uses_entity_fallback_and_clamps_negative_values(
    monkeypatch,
):
    monkeypatch.setattr(
        number_module.dt_util,
        "now",
        lambda: number_module.dt_util.utcnow().replace(hour=12),
        raising=False,
    )
    entry = _entry()
    coordinator = _Coordinator(
        config={
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 12,
            "solar_forecast_start_hour": 23,
            "solar_forecast_today_entity": "sensor.today_solar",
        },
        data={},
        states={"sensor.today_solar": "13,5 kWh"},
    )
    number = SunnyForecastThresholdNumber(coordinator, entry)
    _attach_hass(number, entry)

    attrs = number.extra_state_attributes
    assert attrs["solar_forecast_kwh"] == "13.5 kWh"
    assert attrs["above_threshold"] is True

    await number.async_set_native_value(-1)
    assert coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 0.0


@pytest.mark.asyncio
async def test_arbitrage_deadline_reserve_and_negative_buy_numbers():
    entry = _entry(options={CONF_ARBITRAGE_MODE_RESERVE_SOC: 30})
    coordinator = _Coordinator(
        config={
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 30,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 16,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
        },
        data={
            "battery_analysis": {"average_soc": 75},
            "arbitrage_mode_plan": {
                "enabled": True,
                "arbitrage_price_threshold": 0.2,
                "current_slot_price": 0.23,
                "selected_slots_count": 4,
                "slots_cover_full_arbitrage": True,
            },
            "negative_buy_plan": {
                "enabled": True,
                "active": True,
                "selected_slots_count": 2,
                "buy_price_threshold": -0.04,
                "current_slot_price": -0.06,
                "deadline": "2026-05-17T16:00:00+02:00",
            },
        },
    )

    reserve = ArbitrageModeReserveSocNumber(coordinator, entry)
    _attach_hass(reserve, entry)
    assert reserve.extra_state_attributes["available_to_export_percent"] == "45.0%"
    assert reserve.extra_state_attributes["slots_cover_full_arbitrage"] is True
    await reserve.async_set_native_value(35)
    assert coordinator.config[CONF_ARBITRAGE_MODE_RESERVE_SOC] == 35

    deadline = ArbitrageModeDeadlineHourNumber(coordinator, entry)
    _attach_hass(deadline, entry)
    assert "Local hour" in deadline.extra_state_attributes["description"]
    await deadline.async_set_native_value(18)
    assert coordinator.config[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] == 18
    with pytest.raises(HomeAssistantError):
        await deadline.async_set_native_value(24)

    negative = NegativeBuyThresholdNumber(coordinator, entry)
    _attach_hass(negative, entry)
    assert negative.native_value == -0.05
    assert negative.extra_state_attributes["currently_buying"] is True
    await negative.async_set_native_value(-0.1234)
    assert coordinator.config[CONF_NEGATIVE_BUY_THRESHOLD] == -0.123
