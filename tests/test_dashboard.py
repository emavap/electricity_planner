"""Dashboard automation tests for Electricity Planner."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import dashboard
from custom_components.electricity_planner.const import DOMAIN


class FakeLoop:
    def __init__(self):
        self.calls: list[tuple[float, object]] = []

    def time(self) -> float:
        return 0.0

    def call_later(self, delay: float, callback):
        handle = FakeTimerHandle(delay, callback)
        self.calls.append((delay, callback))
        return handle


class FakeTimerHandle:
    def __init__(self, delay: float, callback):
        self.delay = delay
        self.callback = callback
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def cancelled(self) -> bool:
        return self._cancelled


class FakeStorage:
    def __init__(self, config_id: str = "test-id", saved: dict | None = None):
        self.config = {"id": config_id}
        self.saved = saved

    async def async_load(self, force: bool):
        if self.saved is None:
            raise dashboard.ll_dashboard.ConfigNotFound
        return self.saved

    async def async_save(self, config: dict):
        self.saved = config


class FakeCollection:
    def __init__(self):
        self.created: list[dict] = []
        self.updated: list[tuple[str, dict]] = []
        self.deleted: list[str] = []

    async def async_create_item(self, item: dict):
        self.created.append(item)
        return {"id": item[dashboard.ll_const.CONF_URL_PATH], **item}

    async def async_update_item(self, item_id: str, updates: dict):
        self.updated.append((item_id, updates))

    async def async_delete_item(self, item_id: str):
        self.deleted.append(item_id)


def test_dashboard_core_entity_suffixes_include_dual_threshold_controls():
    """Managed dashboard should wait for all threshold entities to be registered."""
    assert "max_soc_threshold" in dashboard.CORE_ENTITY_SUFFIXES
    assert "max_soc_threshold_sunny" in dashboard.CORE_ENTITY_SUFFIXES
    assert "sunny_forecast_threshold_kwh" in dashboard.CORE_ENTITY_SUFFIXES


@pytest.mark.asyncio
async def test_dashboard_creation_uses_registered_entities():
    """Ensure the generated dashboard references actual entity IDs."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    entity_map = {
        f"{entry.entry_id}_price_analysis": "sensor.custom_price_sensor",
        f"{entry.entry_id}_battery_grid_charging": "binary_sensor.battery_grid_allowed",
        f"{entry.entry_id}_car_grid_charging": "binary_sensor.car_grid_allowed",
        f"{entry.entry_id}_max_soc_threshold": "number.custom_max_soc",
        f"{entry.entry_id}_max_soc_threshold_sunny": "number.custom_max_soc_sunny",
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.custom_sunny_forecast_threshold",
    }
    template = (
        "views:\n"
        "  - cards:\n"
        "      - entity: sensor.electricity_planner_current_electricity_price\n"
        "      - entity: binary_sensor.electricity_planner_battery_charge_from_grid\n"
        "      - entity: binary_sensor.electricity_planner_car_charge_from_grid\n"
        "      - entity: number.electricity_planner_max_soc_threshold\n"
        "      - entity: number.electricity_planner_max_soc_threshold_sunny\n"
        "      - entity: number.electricity_planner_sunny_forecast_threshold_kwh\n"
    )

    storage = FakeStorage()

    with patch.object(
        dashboard, "_get_lovelace_handles", return_value=dashboard.DashboardHandles(collection=None, dashboards={})
    ), patch.object(
        dashboard, "_ensure_dashboard_record", new=AsyncMock(return_value=storage)
    ) as ensure_mock, patch.object(
        dashboard, "_async_wait_for_entity_map", new=AsyncMock(return_value=entity_map)
    ), patch.object(
        dashboard, "_async_load_template_text", new=AsyncMock(return_value=template)
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_called_once()
    config_str = json.dumps(storage.saved)
    assert "sensor.custom_price_sensor" in config_str
    assert "binary_sensor.battery_grid_allowed" in config_str
    assert "binary_sensor.car_grid_allowed" in config_str
    assert "number.custom_max_soc" in config_str
    assert "number.custom_max_soc_sunny" in config_str
    assert "number.custom_sunny_forecast_threshold" in config_str
    assert storage.saved[dashboard.MANAGED_KEY]["entry_id"] == entry.entry_id


@pytest.mark.asyncio
async def test_dashboard_setup_skips_when_no_entities():
    """Dashboard creation is skipped if no entities were registered."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    with patch.object(
        dashboard, "_get_lovelace_handles", return_value=dashboard.DashboardHandles(collection=None, dashboards={})
    ), patch.object(
        dashboard, "_async_wait_for_entity_map", new=AsyncMock(return_value={})
    ), patch.object(
        dashboard, "_ensure_dashboard_record", new=AsyncMock()
    ) as ensure_mock, patch.object(
        dashboard, "_schedule_entity_map_retry"
    ) as retry_mock:
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_not_called()
    retry_mock.assert_called_once()


@pytest.mark.asyncio
async def test_dashboard_setup_retries_when_core_entities_missing():
    """Dashboard creation should be deferred if core entities are incomplete."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    entity_map = {
        f"{entry.entry_id}_price_analysis": "sensor.custom_price_sensor",
        f"{entry.entry_id}_battery_grid_charging": "binary_sensor.battery_grid_allowed",
        f"{entry.entry_id}_car_grid_charging": "binary_sensor.car_grid_allowed",
        f"{entry.entry_id}_max_soc_threshold": "number.custom_max_soc",
        f"{entry.entry_id}_max_soc_threshold_sunny": "number.custom_max_soc_sunny",
        # Missing sunny_forecast_threshold_kwh on purpose
    }

    with patch.object(
        dashboard, "_get_lovelace_handles", return_value=dashboard.DashboardHandles(collection=None, dashboards={})
    ), patch.object(
        dashboard, "_async_wait_for_entity_map", new=AsyncMock(return_value=entity_map)
    ), patch.object(
        dashboard, "_ensure_dashboard_record", new=AsyncMock()
    ) as ensure_mock, patch.object(
        dashboard, "_schedule_entity_map_retry"
    ) as retry_mock:
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_not_called()
    retry_mock.assert_called_once()


@pytest.mark.asyncio
async def test_dashboard_removal_only_deletes_managed_dashboard():
    """Ensure dashboard removal only occurs for managed dashboards."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Planner")
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    collection = FakeCollection()
    managed_storage = FakeStorage(
        config_id="managed-id",
        saved={
            "views": [],
            dashboard.MANAGED_KEY: {"entry_id": entry.entry_id, "version": dashboard.MANAGED_VERSION},
        },
    )
    unmanaged_storage = FakeStorage(
        config_id="other-id",
        saved={
            "views": [],
            dashboard.MANAGED_KEY: {"entry_id": "different", "version": dashboard.MANAGED_VERSION},
        },
    )

    handles_managed = dashboard.DashboardHandles(
        collection=collection,
        dashboards={"electricity-planner-planner-": managed_storage},
    )
    handles_unmanaged = dashboard.DashboardHandles(
        collection=collection,
        dashboards={"electricity-planner-planner-": unmanaged_storage},
    )

    with patch.object(dashboard, "_dashboard_url_path", return_value="electricity-planner-planner-"), patch.object(
        dashboard, "_get_lovelace_handles", return_value=handles_managed
    ):
        await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == ["managed-id"]

    collection.deleted.clear()
    with patch.object(dashboard, "_dashboard_url_path", return_value="electricity-planner-planner-"), patch.object(
        dashboard, "_get_lovelace_handles", return_value=handles_unmanaged
    ):
        await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == []


def test_schedule_entity_map_retry_deduplicates_pending_retry():
    """Scheduling should not queue multiple timers for the same entry."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={DOMAIN: {entry.entry_id: object()}})

    dashboard._schedule_entity_map_retry(hass, entry, "first")
    dashboard._schedule_entity_map_retry(hass, entry, "duplicate")

    retry_counts = hass.data[DOMAIN][dashboard.ENTITY_MAP_RETRY_COUNTS_KEY]
    retry_handles = hass.data[DOMAIN][dashboard.ENTITY_MAP_RETRY_HANDLES_KEY]

    assert retry_counts[entry.entry_id] == 1
    assert entry.entry_id in retry_handles
    assert len(hass.loop.calls) == 1


@pytest.mark.asyncio
async def test_remove_dashboard_cancels_pending_entity_map_retry():
    """Unloading should cancel any delayed retry to avoid ghost dashboard creation."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    loop = FakeLoop()
    handle = FakeTimerHandle(10, lambda: None)
    hass = SimpleNamespace(
        loop=loop,
        data={
            DOMAIN: {
                dashboard.ENTITY_MAP_RETRY_COUNTS_KEY: {entry.entry_id: 2},
                dashboard.ENTITY_MAP_RETRY_HANDLES_KEY: {entry.entry_id: handle},
            }
        },
    )

    with patch.object(dashboard, "_get_lovelace_handles", return_value=None):
        await dashboard.async_remove_dashboard(hass, entry)

    assert handle.cancelled()
    retry_counts = hass.data[DOMAIN].get(dashboard.ENTITY_MAP_RETRY_COUNTS_KEY, {})
    retry_handles = hass.data[DOMAIN].get(dashboard.ENTITY_MAP_RETRY_HANDLES_KEY, {})
    assert entry.entry_id not in retry_counts
    assert entry.entry_id not in retry_handles
