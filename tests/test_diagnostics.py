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
