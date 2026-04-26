"""Tests for integration setup/reload helpers in __init__.py."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.exceptions import HomeAssistantError

from custom_components.electricity_planner import (
    _async_migrate_number_entity_ids,
    _async_migrate_switch_entity_ids,
    MANUAL_OVERRIDE_SERVICE_SCHEMA,
    async_reload_entry,
    _register_services_once,
)
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    ATTR_ACTION,
    ATTR_DURATION,
    ATTR_TARGET,
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_BATTERY_CAPACITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_ENERGY_COST_GSC,
    CONF_ENERGY_COST_WKK,
    CONF_ENERGY_TAX_ACCIJNS,
    CONF_ENERGY_TAX_BIJDRAGE,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_MAX_INVERTER_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
    ATTR_GRID_SETPOINT_OVERRIDE,
    MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT,
    MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
    SERVICE_SET_MANUAL_OVERRIDE,
    DOMAIN,
)
from custom_components.electricity_planner.migrations import async_migrate_entry


class FakeServices:
    def __init__(self):
        self.registered: dict[tuple[str, str], dict[str, object]] = {}

    def async_register(self, domain, service, handler, schema=None):
        self.registered[(domain, service)] = {
            "handler": handler,
            "schema": schema,
        }


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.data: dict = {}
        self.services = FakeServices()
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


@pytest.mark.asyncio
async def test_async_reload_entry_applies_live_options_without_full_reload():
    """Only live options changed -> apply in-place and skip full reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_MAX_SOC_THRESHOLD_SOLAR: 85,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 8,
            CONF_SOLAR_FORECAST_START_HOUR: 18,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 45,
            CONF_MAX_SOC_THRESHOLD_SOLAR: 80,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.08,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 6.5,
        },
    )

    refresh_settings = Mock()
    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_MAX_SOC_THRESHOLD_SOLAR: 85,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        decision_engine=SimpleNamespace(refresh_settings=refresh_settings),
        async_request_refresh=AsyncMock(),
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    assert coordinator.config[CONF_SOLAR_FORECAST_START_HOUR] == 18
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD_SUNNY] == 45
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD_SOLAR] == 80
    assert coordinator.config[CONF_NEGATIVE_BUY_THRESHOLD] == -0.08
    assert coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 6.5
    assert coordinator.config[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] == 8
    refresh_settings.assert_called_once_with(coordinator.config)
    coordinator.async_request_refresh.assert_awaited_once()
    hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_async_reload_entry_performs_full_reload_for_non_live_changes():
    """Non-live changes should still trigger a full reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price_new",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={},
    )

    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price_old",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_async_reload_entry_skips_when_live_change_already_applied():
    """No detected diff should not trigger a reload loop."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={
            CONF_MAX_SOC_THRESHOLD: 85,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 40,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 10,
        },
    )

    merged = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_MAX_SOC_THRESHOLD: 85,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 40,
        CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 10,
        CONF_SOLAR_FORECAST_START_HOUR: 20,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
    }
    refresh_settings = Mock()
    coordinator = SimpleNamespace(
        config=dict(merged),
        decision_engine=SimpleNamespace(refresh_settings=refresh_settings),
        async_request_refresh=AsyncMock(),
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    refresh_settings.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()
    hass.config_entries.async_reload.assert_not_called()


def test_manual_override_service_schema_accepts_negative_grid_setpoint():
    """Grid setpoint overrides should allow export values."""
    data = MANUAL_OVERRIDE_SERVICE_SCHEMA(
        {
            ATTR_TARGET: MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
            ATTR_GRID_SETPOINT_OVERRIDE: -4500,
        }
    )

    assert data[ATTR_GRID_SETPOINT_OVERRIDE] == -4500


def test_manual_override_service_schema_rejects_runtime_mode_targets():
    """Runtime mode toggles are planner settings, not manual override targets."""
    with pytest.raises(vol.Invalid):
        MANUAL_OVERRIDE_SERVICE_SCHEMA({ATTR_TARGET: "arbitrage_mode"})
    with pytest.raises(vol.Invalid):
        MANUAL_OVERRIDE_SERVICE_SCHEMA({ATTR_TARGET: "negative_buy"})


@pytest.mark.asyncio
async def test_manual_override_service_requires_numeric_payload_for_numeric_target(monkeypatch):
    """Numeric-only override calls should fail fast when the payload is missing."""
    hass = FakeHass()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CURRENT_PRICE_ENTITY: "sensor.price"}, options={})
    monkeypatch.setattr(
        ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(hass, entry)
    coordinator.async_set_manual_override = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    hass.data = {DOMAIN: {entry.entry_id: coordinator}}

    _register_services_once(hass)
    handler = hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)]["handler"]

    with pytest.raises(HomeAssistantError, match="charger_limit is required"):
        await handler(SimpleNamespace(data={ATTR_TARGET: MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT}))

    coordinator.async_set_manual_override.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_override_service_applies_negative_grid_setpoint(monkeypatch):
    """Negative grid setpoints should be passed through for export control."""
    hass = FakeHass()
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_CURRENT_PRICE_ENTITY: "sensor.price"}, options={})

    monkeypatch.setattr(
        ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(hass, entry)
    coordinator.async_set_manual_override = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()
    hass.data = {DOMAIN: {entry.entry_id: coordinator}}

    _register_services_once(hass)
    handler = hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)]["handler"]

    await handler(
        SimpleNamespace(
            data={
                ATTR_TARGET: MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
                ATTR_GRID_SETPOINT_OVERRIDE: -4500,
            }
        )
    )

    coordinator.async_set_manual_override.assert_awaited_once_with(
        MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
        None,
        None,
        None,
        None,
        -4500,
    )
    coordinator.async_request_refresh.assert_awaited_once()


def test_services_yaml_allows_negative_grid_setpoint_selector():
    """The service selector should expose export-capable grid setpoints in the UI."""
    services_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "electricity_planner"
        / "services.yaml"
    )

    services_data = yaml.safe_load(services_path.read_text())
    grid_selector = services_data["set_manual_override"]["fields"]["grid_setpoint"]["selector"]["number"]

    assert grid_selector["min"] == -50000


def test_services_yaml_excludes_runtime_mode_targets():
    """Manual override services should not expose planner runtime modes."""
    services_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "electricity_planner"
        / "services.yaml"
    )

    services_data = yaml.safe_load(services_path.read_text())
    set_options = services_data["set_manual_override"]["fields"]["target"]["selector"]["select"]["options"]
    clear_options = services_data["clear_manual_override"]["fields"]["target"]["selector"]["select"]["options"]

    assert "arbitrage_mode" not in set_options
    assert "negative_buy" not in set_options
    assert "arbitrage_mode" not in clear_options
    assert "negative_buy" not in clear_options


@pytest.mark.asyncio
async def test_async_migrate_number_entity_ids_renames_legacy_number_ids(monkeypatch):
    """Legacy sunny number IDs should be migrated to stable dashboard-friendly IDs."""
    entry = MockConfigEntry(domain=DOMAIN, title="Electricity Planner", data={}, options={})
    unique_id_to_entity_id = {
        f"{entry.entry_id}_max_soc_threshold": "number.electricity_planner_battery_max_soc_threshold",
        f"{entry.entry_id}_max_soc_threshold_sunny": (
            "number.electricity_planner_battery_max_soc_threshold_high_solar"
        ),
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.electricity_planner_sunny_forecast_trigger",
    }

    class FakeRegistry:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.existing = set(unique_id_to_entity_id.values())

        def async_get_entity_id(self, domain, platform, unique_id):
            assert domain == "number"
            assert platform == DOMAIN
            return unique_id_to_entity_id.get(unique_id)

        def async_get(self, entity_id):
            return object() if entity_id in self.existing else None

        def async_update_entity(self, entity_id, **kwargs):
            self.calls.append((entity_id, kwargs))
            self.existing.discard(entity_id)
            new_entity_id = kwargs.get("new_entity_id")
            if new_entity_id:
                self.existing.add(new_entity_id)

    fake_registry = FakeRegistry()
    monkeypatch.setattr(
        "custom_components.electricity_planner.er.async_get",
        lambda hass: fake_registry,
    )

    await _async_migrate_number_entity_ids(SimpleNamespace(), entry)

    assert (
        "number.electricity_planner_battery_max_soc_threshold",
        {"new_entity_id": "number.electricity_planner_max_soc_threshold"},
    ) in fake_registry.calls
    assert (
        "number.electricity_planner_battery_max_soc_threshold_high_solar",
        {"new_entity_id": "number.electricity_planner_max_soc_threshold_sunny"},
    ) in fake_registry.calls
    assert (
        "number.electricity_planner_sunny_forecast_trigger",
        {"new_entity_id": "number.electricity_planner_sunny_forecast_threshold_kwh"},
    ) in fake_registry.calls


@pytest.mark.asyncio
async def test_async_migrate_switch_entity_ids_renames_legacy_arbitrage_switch(monkeypatch):
    """Legacy arbitrage switch ID should be migrated to the new dashboard-friendly ID."""
    entry = MockConfigEntry(domain=DOMAIN, title="Electricity Planner", data={}, options={})
    unique_id_to_entity_id = {
        f"{entry.entry_id}_battery_dump_to_grid": "switch.electricity_planner_battery_dump_to_grid",
    }

    class FakeRegistry:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.existing = set(unique_id_to_entity_id.values())

        def async_get_entity_id(self, domain, platform, unique_id):
            assert domain == "switch"
            assert platform == DOMAIN
            return unique_id_to_entity_id.get(unique_id)

        def async_get(self, entity_id):
            return object() if entity_id in self.existing else None

        def async_update_entity(self, entity_id, **kwargs):
            self.calls.append((entity_id, kwargs))
            self.existing.discard(entity_id)
            new_entity_id = kwargs.get("new_entity_id")
            if new_entity_id:
                self.existing.add(new_entity_id)

    fake_registry = FakeRegistry()
    monkeypatch.setattr(
        "custom_components.electricity_planner.er.async_get",
        lambda hass: fake_registry,
    )

    await _async_migrate_switch_entity_ids(SimpleNamespace(), entry)

    assert (
        "switch.electricity_planner_battery_dump_to_grid",
        {
            "new_unique_id": f"{entry.entry_id}_arbitrage_mode",
            "new_entity_id": "switch.electricity_planner_arbitrage_mode",
        },
    ) in fake_registry.calls


@pytest.mark.asyncio
async def test_async_migrate_entry_derives_sunny_threshold_from_option_capacities():
    """Migration should derive the new threshold from merged data+options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=13,
        data={},
        options={CONF_BATTERY_CAPACITIES: {"sensor.main_battery": 14.0}},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == pytest.approx(7.0)
    assert entry.data[CONF_MAX_SOC_THRESHOLD] == 90
    assert entry.data[CONF_MAX_SOC_THRESHOLD_SUNNY] == 50


@pytest.mark.asyncio
async def test_async_migrate_entry_preserves_legacy_soc_defaults_for_sparse_v14_entries():
    """Sparse legacy entries should keep historical 90/50 semantics after upgrade."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=14,
        data={CONF_CURRENT_PRICE_ENTITY: "sensor.current_price"},
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_MAX_SOC_THRESHOLD] == 90
    assert entry.data[CONF_MAX_SOC_THRESHOLD_SUNNY] == 50


@pytest.mark.asyncio
async def test_async_migrate_entry_uses_legacy_sunny_default_for_pre_v12_entries():
    """Upgrading from v11 should keep the historical sunny default, not the new-install default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=11,
        data={},
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_MAX_SOC_THRESHOLD_SUNNY] == 50


@pytest.mark.asyncio
async def test_async_migrate_entry_replaces_legacy_transport_cost_sensor():
    """v15 entries should migrate to built-in transport cost defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=15,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_TRANSPORT_COST_ENTITY: "sensor.transport_cost",
        },
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert CONF_TRANSPORT_COST_ENTITY not in entry.data
    assert entry.data[CONF_TRANSPORT_COST_DAY] == pytest.approx(DEFAULT_TRANSPORT_COST_DAY)
    assert entry.data[CONF_TRANSPORT_COST_NIGHT] == pytest.approx(DEFAULT_TRANSPORT_COST_NIGHT)
    assert entry.data[CONF_ENERGY_TAX_ACCIJNS] == pytest.approx(DEFAULT_ENERGY_TAX_ACCIJNS)
    assert entry.data[CONF_ENERGY_TAX_BIJDRAGE] == pytest.approx(DEFAULT_ENERGY_TAX_BIJDRAGE)
    assert entry.data[CONF_ENERGY_COST_GSC] == pytest.approx(DEFAULT_ENERGY_COST_GSC)
    assert entry.data[CONF_ENERGY_COST_WKK] == pytest.approx(DEFAULT_ENERGY_COST_WKK)


@pytest.mark.asyncio
async def test_async_migrate_entry_adds_inverter_derating_defaults_for_v16():
    """v16 entries should receive the new inverter-derating defaults."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=16,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        },
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_MAX_INVERTER_POWER] == DEFAULT_MAX_INVERTER_POWER
    assert entry.data[CONF_INVERTER_EXPORT_LIMIT] == DEFAULT_INVERTER_EXPORT_LIMIT
    assert entry.data[CONF_INVERTER_EXPORT_DEADBAND] == DEFAULT_INVERTER_EXPORT_DEADBAND
    assert (
        entry.data[CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES]
        == DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES
    )
    assert (
        entry.data[CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD]
        == DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD
    )


@pytest.mark.asyncio
async def test_async_migrate_entry_adds_arbitrage_mode_deadline_hour_for_v19():
    """v19 entries should receive the configurable arbitrage deadline (renamed in v23)."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=19,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        },
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] == DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR


@pytest.mark.parametrize(
    ("stored_value", "expected_value"),
    [
        (9.0, 9),
        (9.5, DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR),
        ("abc", DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR),
        (-1, DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR),
        (24, DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR),
    ],
)
@pytest.mark.asyncio
async def test_async_migrate_entry_normalizes_arbitrage_mode_deadline_hour_for_v19(
    stored_value,
    expected_value,
):
    """v19 deadline hours should become valid integers or reset to the default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=19,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            "battery_dump_deadline_hour": stored_value,
        },
        options={},
    )

    def _update_entry(config_entry, *, data=None, version=None, options=None):
        if data is not None:
            config_entry.data = data
        if options is not None:
            config_entry.options = options
        if version is not None:
            config_entry.version = version

    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=_update_entry),
    )

    await async_migrate_entry(hass, entry)

    assert entry.version == 23
    assert entry.data[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] == expected_value
