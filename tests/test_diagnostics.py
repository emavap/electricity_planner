"""Diagnostics tests for Electricity Planner."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.diagnostics import (
    async_get_config_entry_diagnostics,
)


class DummyHass:
    def __init__(self) -> None:
        self.data: dict = {}


@pytest.mark.asyncio
async def test_async_get_config_entry_diagnostics_basic():
    hass = DummyHass()
    entry = MockConfigEntry(domain=DOMAIN, data={"foo": "bar"}, options={"opt": 1})

    coordinator = SimpleNamespace(
        data={
            "price_analysis": {"current_price": 0.12},
            "strategy_trace": [],
        },
        last_successful_update="2025-01-01T00:00:00+00:00",
        data_unavailable_since=None,
        notification_sent=False,
    )

    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["config_entry"]["data"] == {"foo": "bar"}
    assert diagnostics["config_entry"]["options"] == {"opt": 1}
    assert diagnostics["diagnostics"]["price_analysis"]["current_price"] == 0.12


@pytest.mark.asyncio
async def test_diagnostics_report_the_live_energy_ledger():
    """Diagnostics must show why arbitrage export is or is not eligible."""
    hass = DummyHass()
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})

    coordinator = SimpleNamespace(
        data={
            "arbitrage_mode_plan": {"ledger_exportable_kwh": 1.8},
            # A stale snapshot taken before the last refresh.
            "energy_ledger": {"status": "stale"},
        },
        energy_ledger_diagnostics=lambda: {
            "initialized": True,
            "status": "charged_grid",
            "lot_count": 2,
            "energy_by_origin": {"unknown": 8.0, "grid": 2.0},
        },
        last_successful_update=None,
        data_unavailable_since=None,
        notification_sent=False,
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["diagnostics"]["arbitrage_mode_plan"] == {
        "ledger_exportable_kwh": 1.8
    }
    # The live ledger supersedes the snapshot stored in coordinator.data.
    assert diagnostics["diagnostics"]["energy_ledger"]["status"] == "charged_grid"
    assert diagnostics["diagnostics"]["energy_ledger"]["energy_by_origin"] == {
        "unknown": 8.0,
        "grid": 2.0,
    }


@pytest.mark.asyncio
async def test_diagnostics_tolerate_a_coordinator_without_a_ledger():
    hass = DummyHass()
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    coordinator = SimpleNamespace(
        data={"price_analysis": {}},
        last_successful_update=None,
        data_unavailable_since=None,
        notification_sent=False,
    )
    hass.data[DOMAIN] = {entry.entry_id: coordinator}

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert "energy_ledger" not in diagnostics["diagnostics"]
