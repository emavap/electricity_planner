"""Switch platform for Electricity Planner."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DOMAIN,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Electricity Planner switch entities."""
    coordinator: ElectricityPlannerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        CarPermissiveModeSwitch(coordinator, entry),
        BatteryChargingDisableSwitch(coordinator, entry),
        ArbitrageModeSwitch(coordinator, entry),
        NegativeArbitrageBuyModeSwitch(coordinator, entry),
    ]

    async_add_entities(entities)


class CarPermissiveModeSwitch(CoordinatorEntity, RestoreEntity, SwitchEntity):
    """Switch to enable permissive car charging mode (higher price threshold)."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the permissive mode switch."""
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Car Permissive Mode"
        self._attr_unique_id = f"{entry.entry_id}_car_permissive_mode"
        self._attr_icon = "mdi:car-electric-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if permissive mode is enabled."""
        return self.coordinator._car_permissive_mode_active

    async def async_added_to_hass(self) -> None:
        """Restore the last switch state after Home Assistant restarts."""
        await super().async_added_to_hass()

        if self.coordinator._car_permissive_mode_has_persisted_state:
            return

        last_state = await self.async_get_last_state()
        if last_state is None:
            return

        restored_state = last_state.state == STATE_ON
        if self.coordinator._car_permissive_mode_active == restored_state:
            return

        _LOGGER.info(
            "Restored car permissive charging mode to %s",
            "on" if restored_state else "off",
        )
        if restored_state:
            await self.coordinator.async_set_car_permissive_mode(
                reason="Restored car permissive mode from entity state"
            )
        else:
            await self.coordinator.async_clear_car_permissive_mode(
                reason="Restored car permissive mode from entity state"
            )
        await self.coordinator.async_request_refresh()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        multiplier = self.coordinator.config.get(
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
        )
        increase_pct = (multiplier - 1.0) * 100

        return {
            "threshold_multiplier": multiplier,
            "threshold_increase_percent": f"{increase_pct:.0f}%",
            "description": (
                f"When enabled, car charging threshold is increased by {increase_pct:.0f}% "
                "to allow charging at moderately higher prices"
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on permissive mode.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Enabling car permissive charging mode")
        await self.coordinator.async_set_car_permissive_mode(
            reason="Manual permissive mode via dashboard switch"
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off permissive mode.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Disabling car permissive charging mode")
        await self.coordinator.async_clear_car_permissive_mode(
            reason="Manual permissive mode via dashboard switch"
        )
        await self.coordinator.async_request_refresh()


class BatteryChargingDisableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to manually disable battery grid charging.

    When ON, battery charging from grid is forcibly disabled regardless of
    price conditions. Works for both single-phase and three-phase configurations.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the battery charging disable switch."""
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Disable Battery Charging"
        self._attr_unique_id = f"{entry.entry_id}_disable_battery_charging"
        self._attr_icon = "mdi:battery-off"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if battery charging is disabled (override active)."""
        override = self.coordinator.get_manual_override("battery_grid_charging")
        if override is None:
            return False
        # Check if the override is forcing charging OFF (disabled)
        return override.get("value") is False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        override = self.coordinator.get_manual_override("battery_grid_charging")
        if override and override.get("value") is False:
            set_at = override.get("set_at")
            expires_at = override.get("expires_at")
            return {
                "override_active": True,
                "reason": override.get("reason", "Manual disable"),
                "set_at": set_at.isoformat() if set_at else None,
                "expires_at": expires_at.isoformat() if expires_at else "Never",
                "description": (
                    "Battery charging from grid is manually disabled. "
                    "Turn off this switch to resume automatic charging decisions."
                ),
            }
        return {
            "override_active": False,
            "description": (
                "Turn on to manually prevent battery from charging from the grid. "
                "This overrides all automatic charging decisions."
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on to disable battery charging.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Manually disabling battery grid charging")
        await self.coordinator.async_set_manual_override(
            target="battery",
            value=False,  # Force charging OFF
            duration=None,  # No expiration
            reason="Manual disable via dashboard switch",
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off to resume automatic charging decisions.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Clearing manual battery charging disable - resuming automatic mode")
        await self.coordinator.async_clear_manual_override(target="battery")
        await self.coordinator.async_request_refresh()


class ArbitrageModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable persistent arbitrage mode."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the arbitrage mode switch."""
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Arbitrage Mode"
        self._attr_unique_id = f"{entry.entry_id}_arbitrage_mode"
        self._attr_icon = "mdi:battery-arrow-down-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if arbitrage mode is enabled."""
        return self.coordinator.is_arbitrage_mode_enabled()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        arbitrage_plan = self.coordinator.data.get("arbitrage_mode_plan", {}) if self.coordinator.data else {}
        runtime_state = self.coordinator.get_arbitrage_mode_state()
        mode_enabled = runtime_state is not None
        set_at = runtime_state.get("set_at") if runtime_state else None
        effective_plan = arbitrage_plan if mode_enabled and arbitrage_plan.get("enabled") else {}
        reason = (
            effective_plan.get("reason")
            or (runtime_state.get("reason") if runtime_state else None)
            or "Arbitrage mode disabled"
        )

        return {
            "mode_enabled": mode_enabled,
            "reason": reason,
            "reserve_soc": effective_plan.get("reserve_soc"),
            "currently_exporting": effective_plan.get("active", False),
            "arbitrage_price_threshold": effective_plan.get("arbitrage_price_threshold"),
            "current_slot_price": effective_plan.get("current_slot_price"),
            "slots_cover_full_arbitrage": effective_plan.get("slots_cover_full_arbitrage"),
            "selected_slots_count": effective_plan.get("selected_slots_count", 0),
            "selected_slots": effective_plan.get("selected_slots", []),
            "deadline": effective_plan.get("deadline"),
            "export_power": effective_plan.get("export_power"),
            "configured_export_cap_w": effective_plan.get("configured_export_cap_w"),
            "available_energy_kwh": effective_plan.get("available_energy_kwh"),
            "set_at": set_at.isoformat() if set_at else None,
            "description": (
                "When enabled, the planner derives an arbitrage price threshold from the highest "
                "eligible feed-in slots and exports while the current feed-in price is at or above that threshold."
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on arbitrage mode."""
        _LOGGER.info("Enabling arbitrage mode")
        await self.coordinator.async_set_arbitrage_mode(
            reason="Manual arbitrage mode via dashboard switch",
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off arbitrage mode."""
        _LOGGER.info("Clearing arbitrage mode")
        await self.coordinator.async_clear_arbitrage_mode()
        await self.coordinator.async_request_refresh()


class NegativeArbitrageBuyModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable persistent Negative Arbitrage Buy mode."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the negative arbitrage buy mode switch."""
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Negative Arbitrage Buy Mode"
        self._attr_unique_id = f"{entry.entry_id}_negative_buy_mode"
        self._attr_icon = "mdi:battery-arrow-up-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if Negative Arbitrage Buy mode is enabled."""
        return self.coordinator.is_negative_buy_mode_enabled()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        buy_plan = (
            self.coordinator.data.get("negative_buy_plan", {})
            if self.coordinator.data
            else {}
        )
        runtime_state = self.coordinator.get_negative_buy_mode_state()
        mode_enabled = runtime_state is not None
        set_at = runtime_state.get("set_at") if runtime_state else None
        effective_plan = buy_plan if mode_enabled and buy_plan.get("enabled") else {}
        reason = (
            effective_plan.get("reason")
            or (runtime_state.get("reason") if runtime_state else None)
            or "Negative Arbitrage Buy mode disabled"
        )

        return {
            "mode_enabled": mode_enabled,
            "reason": reason,
            "threshold": effective_plan.get("threshold"),
            "currently_buying": effective_plan.get("active", False),
            "buy_price_threshold": effective_plan.get("buy_price_threshold"),
            "current_slot_price": effective_plan.get("current_slot_price"),
            "slots_cover_full_charge": effective_plan.get("slots_cover_full_charge"),
            "selected_slots_count": effective_plan.get("selected_slots_count", 0),
            "selected_slots": effective_plan.get("selected_slots", []),
            "deadline": effective_plan.get("deadline"),
            "import_power": effective_plan.get("import_power"),
            "configured_import_cap_w": effective_plan.get("configured_import_cap_w"),
            "required_energy_kwh": effective_plan.get("required_energy_kwh"),
            "required_duration_hours": effective_plan.get("required_duration_hours"),
            "set_at": set_at.isoformat() if set_at else None,
            "description": (
                "When enabled, the planner requests grid import for every slot at or below "
                "the configured negative-price threshold and curtails solar during those "
                "paid-to-consume periods."
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on Negative Arbitrage Buy mode."""
        _LOGGER.info("Enabling Negative Arbitrage Buy mode")
        await self.coordinator.async_set_negative_buy_mode(
            reason="Manual Negative Arbitrage Buy mode via dashboard switch",
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off Negative Arbitrage Buy mode."""
        _LOGGER.info("Clearing Negative Arbitrage Buy mode")
        await self.coordinator.async_clear_negative_buy_mode()
        await self.coordinator.async_request_refresh()
