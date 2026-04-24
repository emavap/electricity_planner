"""Entity state value + status reporting collaborator.

Extracted from ``coordinator.py``. Provides safe numeric reads of HA
entity states and a structured status report (per entity + summary) used
by diagnostic sensors.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_P1_TARIFF_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASES,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    PHASE_IDS,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

_NUMERIC_PREFIX_RE = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)")


class EntityStatusReporter:
    """Reads numeric entity states and produces diagnostic status reports."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    async def get_state_value(self, entity_id: str | None) -> float | None:
        """Get numeric state value from entity."""
        if not entity_id:
            return None

        state = self._coordinator.hass.states.get(entity_id)
        if not state or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        raw_value = str(state.state).strip()
        try:
            return float(raw_value)
        except (ValueError, TypeError):
            # Accept common localized/unit-appended variants like "13,2" or "13.2 kWh".
            match = _NUMERIC_PREFIX_RE.match(raw_value)
            if match:
                candidate = match.group(1).replace(",", ".")
                try:
                    return float(candidate)
                except (ValueError, TypeError):
                    pass

            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug("Could not convert state to float: %s = %s", entity_id, state.state)
            return None

    def get_entity_status(
        self, entity_id: str | None, is_required: bool = False
    ) -> dict[str, Any]:
        """Get detailed status for a configured entity."""
        if not entity_id:
            return {"configured": False, "status": "not_configured", "is_required": is_required}

        state = self._coordinator.hass.states.get(entity_id)
        if not state:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "missing",
                "is_required": is_required,
                "reason": "Entity not found in Home Assistant",
            }

        if state.state == STATE_UNAVAILABLE:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "unavailable",
                "is_required": is_required,
                "reason": "Entity is unavailable",
            }

        if state.state == STATE_UNKNOWN:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "unknown",
                "is_required": is_required,
                "reason": "Entity state is unknown",
            }

        try:
            float(state.state)
        except (ValueError, TypeError):
            pass
        return {
            "configured": True,
            "entity_id": entity_id,
            "status": "available",
            "is_required": is_required,
            "current_value": state.state,
        }

    def get_all_entity_statuses(self) -> dict[str, Any]:
        """Get status of all configured entities, organized by category."""
        coordinator = self._coordinator
        config = coordinator.config
        phase_mode = config.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)

        price_entities = {
            "current_price": self.get_entity_status(
                config.get(CONF_CURRENT_PRICE_ENTITY), is_required=True
            ),
            "highest_price": self.get_entity_status(
                config.get(CONF_HIGHEST_PRICE_ENTITY), is_required=True
            ),
            "lowest_price": self.get_entity_status(
                config.get(CONF_LOWEST_PRICE_ENTITY), is_required=True
            ),
            "next_price": self.get_entity_status(
                config.get(CONF_NEXT_PRICE_ENTITY), is_required=False
            ),
        }

        battery_entities: dict[str, Any] = {}
        for entity_id in config.get(CONF_BATTERY_SOC_ENTITIES, []):
            battery_entities[entity_id] = self.get_entity_status(entity_id, is_required=False)

        power_entities: dict[str, Any] = {}
        if phase_mode == PHASE_MODE_THREE:
            phases_config = config.get(CONF_PHASES, {})
            for phase_id in PHASE_IDS:
                phase_config = phases_config.get(phase_id, {})
                power_entities[f"{phase_id}_solar"] = self.get_entity_status(
                    phase_config.get(CONF_PHASE_SOLAR_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_consumption"] = self.get_entity_status(
                    phase_config.get(CONF_PHASE_CONSUMPTION_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_car"] = self.get_entity_status(
                    phase_config.get(CONF_PHASE_CAR_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_battery_power"] = self.get_entity_status(
                    phase_config.get(CONF_PHASE_BATTERY_POWER_ENTITY), is_required=False
                )
        else:
            power_entities["solar_production"] = self.get_entity_status(
                config.get(CONF_SOLAR_PRODUCTION_ENTITY), is_required=False
            )
            power_entities["house_consumption"] = self.get_entity_status(
                config.get(CONF_HOUSE_CONSUMPTION_ENTITY), is_required=False
            )
            power_entities["car_charging_power"] = self.get_entity_status(
                config.get(CONF_CAR_CHARGING_POWER_ENTITY), is_required=False
            )

        optional_entities: dict[str, Any] = {
            "monthly_grid_peak": self.get_entity_status(
                config.get(CONF_MONTHLY_GRID_PEAK_ENTITY), is_required=False
            ),
            "grid_power": self.get_entity_status(
                config.get(CONF_GRID_POWER_ENTITY), is_required=False
            ),
        }
        if coordinator._has_builtin_transport_cost():
            optional_entities["p1_tariff"] = self.get_entity_status(
                config.get(CONF_P1_TARIFF_ENTITY), is_required=False
            )
        else:
            optional_entities["transport_cost"] = self.get_entity_status(
                config.get(CONF_TRANSPORT_COST_ENTITY), is_required=False
            )

        all_entities: list[dict[str, Any]] = []
        for category in [price_entities, battery_entities, power_entities, optional_entities]:
            all_entities.extend(category.values())

        configured_entities = [e for e in all_entities if e.get("configured")]
        available_count = sum(1 for e in configured_entities if e.get("status") == "available")
        unavailable_count = sum(
            1 for e in configured_entities if e.get("status") in ("unavailable", "unknown", "missing")
        )
        required_unavailable = [
            e.get("entity_id") for e in configured_entities
            if e.get("is_required") and e.get("status") in ("unavailable", "unknown", "missing")
        ]

        return {
            "price_entities": price_entities,
            "battery_entities": battery_entities,
            "power_entities": power_entities,
            "optional_entities": optional_entities,
            "summary": {
                "total_configured": len(configured_entities),
                "available": available_count,
                "unavailable": unavailable_count,
                "required_unavailable": required_unavailable,
                "all_required_available": len(required_unavailable) == 0,
            },
        }
