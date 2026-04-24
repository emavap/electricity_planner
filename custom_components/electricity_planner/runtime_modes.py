"""Runtime-mode manager extracted from ``coordinator.py``.

Owns the persistent toggles that are not per-decision overrides:

* Car permissive mode (load/persist to ``_car_permissive_mode_store``)
* Battery-dump (a.k.a. arbitrage) mode, which is implemented as an
  ``arbitrage_mode`` entry in ``_manual_overrides`` plus a runtime state
  flip on the data payload so sensors update before the next coordinator
  refresh.

Underlying state attributes remain on the coordinator for test and
sensor compatibility.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import CONF_BATTERY_DUMP_TARGET_SOC, DEFAULT_BATTERY_DUMP_TARGET_SOC

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class RuntimeModeManager:
    """Coordinate persistent runtime-mode toggles."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    # --- battery-dump / arbitrage mode -----------------------------------

    def is_battery_dump_mode_enabled(self) -> bool:
        """Return whether persistent arbitrage mode is enabled."""
        override = self._coordinator.get_manual_override("arbitrage_mode")
        return bool(override and override.get("value") is True)

    async def set_battery_dump_mode(self, reason: str | None = None) -> None:
        """Persistently enable arbitrage mode."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        effective_reason = reason or "Arbitrage mode enabled"
        coordinator._manual_overrides["arbitrage_mode"] = {
            "value": True,
            "reason": effective_reason,
            "expires_at": None,
            "set_at": now,
        }
        self.update_runtime_arbitrage_state(enabled=True, reason=effective_reason)
        _LOGGER.info("Arbitrage mode enabled")
        await coordinator._manual_override_manager.persist()

    async def clear_battery_dump_mode(self) -> None:
        """Disable persistent arbitrage mode."""
        coordinator = self._coordinator
        if coordinator._manual_overrides.get("arbitrage_mode"):
            _LOGGER.info("Arbitrage mode cleared")
        coordinator._manual_overrides["arbitrage_mode"] = None
        self.update_runtime_arbitrage_state(enabled=False, reason="Arbitrage mode disabled")
        await coordinator._manual_override_manager.persist()

    def update_runtime_arbitrage_state(self, enabled: bool, reason: str) -> None:
        """Keep runtime entity state coherent while the next refresh is pending."""
        coordinator = self._coordinator
        if not isinstance(coordinator.data, dict):
            return

        target_soc = float(
            coordinator.config.get(
                CONF_BATTERY_DUMP_TARGET_SOC,
                DEFAULT_BATTERY_DUMP_TARGET_SOC,
            )
        )
        plan = {
            "enabled": enabled,
            "active": False,
            "reason": reason,
            "target_soc": round(min(100.0, max(0.0, target_soc)), 1),
            "configured_export_cap_w": 0,
            "deadline": None,
            "available_energy_kwh": 0.0,
            "required_duration_hours": 0.0,
            "slots_cover_full_dump": False,
            "dump_price_threshold": None,
            "current_slot_price": None,
            "selected_slots": [],
            "selected_slots_count": 0,
            "export_power": 0,
        }

        updated_data = dict(coordinator.data)
        updated_data["battery_dump_plan"] = plan
        updated_data["arbitrage_mode_enabled"] = enabled
        updated_data["arbitrage_mode_active"] = False
        updated_data["arbitrage_mode_reason"] = reason
        updated_data["battery_dump_target_soc"] = plan["target_soc"]
        updated_data["battery_dump_export_power"] = 0
        coordinator.async_set_updated_data(updated_data)

    # --- car permissive mode ---------------------------------------------

    async def set_car_permissive_mode(self, reason: str | None = None) -> None:
        """Persistently enable car permissive charging mode."""
        coordinator = self._coordinator
        coordinator._car_permissive_mode_active = True
        coordinator._car_permissive_mode_has_persisted_state = True
        _LOGGER.info("Car permissive mode enabled")
        await self.persist_car_permissive_mode(reason=reason)

    async def clear_car_permissive_mode(self, reason: str | None = None) -> None:
        """Disable persistent car permissive charging mode."""
        coordinator = self._coordinator
        coordinator._car_permissive_mode_active = False
        coordinator._car_permissive_mode_has_persisted_state = True
        _LOGGER.info("Car permissive mode cleared")
        await self.persist_car_permissive_mode(reason=reason)

    async def load_car_permissive_mode(self) -> None:
        """Load persisted car permissive mode from storage."""
        coordinator = self._coordinator
        if coordinator._car_permissive_mode_store is None:
            return

        try:
            stored = await coordinator._car_permissive_mode_store.async_load()
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to load car permissive mode for %s: %s",
                coordinator.entry.entry_id,
                err,
            )
            return

        if stored is None:
            coordinator._car_permissive_mode_has_persisted_state = False
            return

        enabled: Any
        if isinstance(stored, dict):
            enabled = stored.get("enabled", stored.get("value"))
        else:
            enabled = stored

        if enabled is None:
            coordinator._car_permissive_mode_has_persisted_state = False
            return

        coordinator._car_permissive_mode_active = bool(enabled)
        coordinator._car_permissive_mode_has_persisted_state = True
        _LOGGER.debug(
            "Loaded car permissive mode for %s: %s",
            coordinator.entry.entry_id,
            coordinator._car_permissive_mode_active,
        )

    async def persist_car_permissive_mode(self, reason: str | None = None) -> None:
        """Persist car permissive mode for restart recovery."""
        coordinator = self._coordinator
        if coordinator._car_permissive_mode_store is None:
            return

        await coordinator._car_permissive_mode_store.async_save(
            {
                "enabled": coordinator._car_permissive_mode_active,
                "reason": reason
                or (
                    "Car permissive mode enabled"
                    if coordinator._car_permissive_mode_active
                    else "Car permissive mode disabled"
                ),
                "set_at": coordinator._manual_override_manager.serialize_stored_datetime(
                    dt_util.utcnow()
                ),
            }
        )
