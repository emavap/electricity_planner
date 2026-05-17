"""Dashboard automation tests for Electricity Planner."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import dashboard
from custom_components.electricity_planner.const import (
    CONF_PHASE_MODE,
    DOMAIN,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)


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


def test_entry_phase_mode_prefers_options_over_data():
    """Managed dashboard template selection should follow the effective config mode."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_PHASE_MODE: PHASE_MODE_SINGLE},
        options={CONF_PHASE_MODE: PHASE_MODE_THREE},
    )

    assert dashboard._entry_phase_mode(entry) == PHASE_MODE_THREE


def test_dashboard_template_splits_buy_and_sell_price_graphs():
    """The managed dashboard should keep buy and sell concerns on separate charts."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()

    assert "title: Electricity Buy Prices (History + Future)" in template
    assert "title: Electricity Sell Prices (History + Future)" in template
    assert "name: Net feed-in price" in template
    assert "name: Feed-in Threshold" in template
    assert "annotations:" in template
    assert "text: Break-even" in template
    assert "attributes?.price_threshold" in template
    assert "lastInterval.end ?? lastInterval.start" in template
    assert "interval.raw_price ?? 0" in template
    assert "interval.adjusted_energy_price ?? 0" not in template
    assert "name: Contract energy price" not in template
    assert "name: Raw market price" in template
    assert "type: column" not in template
    assert "name: Buy price" in template
    assert "name: Sell price" in template


def test_managed_dashboard_template_splits_heavy_cards_into_secondary_views():
    """Managed dashboard should keep the first view light and move charts/diagnostics elsewhere."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()
    parsed = yaml.safe_load(template)
    views = parsed["views"]

    assert [view["title"] for view in views] == [
        "Overview",
        "Controls",
        "Prices",
        "Diagnostics",
    ]

    overview_text = json.dumps(views[0])
    prices_text = json.dumps(views[2])
    diagnostics_text = json.dumps(views[3])

    assert "custom:apexcharts-card" not in overview_text
    assert "Electricity Buy Prices (History + Future)" in prices_text
    assert "Electricity Sell Prices (History + Future)" in prices_text
    assert "Price & Decisions (24h)" in prices_text
    assert "Algorithm thresholds" in diagnostics_text


def test_dashboard_template_keeps_dump_threshold_on_sell_graph():
    """The managed dashboard should surface the arbitrage threshold only on the sell chart."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()

    sell_section = template.split(
        "title: Electricity Sell Prices (History + Future)", 1
    )[1]
    buy_section = template.split("title: Electricity Buy Prices (History + Future)", 1)[
        1
    ].split("title: Electricity Sell Prices (History + Future)", 1)[0]

    assert "name: Arbitrage Threshold" in template
    assert "name: Arbitrage Threshold" in sell_section
    assert "name: Arbitrage Threshold" not in buy_section
    assert "arbitrage_price_threshold" in template
    assert "title: Battery Dump Plan" not in template
    assert "number.electricity_planner_arbitrage_mode_max_export_power" not in template


def test_dashboard_three_phase_appendix_contains_phase_specific_cards():
    """The managed three-phase appendix should add per-phase cards on top of the shared template."""
    appendix = (
        Path(dashboard.__file__)
        .with_name("dashboard_template_3phase_appendix.yaml")
        .read_text()
    )

    assert "## 🔀 Three-Phase Details" in appendix
    assert "Phase 1 (L1) Status" in appendix
    assert "Phase 2 (L2) Status" in appendix
    assert "Phase 3 (L3) Status" in appendix
    assert "Grid Setpoint per Phase (Current)" in appendix
    assert "Actual grid power" in appendix
    assert "sensor.electricity_planner_grid_setpoint_phase_1" in appendix
    assert "sensor.electricity_planner_grid_setpoint_phase_2" in appendix
    assert "sensor.electricity_planner_grid_setpoint_phase_3" in appendix
    assert "phase_details" in appendix
    assert "Three-phase source entities" in appendix
    assert "custom:template-entity-row" not in appendix
    assert "card_mod:" not in appendix


def test_three_phase_appendix_merges_as_dedicated_view():
    """Managed three-phase cards should not make the overview heavy again."""
    dashboard_config = {"views": [{"title": "Overview", "cards": []}]}
    appendix_cards = [{"type": "markdown", "content": "three phase"}]

    assert (
        dashboard._append_cards_to_primary_stack(dashboard_config, appendix_cards)
        is True
    )

    assert [view["title"] for view in dashboard_config["views"]] == [
        "Overview",
        "Three Phase",
    ]
    three_phase_view = dashboard_config["views"][1]
    assert three_phase_view["path"] == "three-phase"
    assert three_phase_view["cards"][0]["cards"] == appendix_cards


def test_bundled_single_phase_dashboard_renders_price_components_with_four_decimals():
    """Bundled single-phase dashboard should render formatted values without custom rows."""
    dashboard_path = Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    content = dashboard_path.read_text(encoding="utf-8")

    assert "title: Price components" in content
    assert "type: custom:template-entity-row" not in content
    assert "type: markdown" in content
    assert "**Total price:**" in content
    assert "**Energy market price:**" in content
    assert "**Transport cost:**" in content
    assert "| round(4)" in content
    assert "€/kWh" in content


def test_bundled_single_phase_dashboard_documents_required_custom_cards():
    """Bundled single-phase dashboard should declare every custom card it uses."""
    dashboard_path = Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    content = dashboard_path.read_text(encoding="utf-8")

    assert 'Install "Gauge Card Pro"' in content
    assert 'Install "ApexCharts Card"' in content
    assert 'Install "Button Card"' in content
    assert "custom:gauge-card-pro" in content
    assert "custom:apexcharts-card" in content
    assert "custom:button-card" in content


def test_bundled_dashboards_keep_dump_toggle_but_not_export_cap_number():
    """Arbitrage mode should stay toggleable from dashboards, but export cap lives in config flow."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    assert "switch.electricity_planner_arbitrage_mode" in single_phase
    assert "switch.electricity_planner_arbitrage_mode" in three_phase
    assert "name: Arbitrage Threshold" in single_phase
    assert "name: Arbitrage Threshold" in three_phase
    single_phase_buy_section = single_phase.split(
        "title: Electricity Buy Prices (History + Future)", 1
    )[1].split("title: Electricity Sell Prices (History + Future)", 1)[0]
    three_phase_buy_section = three_phase.split(
        "title: Electricity Buy Prices (History + Future)", 1
    )[1].split("title: Electricity Sell Prices (History + Future)", 1)[0]
    assert "name: Arbitrage Threshold" not in single_phase_buy_section
    assert "name: Arbitrage Threshold" not in three_phase_buy_section
    assert "name: Net feed-in price" in single_phase
    assert "name: Net feed-in price" in three_phase
    assert "binary_sensor.electricity_planner_solar_feed_in_grid" in single_phase
    assert "Sunny Forecast Trigger" in single_phase
    assert "Sunny Forecast Trigger" in three_phase
    assert "Solar Max SOC" in single_phase
    assert "Solar Max SOC" in three_phase
    assert "number.electricity_planner_max_soc_threshold_solar" in single_phase
    assert "number.electricity_planner_max_soc_threshold_solar" in three_phase
    assert "title: Battery Dump Plan" not in single_phase
    assert "title: Battery Dump Plan" not in three_phase
    assert (
        "number.electricity_planner_arbitrage_mode_max_export_power" not in single_phase
    )
    assert (
        "number.electricity_planner_arbitrage_mode_max_export_power" not in three_phase
    )


def test_bundled_dashboards_include_arbitrage_reason():
    """Both bundled dashboards should expose the arbitrage reason line."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    assert "name: Arbitrage reason" in single_phase
    assert "name: Arbitrage reason" in three_phase


def test_bundled_dashboards_include_negative_arbitrage_buy_reason():
    """Both bundled dashboards should expose the Negative Arbitrage Buy reason attribute row."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    assert "name: Negative Arbitrage Buy reason" in single_phase
    assert "name: Negative Arbitrage Buy reason" in three_phase
    assert "switch.electricity_planner_negative_arbitrage_buy_mode" in single_phase
    assert "switch.electricity_planner_negative_arbitrage_buy_mode" in three_phase


def test_managed_dashboard_template_includes_negative_arbitrage_buy_reason():
    """Managed dashboard should expose the Negative Arbitrage Buy reason attribute row."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()

    assert "name: Negative Arbitrage Buy reason" in template
    assert "switch.electricity_planner_negative_arbitrage_buy_mode" in template


def test_bundled_dashboards_include_negative_buy_threshold_line_on_buy_chart():
    """The Negative Arbitrage Buy threshold line should appear only on the buy chart."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    for content in (single_phase, three_phase):
        buy_section = content.split(
            "title: Electricity Buy Prices (History + Future)", 1
        )[1].split("title: Electricity Sell Prices (History + Future)", 1)[0]
        sell_section = content.split(
            "title: Electricity Sell Prices (History + Future)", 1
        )[1]
        negative_threshold_section = buy_section.split(
            "name: Negative Arbitrage Buy Threshold", 1
        )[1].split("data_generator:", 1)[0]

        assert "name: Negative Arbitrage Buy Threshold" in buy_section
        assert "name: Negative Arbitrage Buy Threshold" not in sell_section
        assert 'color: "#16a085"' in negative_threshold_section
        assert 'color: "#c0392b"' not in negative_threshold_section
        assert "switch.electricity_planner_negative_arbitrage_buy_mode" in buy_section


def test_managed_dashboard_template_includes_negative_buy_threshold_line_on_buy_chart():
    """Managed dashboard should show the Negative Arbitrage Buy threshold only on the buy chart."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()
    buy_section = template.split("title: Electricity Buy Prices (History + Future)", 1)[
        1
    ].split("title: Electricity Sell Prices (History + Future)", 1)[0]
    sell_section = template.split(
        "title: Electricity Sell Prices (History + Future)", 1
    )[1]
    negative_threshold_section = buy_section.split(
        "name: Negative Arbitrage Buy Threshold", 1
    )[1].split("data_generator:", 1)[0]

    assert "name: Negative Arbitrage Buy Threshold" in buy_section
    assert "name: Negative Arbitrage Buy Threshold" not in sell_section
    assert 'color: "#16a085"' in negative_threshold_section
    assert 'color: "#c0392b"' not in negative_threshold_section
    assert "switch.electricity_planner_negative_arbitrage_buy_mode" in buy_section


def test_bundled_dashboards_drop_top_section_buttons():
    """The redundant top-of-dashboard Car permissive / Arbitrage mode buttons should be removed."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    for content in (single_phase, three_phase):
        # Entries above the "Active Grid Charging Limit" markdown card represent the top section.
        top_section = content.split("title: Active Grid Charging Limit", 1)[0]

        # The two buttons (`type: button` with these names) should no longer live in the top section.
        assert "name: Car permissive" not in top_section
        assert "name: Arbitrage mode" not in top_section
        # But both switches must still be reachable from the entities row in Battery Controls below.
        assert "switch.electricity_planner_car_permissive_mode" in content
        assert "switch.electricity_planner_arbitrage_mode" in content


def test_managed_dashboard_template_drops_top_section_buttons():
    """Managed dashboard should keep runtime switches in Battery Controls, not duplicate top buttons."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()
    top_section = template.split("title: Active Grid Charging Limit", 1)[0]

    assert "name: Car permissive" not in top_section
    assert "name: Arbitrage mode" not in top_section
    assert "switch.electricity_planner_car_permissive_mode" in template
    assert "switch.electricity_planner_arbitrage_mode" in template


def test_bundled_dashboards_include_battery_controls_quick_reference():
    """Battery Controls section should be followed by an explanatory markdown card."""
    single_phase = (
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    ).read_text(encoding="utf-8")
    three_phase = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    ).read_text(encoding="utf-8")

    for content in (single_phase, three_phase):
        # The explainer card lives between Battery Controls and the next entities card (Decisions).
        battery_section = content.split("title: Battery Controls", 1)[1].split(
            "title: Decisions", 1
        )[0]

        assert "Battery Controls — quick reference" in battery_section
        # Each control name from the entities row should appear in the explanations.
        assert "**Grid Max SOC**" in battery_section
        assert "**Grid Max SOC (high solar)**" in battery_section
        assert "**Solar Max SOC**" in battery_section
        assert "**Sunny Forecast Trigger**" in battery_section
        assert "**Arbitrage Reserve SOC**" in battery_section
        assert "**Arbitrage Deadline Hour**" in battery_section
        assert "**Negative Arbitrage Buy Threshold**" in battery_section
        assert "**Car permissive**" in battery_section
        assert "**Arbitrage mode**" in battery_section
        assert "**Negative Arbitrage Buy Mode**" in battery_section
        assert "**Disable Battery Charging**" in battery_section


def test_managed_dashboard_template_includes_battery_controls_quick_reference():
    """Managed template should ship the same Battery Controls explanations card."""
    template = Path(dashboard.__file__).with_name("dashboard_template.yaml").read_text()
    battery_section = template.split("title: Battery Controls", 1)[1].split(
        "title: Decisions", 1
    )[0]

    assert "Battery Controls — quick reference" in battery_section
    assert "**Grid Max SOC**" in battery_section
    assert "**Grid Max SOC (high solar)**" in battery_section
    assert "**Solar Max SOC**" in battery_section
    assert "**Sunny Forecast Trigger**" in battery_section
    assert "**Arbitrage Reserve SOC**" in battery_section
    assert "**Arbitrage Deadline Hour**" in battery_section
    assert "**Negative Arbitrage Buy Threshold**" in battery_section
    assert "**Car permissive**" in battery_section
    assert "**Arbitrage mode**" in battery_section
    assert "**Negative Arbitrage Buy Mode**" in battery_section
    assert "**Disable Battery Charging**" in battery_section


def test_bundled_three_phase_dashboard_includes_shared_single_phase_sections():
    """Three-phase dashboard should include the same shared charts and diagnostics as single-phase."""
    dashboard_path = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    )
    content = dashboard_path.read_text(encoding="utf-8")

    assert "title: Electricity Buy Prices (History + Future)" in content
    assert "title: Electricity Sell Prices (History + Future)" in content
    assert "title: Price components" in content
    assert "title: Price adjustments" in content
    assert "title: Thresholds" in content
    assert "title: Status" in content
    assert "title: Battery Controls" in content
    assert "title: Decisions" in content
    assert "title: Algorithm thresholds" in content
    assert "title: Price & Decisions (24h)" in content
    assert "title: Price Forecast Insights" in content
    assert "title: Manual override status" in content
    assert "text: Break-even" in content
    assert "name: Net feed-in price" in content
    assert "name: Feed-in Threshold" in content
    assert "name: Data OK" in content
    assert "name: Car permissive" in content
    assert "custom:button-card" in content
    assert "service: electricity_planner.clear_manual_override" in content
    assert "target: all" in content


def test_bundled_three_phase_dashboard_uses_native_rows_for_phase_details():
    """Bundled three-phase dashboard should avoid fragile custom rows on the phase view."""
    dashboard_path = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    )
    content = dashboard_path.read_text(encoding="utf-8")

    assert "type: custom:template-entity-row" not in content
    assert "Battery reason" in content
    assert "Car reason" in content
    assert "Actual grid power" in content
    assert "sensor.electricity_planner_grid_setpoint_phase_1" in content
    assert "Three-phase source entities" in content
    assert "phase.grid_setpoint" in content


def test_bundled_three_phase_dashboard_documents_required_custom_cards():
    """Bundled three-phase dashboard should declare every custom card it uses."""
    dashboard_path = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    )
    content = dashboard_path.read_text(encoding="utf-8")

    assert 'Install "Gauge Card Pro"' in content
    assert 'Install "ApexCharts Card"' in content
    assert 'Install "Button Card"' in content
    assert "custom:gauge-card-pro" in content
    assert "custom:apexcharts-card" in content
    assert "custom:button-card" in content


def test_manual_override_status_formats_numeric_overrides_as_watts():
    """Dashboard status should format numeric override values as watts."""
    paths = [
        Path(__file__).parent.parent
        / "custom_components"
        / "electricity_planner"
        / "dashboard_template.yaml",
        Path(__file__).parent.parent / "electricity_planner_dashboard.yaml",
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml",
    ]

    for dashboard_path in paths:
        content = dashboard_path.read_text(encoding="utf-8")
        status_block = content.split("title: Manual override status", 1)[1].split(
            "_No manual overrides active_",
            1,
        )[0]

        assert "key not in ['arbitrage_mode', 'negative_buy_mode']" not in status_block
        assert "{% if key in ['charger_limit', 'grid_setpoint'] %}" in status_block
        assert "{{ value.value }}W" in status_block
        assert "'arbitrage_mode': 'Arbitrage mode'" not in status_block


def test_bundled_single_phase_dashboard_clear_all_targets_per_decision_overrides():
    """Bundled single-phase dashboard should clear the per-decision override group."""
    dashboard_path = Path(__file__).parent.parent / "electricity_planner_dashboard.yaml"
    content = dashboard_path.read_text(encoding="utf-8")

    assert "service: electricity_planner.clear_manual_override" in content
    assert "target: all" in content


def test_bundled_three_phase_template_rows_define_backing_entities():
    """Every template row in the bundled three-phase dashboard should define an entity."""
    dashboard_path = (
        Path(__file__).parent.parent / "electricity_planner_3phase_dashboard.yaml"
    )
    content = dashboard_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(content)

    missing_entity_rows: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            if (
                node.get("type") == "custom:template-entity-row"
                and "entity" not in node
            ):
                missing_entity_rows.append(node.get("name", "<unnamed>"))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(parsed)

    assert missing_entity_rows == []


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
        f"{entry.entry_id}_max_soc_threshold_solar": "number.custom_max_soc_solar",
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.custom_sunny_forecast_threshold",
        f"{entry.entry_id}_battery_dump_target_soc": "number.custom_arbitrage_reserve",
        f"{entry.entry_id}_arbitrage_mode_deadline_hour": "number.custom_arbitrage_deadline",
        f"{entry.entry_id}_negative_buy_threshold": "number.custom_negative_buy_threshold",
        f"{entry.entry_id}_negative_buy_mode": "switch.custom_negative_buy_mode",
    }
    template = (
        "views:\n"
        "  - cards:\n"
        "      - entity: sensor.electricity_planner_current_electricity_price\n"
        "      - entity: binary_sensor.electricity_planner_battery_charge_from_grid\n"
        "      - entity: binary_sensor.electricity_planner_car_charge_from_grid\n"
        "      - entity: number.electricity_planner_max_soc_threshold\n"
        "      - entity: number.electricity_planner_max_soc_threshold_sunny\n"
        "      - entity: number.electricity_planner_max_soc_threshold_solar\n"
        "      - entity: number.electricity_planner_sunny_forecast_threshold_kwh\n"
        "      - entity: number.electricity_planner_arbitrage_mode_reserve_soc\n"
        "      - entity: number.electricity_planner_arbitrage_mode_deadline_hour\n"
        "      - entity: number.electricity_planner_negative_buy_threshold\n"
        "      - entity: switch.electricity_planner_negative_arbitrage_buy_mode\n"
    )

    storage = FakeStorage()

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock(return_value=storage)
        ) as ensure_mock,
        patch.object(
            dashboard,
            "_async_wait_for_entity_map",
            new=AsyncMock(return_value=entity_map),
        ),
        patch.object(
            dashboard, "_async_load_template_text", new=AsyncMock(return_value=template)
        ),
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_called_once()
    config_str = json.dumps(storage.saved)
    assert "sensor.custom_price_sensor" in config_str
    assert "binary_sensor.battery_grid_allowed" in config_str
    assert "binary_sensor.car_grid_allowed" in config_str
    assert "number.custom_max_soc" in config_str
    assert "number.custom_max_soc_sunny" in config_str
    assert "number.custom_max_soc_solar" in config_str
    assert "number.custom_sunny_forecast_threshold" in config_str
    assert "number.custom_arbitrage_reserve" in config_str
    assert "number.custom_arbitrage_deadline" in config_str
    assert "number.custom_negative_buy_threshold" in config_str
    assert "switch.custom_negative_buy_mode" in config_str
    assert "number.electricity_planner_arbitrage_mode_reserve_soc" not in config_str
    assert "number.electricity_planner_negative_buy_threshold" not in config_str
    assert "switch.electricity_planner_negative_arbitrage_buy_mode" not in config_str
    assert storage.saved[dashboard.MANAGED_KEY]["entry_id"] == entry.entry_id


@pytest.mark.asyncio
async def test_dashboard_creation_appends_three_phase_cards_when_enabled():
    """Three-phase entries should receive the shared managed layout plus phase-specific cards."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Planner Instance",
        data={CONF_PHASE_MODE: PHASE_MODE_THREE},
    )
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    entity_map = {
        f"{entry.entry_id}_price_analysis": "sensor.custom_price_sensor",
        f"{entry.entry_id}_battery_grid_charging": "binary_sensor.battery_grid_allowed",
        f"{entry.entry_id}_car_grid_charging": "binary_sensor.car_grid_allowed",
        f"{entry.entry_id}_max_soc_threshold": "number.custom_max_soc",
        f"{entry.entry_id}_max_soc_threshold_sunny": "number.custom_max_soc_sunny",
        f"{entry.entry_id}_max_soc_threshold_solar": "number.custom_max_soc_solar",
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.custom_sunny_forecast_threshold",
    }
    base_template = (
        "views:\n"
        "  - cards:\n"
        "      - type: vertical-stack\n"
        "        cards:\n"
        "          - type: markdown\n"
        "            content: base\n"
    )
    appendix_template = "- type: markdown\n" "  content: |\n" "    three phase\n"
    storage = FakeStorage()

    async def _load_text(_hass, template_filename=dashboard.TEMPLATE_FILENAME):
        if template_filename == dashboard.THREE_PHASE_APPENDIX_FILENAME:
            return appendix_template
        return base_template

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock(return_value=storage)
        ),
        patch.object(
            dashboard,
            "_async_wait_for_entity_map",
            new=AsyncMock(return_value=entity_map),
        ),
        patch.object(
            dashboard,
            "_async_load_template_text",
            new=AsyncMock(side_effect=_load_text),
        ),
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    assert [view.get("title") for view in storage.saved["views"]] == [
        None,
        "Three Phase",
    ]
    cards = storage.saved["views"][1]["cards"][0]["cards"]
    assert cards[0]["content"].strip() == "three phase"


@pytest.mark.asyncio
async def test_dashboard_setup_skips_when_no_entities():
    """Dashboard creation is skipped if no entities were registered."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={})

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard, "_async_wait_for_entity_map", new=AsyncMock(return_value={})
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock()
        ) as ensure_mock,
        patch.object(dashboard, "_schedule_entity_map_retry") as retry_mock,
    ):
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

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard,
            "_async_wait_for_entity_map",
            new=AsyncMock(return_value=entity_map),
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock()
        ) as ensure_mock,
        patch.object(dashboard, "_schedule_entity_map_retry") as retry_mock,
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_not_called()
    retry_mock.assert_called_once()


@pytest.mark.asyncio
async def test_dashboard_setup_retries_when_rendered_template_has_unresolved_placeholders():
    """Dashboard creation should retry instead of saving placeholders as literal entity IDs."""
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
        "      - entity: binary_sensor.electricity_planner_solar_derating_alarm\n"
    )

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard,
            "_async_wait_for_entity_map",
            new=AsyncMock(return_value=entity_map),
        ),
        patch.object(
            dashboard, "_async_load_template_text", new=AsyncMock(return_value=template)
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock()
        ) as ensure_mock,
        patch.object(dashboard, "_schedule_entity_map_retry") as retry_mock,
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_not_called()
    retry_mock.assert_called_once()


@pytest.mark.asyncio
async def test_dashboard_setup_accepts_canonical_entity_ids_as_resolved_placeholders():
    """Canonical entity IDs should not be treated as unresolved after replacement."""
    entry = MockConfigEntry(domain=DOMAIN, title="Planner Instance", data={})
    hass = SimpleNamespace(loop=FakeLoop(), data={})
    entity_map = {
        f"{entry.entry_id}_price_analysis": "sensor.electricity_planner_current_electricity_price",
        f"{entry.entry_id}_battery_grid_charging": "binary_sensor.electricity_planner_battery_charge_from_grid",
        f"{entry.entry_id}_car_grid_charging": "binary_sensor.electricity_planner_car_charge_from_grid",
        f"{entry.entry_id}_max_soc_threshold": "number.electricity_planner_max_soc_threshold",
        f"{entry.entry_id}_max_soc_threshold_sunny": "number.electricity_planner_max_soc_threshold_sunny",
        f"{entry.entry_id}_max_soc_threshold_solar": "number.electricity_planner_max_soc_threshold_solar",
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.electricity_planner_sunny_forecast_threshold_kwh",
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

    with (
        patch.object(
            dashboard,
            "_get_lovelace_handles",
            return_value=dashboard.DashboardHandles(collection=None, dashboards={}),
        ),
        patch.object(
            dashboard,
            "_async_wait_for_entity_map",
            new=AsyncMock(return_value=entity_map),
        ),
        patch.object(
            dashboard, "_async_load_template_text", new=AsyncMock(return_value=template)
        ),
        patch.object(
            dashboard, "_ensure_dashboard_record", new=AsyncMock(return_value=storage)
        ) as ensure_mock,
        patch.object(dashboard, "_schedule_entity_map_retry") as retry_mock,
    ):
        await dashboard.async_setup_or_update_dashboard(hass, entry)

    ensure_mock.assert_called_once()
    retry_mock.assert_not_called()
    assert storage.saved is not None


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
            dashboard.MANAGED_KEY: {
                "entry_id": entry.entry_id,
                "version": dashboard.MANAGED_VERSION,
            },
        },
    )
    unmanaged_storage = FakeStorage(
        config_id="other-id",
        saved={
            "views": [],
            dashboard.MANAGED_KEY: {
                "entry_id": "different",
                "version": dashboard.MANAGED_VERSION,
            },
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

    with (
        patch.object(
            dashboard,
            "_dashboard_url_path",
            return_value="electricity-planner-planner-",
        ),
        patch.object(dashboard, "_get_lovelace_handles", return_value=handles_managed),
        patch.object(dashboard.frontend, "async_remove_panel") as remove_panel,
    ):
        await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == ["managed-id"]
    remove_panel.assert_called_once_with(hass, "electricity-planner-planner-")

    collection.deleted.clear()
    with (
        patch.object(
            dashboard,
            "_dashboard_url_path",
            return_value="electricity-planner-planner-",
        ),
        patch.object(
            dashboard, "_get_lovelace_handles", return_value=handles_unmanaged
        ),
        patch.object(dashboard.frontend, "async_remove_panel") as remove_panel,
    ):
        await dashboard.async_remove_dashboard(hass, entry)
    assert collection.deleted == []
    remove_panel.assert_called_once_with(hass, "electricity-planner-planner-")


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


@pytest.mark.asyncio
async def test_dashboard_template_includes_grid_setpoint_reason():
    """Bundled dashboard should render the grid setpoint reason attribute."""
    template = dashboard._load_template_text()

    assert "entity: sensor.electricity_planner_grid_setpoint" in template
    assert "attribute: grid_setpoint_reason" in template


@pytest.mark.asyncio
async def test_dashboard_template_includes_arbitrage_reason():
    """Bundled dashboard should render the arbitrage mode reason attribute."""
    template = dashboard._load_template_text()

    assert "entity: switch.electricity_planner_arbitrage_mode" in template
    assert "attribute: reason" in template
    assert "name: Arbitrage reason" in template


@pytest.mark.asyncio
async def test_dashboard_template_includes_inverter_derating_entities():
    """Bundled dashboard should surface the inverter derating target and alarm."""
    template = dashboard._load_template_text()

    assert "entity: sensor.electricity_planner_inverter_derating_target" in template
    assert "entity: binary_sensor.electricity_planner_solar_derating_alarm" in template
