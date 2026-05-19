"""Shared test fixtures.

Dev-tooling shim: newer Home Assistant (>=2025.x) requires the frame helper
to be initialised before integration code can call `report_usage`. Tests in
this repo build isolated fake objects (no `hass` fixture) so the helper is
never wired up. We stub a minimal HA-like object on the module-level
container so `report_usage` no-ops instead of raising RuntimeError.

Do not change integration logic to work around this; this conftest is
purely a test-environment compatibility shim.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.helpers import frame


def _ensure_current_event_loop() -> asyncio.AbstractEventLoop:
    """Provide a current loop for legacy HA/pytest fixtures on Python 3.13."""
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


def pytest_runtest_setup(item):
    """Make asyncio.get_event_loop() usable during fixture setup."""
    _ensure_current_event_loop()


@pytest.fixture(autouse=True)
def _ha_frame_helper_stub(monkeypatch):
    """Make frame.report_usage a no-op for isolated unit tests."""
    monkeypatch.setattr(frame, "report_usage", lambda *a, **kw: None)
    yield
