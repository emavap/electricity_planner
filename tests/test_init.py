"""Tests for integration setup/reload helpers in __init__.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import (
    _async_migrate_number_entity_ids,
    _async_migrate_switch_entity_ids,
    async_reload_entry,
)
from custom_components.electricity_planner.const import (
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_DUMP_DEADLINE_HOUR,
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
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_BATTERY_DUMP_DEADLINE_HOUR,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
    DOMAIN,
)
from custom_components.electricity_planner.migrations import async_migrate_entry


@pytest.mark.asyncio
async def test_async_reload_entry_applies_live_options_without_full_reload():
    """Only live options changed -> apply in-place and skip full reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 8,
            CONF_SOLAR_FORECAST_START_HOUR: 18,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 45,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 6.5,
        },
    )

    refresh_settings = Mock()
    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 12,
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
    assert coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 6.5
    assert coordinator.config[CONF_BATTERY_DUMP_DEADLINE_HOUR] == 8
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
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 12,
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
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 12,
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
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 12,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={
            CONF_MAX_SOC_THRESHOLD: 85,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 40,
            CONF_BATTERY_DUMP_DEADLINE_HOUR: 10,
        },
    )

    merged = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_MAX_SOC_THRESHOLD: 85,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 40,
        CONF_BATTERY_DUMP_DEADLINE_HOUR: 10,
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

    assert entry.version == 20
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

    assert entry.version == 20
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

    assert entry.version == 20
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

    assert entry.version == 20
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

    assert entry.version == 20
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
async def test_async_migrate_entry_adds_battery_dump_deadline_hour_for_v19():
    """v19 entries should receive the configurable battery dump deadline."""
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

    assert entry.version == 20
    assert entry.data[CONF_BATTERY_DUMP_DEADLINE_HOUR] == DEFAULT_BATTERY_DUMP_DEADLINE_HOUR


@pytest.mark.parametrize(
    ("stored_value", "expected_value"),
    [
        (9.0, 9),
        (9.5, DEFAULT_BATTERY_DUMP_DEADLINE_HOUR),
        ("abc", DEFAULT_BATTERY_DUMP_DEADLINE_HOUR),
        (-1, DEFAULT_BATTERY_DUMP_DEADLINE_HOUR),
        (24, DEFAULT_BATTERY_DUMP_DEADLINE_HOUR),
    ],
)
@pytest.mark.asyncio
async def test_async_migrate_entry_normalizes_battery_dump_deadline_hour_for_v19(
    stored_value,
    expected_value,
):
    """v19 deadline hours should become valid integers or reset to the default."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=19,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_BATTERY_DUMP_DEADLINE_HOUR: stored_value,
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

    assert entry.version == 20
    assert entry.data[CONF_BATTERY_DUMP_DEADLINE_HOUR] == expected_value
