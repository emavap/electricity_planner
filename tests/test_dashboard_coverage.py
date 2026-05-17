"""Additional unit coverage for managed dashboard helpers."""

import asyncio
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import dashboard
from custom_components.electricity_planner.const import (
    CONF_PHASE_MODE,
    DOMAIN,
    PHASE_MODE_THREE,
)


class _Bus:
    def __init__(self):
        self.listeners = []

    def async_listen_once(self, event, callback):
        self.listeners.append((event, callback))


class _Loop:
    def __init__(self):
        self.now = 0
        self.handles = []

    def time(self):
        return self.now

    def call_later(self, delay, callback):
        handle = SimpleNamespace(_cancelled=False)
        handle.cancelled = lambda: handle._cancelled
        handle.cancel = lambda: setattr(handle, "_cancelled", True)
        self.handles.append((delay, callback, handle))
        return handle


class _Hass:
    def __init__(self):
        self.data = {DOMAIN: {"entry1": object()}}
        self.is_running = False
        self.bus = _Bus()
        self.loop = _Loop()
        self.tasks = []

    def async_create_task(self, coro):
        self.tasks.append(coro)
        return coro

    async def async_add_executor_job(self, func, *args):
        return func(*args)


class _Collection:
    def __init__(self, data=None):
        self.data = data or {}
        self.loaded = False
        self.created = []
        self.updated = []
        self.deleted = []

    async def async_load(self):
        self.loaded = True

    async def async_create_item(self, item):
        self.created.append(item)
        return {"id": "created", **item}

    async def async_update_item(self, item_id, updates):
        self.updated.append((item_id, updates))
        return {"id": item_id, **updates}

    async def async_delete_item(self, item_id):
        self.deleted.append(item_id)


class _Storage:
    def __init__(self, config=None, existing=None, load_error=None):
        self.config = config or {"id": "storage-id"}
        self.existing = existing
        self.load_error = load_error
        self.saved = None

    async def async_load(self, force):
        if self.load_error:
            raise self.load_error
        if self.existing is None:
            raise dashboard.ll_dashboard.ConfigNotFound
        return self.existing

    async def async_save(self, config):
        self.saved = config


def _entry(**kwargs):
    data = kwargs.pop("data", {})
    options = kwargs.pop("options", {})
    return MockConfigEntry(
        domain=DOMAIN, title="Planner", data=data, options=options, entry_id="entry1"
    )


def _entity_map(entry):
    return {
        f"{entry.entry_id}_{ref.unique_suffix}": f"sensor.real_{ref.unique_suffix}"
        for ref in dashboard.ENTITY_REFERENCES
    }


@pytest.mark.asyncio
async def test_setup_dashboard_success_single_phase(monkeypatch):
    hass = _Hass()
    entry = _entry()
    collection = _Collection()
    storage = _Storage(
        config={"id": "storage", dashboard.ll_const.CONF_SHOW_IN_SIDEBAR: True}
    )
    registered = []

    monkeypatch.setattr(
        dashboard,
        "_get_lovelace_handles",
        lambda hass: dashboard.DashboardHandles(collection, {}),
    )
    monkeypatch.setattr(
        dashboard,
        "_async_wait_for_entity_map",
        lambda hass, entry: asyncio.sleep(0, _entity_map(entry)),
    )
    monkeypatch.setattr(
        dashboard,
        "_async_load_template_text",
        lambda hass, filename=dashboard.TEMPLATE_FILENAME: asyncio.sleep(
            0,
            "views:\n  - title: Overview\n    cards:\n      - entity: sensor.electricity_planner_current_electricity_price\n",
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "_ensure_dashboard_record",
        lambda hass, handles, entry, url_path: asyncio.sleep(0, storage),
    )
    monkeypatch.setattr(
        dashboard, "_register_dashboard_panel", lambda *args: registered.append(args)
    )

    await dashboard.async_setup_or_update_dashboard(hass, entry)

    assert collection.loaded is True
    assert storage.saved[dashboard.MANAGED_KEY]["entry_id"] == entry.entry_id
    assert (
        storage.saved["views"][0]["cards"][0]["entity"] == "sensor.real_price_analysis"
    )
    assert registered


@pytest.mark.asyncio
async def test_setup_dashboard_defers_when_handles_or_entities_missing(monkeypatch):
    hass = _Hass()
    entry = _entry()

    monkeypatch.setattr(dashboard, "_get_lovelace_handles", lambda hass: None)
    await dashboard.async_setup_or_update_dashboard(hass, entry)
    assert hass.bus.listeners

    collection = _Collection()
    monkeypatch.setattr(
        dashboard,
        "_get_lovelace_handles",
        lambda hass: dashboard.DashboardHandles(collection, {}),
    )
    monkeypatch.setattr(
        dashboard,
        "_async_wait_for_entity_map",
        lambda hass, entry: asyncio.sleep(0, {}),
    )
    await dashboard.async_setup_or_update_dashboard(hass, entry)
    assert hass.data[DOMAIN][dashboard.ENTITY_MAP_RETRY_COUNTS_KEY][entry.entry_id] == 1


@pytest.mark.asyncio
async def test_setup_dashboard_three_phase_appendix_and_invalid_branches(monkeypatch):
    hass = _Hass()
    entry = _entry(options={CONF_PHASE_MODE: PHASE_MODE_THREE})
    collection = _Collection()
    storage = _Storage()

    monkeypatch.setattr(
        dashboard,
        "_get_lovelace_handles",
        lambda hass: dashboard.DashboardHandles(collection, {}),
    )
    monkeypatch.setattr(
        dashboard,
        "_async_wait_for_entity_map",
        lambda hass, entry: asyncio.sleep(0, _entity_map(entry)),
    )
    monkeypatch.setattr(
        dashboard,
        "_ensure_dashboard_record",
        lambda hass, handles, entry, url_path: asyncio.sleep(0, storage),
    )
    monkeypatch.setattr(dashboard, "_register_dashboard_panel", lambda *args: None)

    async def template_loader(hass, filename=dashboard.TEMPLATE_FILENAME):
        if filename == dashboard.THREE_PHASE_APPENDIX_FILENAME:
            return "- type: markdown\n  content: phase\n"
        return "views:\n  - title: Overview\n    cards: []\n"

    monkeypatch.setattr(dashboard, "_async_load_template_text", template_loader)
    await dashboard.async_setup_or_update_dashboard(hass, entry)
    assert [view["title"] for view in storage.saved["views"]] == [
        "Overview",
        "Three Phase",
    ]

    async def bad_template_loader(hass, filename=dashboard.TEMPLATE_FILENAME):
        return "not: [valid"

    storage.saved = None
    monkeypatch.setattr(dashboard, "_async_load_template_text", bad_template_loader)
    await dashboard.async_setup_or_update_dashboard(hass, entry)
    assert storage.saved is None


@pytest.mark.asyncio
async def test_remove_dashboard_only_deletes_managed_dashboard(monkeypatch):
    hass = _Hass()
    entry = _entry()
    url_path = dashboard._dashboard_url_path(entry)
    collection = _Collection()
    storage = _Storage(
        config={"id": "dash-id"},
        existing={dashboard.MANAGED_KEY: {"entry_id": entry.entry_id}},
    )
    monkeypatch.setattr(
        dashboard,
        "_get_lovelace_handles",
        lambda hass: dashboard.DashboardHandles(collection, {url_path: storage}),
    )
    monkeypatch.setattr(dashboard, "_unregister_dashboard_panel", lambda *args: None)

    await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == ["dash-id"]

    collection.deleted.clear()
    storage.existing = {dashboard.MANAGED_KEY: {"entry_id": "other"}}
    await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == []


@pytest.mark.asyncio
async def test_ensure_record_save_and_panel_error_branches(monkeypatch):
    hass = _Hass()
    entry = _entry()
    url_path = dashboard._dashboard_url_path(entry)
    collection = _Collection(
        data={
            "old": {
                "id": "old",
                dashboard.ll_const.CONF_URL_PATH: url_path,
                dashboard.ll_const.CONF_TITLE: "Old",
            }
        }
    )
    handles = dashboard.DashboardHandles(collection, {})

    class LovelaceStorageStub(_Storage):
        def __init__(self, hass, item):
            super().__init__(config=item)

    monkeypatch.setattr(dashboard.ll_dashboard, "LovelaceStorage", LovelaceStorageStub)
    storage = await dashboard._ensure_dashboard_record(hass, handles, entry, url_path)
    assert collection.updated
    assert handles.dashboards[url_path] is storage

    unchanged = {"views": []}
    storage = _Storage(existing=unchanged)
    await dashboard._save_dashboard(storage, dict(unchanged))
    assert storage.saved is None
    await dashboard._save_dashboard(
        _Storage(load_error=HomeAssistantError("boom")), {"views": [1]}
    )

    removed = []
    monkeypatch.setattr(
        dashboard.frontend,
        "async_remove_panel",
        lambda hass, path: removed.append(path),
    )
    dashboard._unregister_dashboard_panel(hass, url_path)
    assert removed == [url_path]


def test_retry_state_and_replacement_helpers():
    hass = _Hass()
    entry = _entry()
    first = dashboard._dashboard_url_path(entry)
    assert first.startswith("electricity-planner-planner-entry1")

    replacements = dashboard._build_replacements(entry, _entity_map(entry))
    assert (
        replacements["sensor.electricity_planner_current_electricity_price"]
        == "sensor.real_price_analysis"
    )
    assert dashboard._find_unresolved_placeholders(
        "sensor.electricity_planner_current_electricity_price", {}
    )
    assert (
        dashboard._apply_replacements(
            "x sensor.electricity_planner_current_electricity_price", replacements
        )
        == "x sensor.real_price_analysis"
    )
    assert dashboard._configs_equal({"b": 1, "a": 2}, {"a": 2, "b": 1}) is True

    dashboard._schedule_entity_map_retry(hass, entry, "missing")
    dashboard._schedule_entity_map_retry(hass, entry, "duplicate")
    assert hass.data[DOMAIN][dashboard.ENTITY_MAP_RETRY_COUNTS_KEY][entry.entry_id] == 1
    handle = hass.data[DOMAIN][dashboard.ENTITY_MAP_RETRY_HANDLES_KEY][entry.entry_id]
    dashboard._clear_entity_map_retry_state(hass, entry.entry_id)
    assert handle.cancelled()
