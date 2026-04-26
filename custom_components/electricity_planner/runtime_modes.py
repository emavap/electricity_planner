"""Runtime-mode manager extracted from ``coordinator.py``.

Owns the persistent toggles that are not per-decision overrides:

* Car permissive mode (load/persist to ``_car_permissive_mode_store``)
* Arbitrage mode and Negative Arbitrage Buy mode (load/persist to
  ``_runtime_mode_store`` plus runtime state flips on the data payload so
  sensors update before the next coordinator refresh).

Underlying state attributes remain on the coordinator for test and
sensor compatibility.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_NEGATIVE_BUY_THRESHOLD,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class RuntimeModeManager:
    """Coordinate persistent runtime-mode toggles."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    # --- arbitrage mode --------------------------------------------------

    @staticmethod
    def _serialize_datetime(value) -> str | None:
        return value.isoformat() if value is not None else None

    @staticmethod
    def _parse_datetime(value: Any):
        if not value:
            return None
        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)

    @staticmethod
    def _coerce_runtime_state(payload: Any, default_reason: str) -> dict[str, Any] | None:
        if not isinstance(payload, dict) or payload.get("value") is not True:
            return None
        return {
            "value": True,
            "reason": payload.get("reason", default_reason),
            "set_at": RuntimeModeManager._parse_datetime(payload.get("set_at")),
        }

    def get_arbitrage_mode_state(self) -> dict[str, Any] | None:
        """Return the active arbitrage runtime mode state."""
        state = self._coordinator._arbitrage_mode_state
        return state if state and state.get("value") is True else None

    def is_arbitrage_mode_enabled(self) -> bool:
        """Return whether persistent arbitrage mode is enabled."""
        return self.get_arbitrage_mode_state() is not None

    async def set_arbitrage_mode(self, reason: str | None = None) -> None:
        """Persistently enable arbitrage mode."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        effective_reason = reason or "Arbitrage mode enabled"
        coordinator._arbitrage_mode_state = {
            "value": True,
            "reason": effective_reason,
            "set_at": now,
        }
        self.update_runtime_arbitrage_state(enabled=True, reason=effective_reason)
        _LOGGER.info("Arbitrage mode enabled")
        await self.persist_runtime_modes()

    async def clear_arbitrage_mode(self) -> None:
        """Disable persistent arbitrage mode."""
        coordinator = self._coordinator
        if coordinator._arbitrage_mode_state:
            _LOGGER.info("Arbitrage mode cleared")
        coordinator._arbitrage_mode_state = None
        self.update_runtime_arbitrage_state(enabled=False, reason="Arbitrage mode disabled")
        await self.persist_runtime_modes()

    def update_runtime_arbitrage_state(self, enabled: bool, reason: str) -> None:
        """Keep runtime entity state coherent while the next refresh is pending."""
        coordinator = self._coordinator
        if not isinstance(coordinator.data, dict):
            return

        reserve_soc = float(
            coordinator.config.get(
                CONF_ARBITRAGE_MODE_RESERVE_SOC,
                DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
            )
        )
        plan = {
            "enabled": enabled,
            "active": False,
            "reason": reason,
            "reserve_soc": round(min(100.0, max(0.0, reserve_soc)), 1),
            "configured_export_cap_w": 0,
            "deadline": None,
            "available_energy_kwh": 0.0,
            "required_duration_hours": 0.0,
            "slots_cover_full_arbitrage": False,
            "arbitrage_price_threshold": None,
            "current_slot_price": None,
            "selected_slots": [],
            "selected_slots_count": 0,
            "export_power": 0,
        }

        updated_data = dict(coordinator.data)
        updated_data["arbitrage_mode_plan"] = plan
        updated_data["arbitrage_mode_enabled"] = enabled
        updated_data["arbitrage_mode_active"] = False
        updated_data["arbitrage_mode_reason"] = reason
        updated_data["arbitrage_mode_reserve_soc"] = plan["reserve_soc"]
        updated_data["arbitrage_mode_export_power"] = 0
        coordinator.async_set_updated_data(updated_data)

    # --- negative arbitrage buy mode -------------------------------------

    def get_negative_buy_mode_state(self) -> dict[str, Any] | None:
        """Return the active Negative Arbitrage Buy runtime mode state."""
        state = self._coordinator._negative_buy_mode_state
        return state if state and state.get("value") is True else None

    def is_negative_buy_mode_enabled(self) -> bool:
        """Return whether persistent Negative Arbitrage Buy mode is enabled."""
        return self.get_negative_buy_mode_state() is not None

    async def set_negative_buy_mode(self, reason: str | None = None) -> None:
        """Persistently enable Negative Arbitrage Buy mode."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        effective_reason = reason or "Negative Arbitrage Buy mode enabled"
        coordinator._negative_buy_mode_state = {
            "value": True,
            "reason": effective_reason,
            "set_at": now,
        }
        self.update_runtime_negative_buy_state(enabled=True, reason=effective_reason)
        _LOGGER.info("Negative Arbitrage Buy mode enabled")
        await self.persist_runtime_modes()

    async def clear_negative_buy_mode(self) -> None:
        """Disable persistent Negative Arbitrage Buy mode."""
        coordinator = self._coordinator
        if coordinator._negative_buy_mode_state:
            _LOGGER.info("Negative Arbitrage Buy mode cleared")
        coordinator._negative_buy_mode_state = None
        self.update_runtime_negative_buy_state(
            enabled=False, reason="Negative Arbitrage Buy mode disabled"
        )
        await self.persist_runtime_modes()

    def update_runtime_negative_buy_state(self, enabled: bool, reason: str) -> None:
        """Keep runtime entity state coherent while the next refresh is pending."""
        coordinator = self._coordinator
        if not isinstance(coordinator.data, dict):
            return

        threshold = float(
            coordinator.config.get(
                CONF_NEGATIVE_BUY_THRESHOLD,
                DEFAULT_NEGATIVE_BUY_THRESHOLD,
            )
        )
        plan = {
            "enabled": enabled,
            "active": False,
            "solar_curtail_active": False,
            "reason": reason,
            "threshold": round(threshold, 4),
            "deadline": None,
            "required_energy_kwh": 0.0,
            "required_duration_hours": 0.0,
            "slots_cover_full_charge": False,
            "buy_price_threshold": None,
            "current_slot_price": None,
            "selected_slots": [],
            "selected_slots_count": 0,
            "import_power": 0,
            "configured_import_cap_w": 0,
        }

        updated_data = dict(coordinator.data)
        updated_data["negative_buy_plan"] = plan
        updated_data["negative_buy_mode_enabled"] = enabled
        updated_data["negative_buy_mode_active"] = False
        updated_data["negative_buy_mode_reason"] = reason
        updated_data["negative_buy_import_power"] = 0
        updated_data["negative_buy_curtail_solar"] = False
        coordinator.async_set_updated_data(updated_data)

    async def load_runtime_modes(self) -> None:
        """Load persisted runtime mode toggles from their dedicated store.

        Older versions stored these toggles in the manual override store. Keep a
        compatibility read here; the manual override loader will later rewrite
        that store without the legacy runtime-mode keys.
        """
        coordinator = self._coordinator
        stored: Any = None
        loaded_from_legacy = False

        if coordinator._runtime_mode_store is not None:
            try:
                stored = await coordinator._runtime_mode_store.async_load()
            except Exception as err:
                if isinstance(err, (KeyboardInterrupt, SystemExit)):
                    raise
                _LOGGER.warning(
                    "Unable to load runtime modes for %s: %s",
                    coordinator.entry.entry_id,
                    err,
                )

        if not isinstance(stored, dict) and coordinator._manual_override_store is not None:
            try:
                legacy = await coordinator._manual_override_store.async_load()
            except Exception as err:
                if isinstance(err, (KeyboardInterrupt, SystemExit)):
                    raise
                _LOGGER.warning(
                    "Unable to load legacy runtime modes for %s: %s",
                    coordinator.entry.entry_id,
                    err,
                )
                legacy = None
            legacy_overrides = legacy.get("overrides", legacy) if isinstance(legacy, dict) else None
            if isinstance(legacy_overrides, dict):
                stored = {
                    "arbitrage_mode": legacy_overrides.get("arbitrage_mode")
                    or legacy_overrides.get("battery_dump")
                    or legacy_overrides.get("battery_dump_to_grid"),
                    "negative_buy_mode": legacy_overrides.get("negative_buy_mode"),
                }
                loaded_from_legacy = True

        if not isinstance(stored, dict):
            return

        serialized_modes = stored.get("modes", stored)
        if not isinstance(serialized_modes, dict):
            return

        coordinator._arbitrage_mode_state = self._coerce_runtime_state(
            serialized_modes.get("arbitrage_mode"),
            "Arbitrage mode enabled",
        )
        coordinator._negative_buy_mode_state = self._coerce_runtime_state(
            serialized_modes.get("negative_buy_mode"),
            "Negative Arbitrage Buy mode enabled",
        )

        if loaded_from_legacy:
            await self.persist_runtime_modes()

    async def persist_runtime_modes(self) -> None:
        """Persist runtime mode toggles for restart recovery."""
        coordinator = self._coordinator
        if coordinator._runtime_mode_store is None:
            return

        modes_to_save: dict[str, dict[str, Any]] = {}
        if coordinator._arbitrage_mode_state:
            modes_to_save["arbitrage_mode"] = {
                "value": True,
                "reason": coordinator._arbitrage_mode_state.get("reason"),
                "set_at": self._serialize_datetime(coordinator._arbitrage_mode_state.get("set_at")),
            }
        if coordinator._negative_buy_mode_state:
            modes_to_save["negative_buy_mode"] = {
                "value": True,
                "reason": coordinator._negative_buy_mode_state.get("reason"),
                "set_at": self._serialize_datetime(
                    coordinator._negative_buy_mode_state.get("set_at")
                ),
            }

        await coordinator._runtime_mode_store.async_save({"modes": modes_to_save})

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
