"""Manual-override manager extracted from ``coordinator.py``.

Owns the apply/load/persist logic for user-supplied overrides that force
battery/car charging decisions, charger limits, or grid setpoints. The
underlying ``_manual_overrides`` dict and storage handle remain on the
coordinator so tests and sensors that read those attributes keep
working.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

_TARGET_MAPPING: dict[str, tuple[str, ...]] = {
    "battery": ("battery_grid_charging",),
    "car": ("car_grid_charging",),
    "both": ("battery_grid_charging", "car_grid_charging"),
    "charger_limit": ("charger_limit",),
    "grid_setpoint": ("grid_setpoint",),
    "all": (
        "battery_grid_charging",
        "car_grid_charging",
        "charger_limit",
        "grid_setpoint",
    ),
}


class ManualOverrideManager:
    """Manage persisted manual overrides and apply them to decisions."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    @staticmethod
    def serialize_stored_datetime(value: datetime | None) -> str | None:
        """Convert datetime to an ISO string for storage."""
        return value.isoformat() if value is not None else None

    @staticmethod
    def parse_stored_datetime(value: Any) -> datetime | None:
        """Parse a datetime loaded from storage."""
        if not value:
            return None
        parsed = dt_util.parse_datetime(str(value))
        if parsed is None:
            return None
        return dt_util.as_utc(parsed)

    def resolve_targets(self, target: str) -> tuple[str, ...]:
        """Resolve override target string into coordinator keys."""
        return _TARGET_MAPPING.get(target, ())

    def get(self, key: str) -> dict[str, Any] | None:
        """Get the active manual override for ``key``, or ``None`` if expired/missing."""
        coordinator = self._coordinator
        override = coordinator._manual_overrides.get(key)
        if not override:
            return None

        expires_at: datetime | None = override.get("expires_at")
        if expires_at and expires_at <= dt_util.utcnow():
            coordinator._manual_overrides[key] = None
            _LOGGER.debug("Removed expired manual override on read: %s", key)
            self.schedule_persist()
            return None

        return override

    async def load(self) -> None:
        """Load persisted manual overrides from storage."""
        coordinator = self._coordinator
        if coordinator._manual_override_store is None:
            return

        try:
            stored = await coordinator._manual_override_store.async_load()
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to load manual overrides for %s: %s",
                coordinator.entry.entry_id,
                err,
            )
            return

        if not isinstance(stored, dict):
            return

        serialized_overrides = stored.get("overrides", stored)
        if not isinstance(serialized_overrides, dict):
            return

        overrides_changed = False
        loaded_overrides: dict[str, dict[str, Any] | None] = {
            key: None for key in coordinator._manual_overrides
        }

        for key, payload in serialized_overrides.items():
            if key not in loaded_overrides or not isinstance(payload, dict):
                overrides_changed = True
                continue

            value = payload.get("value")
            if value is None:
                overrides_changed = True
                continue

            expires_at = self.parse_stored_datetime(payload.get("expires_at"))
            if expires_at is not None and expires_at <= dt_util.utcnow():
                overrides_changed = True
                continue

            loaded_overrides[key] = {
                "value": value,
                "reason": payload.get("reason", "Manual override"),
                "set_at": self.parse_stored_datetime(payload.get("set_at")),
                "expires_at": expires_at,
            }

        coordinator._manual_overrides.update(loaded_overrides)

        if overrides_changed:
            await self.persist()

    async def persist(self) -> None:
        """Persist active manual overrides for restart recovery."""
        coordinator = self._coordinator
        if coordinator._manual_override_store is None:
            return

        overrides_to_save: dict[str, dict[str, Any]] = {}
        for key, override in coordinator._manual_overrides.items():
            if not override:
                continue
            overrides_to_save[key] = {
                "value": override.get("value"),
                "reason": override.get("reason"),
                "set_at": self.serialize_stored_datetime(override.get("set_at")),
                "expires_at": self.serialize_stored_datetime(
                    override.get("expires_at")
                ),
            }

        await coordinator._manual_override_store.async_save(
            {"overrides": overrides_to_save}
        )

    def schedule_persist(self) -> None:
        """Persist manual overrides without blocking sync callers."""
        coordinator = self._coordinator
        if coordinator._manual_override_store is None:
            return
        coordinator.hass.async_create_task(self.persist())

    async def set_override(
        self,
        target: str,
        value: bool | None,
        duration: timedelta | None,
        reason: str | None,
        charger_limit: int | None = None,
        grid_setpoint: int | None = None,
    ) -> None:
        """Apply a manual override for battery/car decisions, charger limit, or grid setpoint."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        expires_at = now + duration if duration is not None else None
        resolved_targets = set(self.resolve_targets(target))

        if value is not None:
            manual_reason = reason or ("force charge" if value else "force wait")
            for coordinator_key in resolved_targets:
                if coordinator_key in ("battery_grid_charging", "car_grid_charging"):
                    coordinator._manual_overrides[coordinator_key] = {
                        "value": value,
                        "reason": manual_reason,
                        "expires_at": expires_at,
                        "set_at": now,
                    }
                    _LOGGER.info(
                        "Manual override set for %s → %s (expires %s)",
                        coordinator_key,
                        value,
                        expires_at.isoformat() if expires_at else "never",
                    )

        # Numeric overrides apply whenever the caller passes a value, regardless
        # of which boolean ``target`` was named.  The dashboard "Force Car
        # Charging" prompt bundles ``charger_limit`` and ``grid_setpoint`` into
        # the same ``target='car'`` service call, so gating these on the target
        # string would silently drop user input.
        if charger_limit is not None:
            coordinator._manual_overrides["charger_limit"] = {
                "value": charger_limit,
                "reason": reason or "Manual charger limit override",
                "expires_at": expires_at,
                "set_at": now,
            }
            _LOGGER.info(
                "Manual override set for charger_limit → %dW (expires %s)",
                charger_limit,
                expires_at.isoformat() if expires_at else "never",
            )

        if grid_setpoint is not None:
            coordinator._manual_overrides["grid_setpoint"] = {
                "value": grid_setpoint,
                "reason": reason or "Manual grid setpoint override",
                "expires_at": expires_at,
                "set_at": now,
            }
            _LOGGER.info(
                "Manual override set for grid_setpoint → %dW (expires %s)",
                grid_setpoint,
                expires_at.isoformat() if expires_at else "never",
            )

        await self.persist()

    async def clear(self, target: str | None = None) -> None:
        """Clear manual overrides for the given target (or all)."""
        coordinator = self._coordinator
        effective_target = target or "all"
        for coordinator_key in self.resolve_targets(effective_target):
            if coordinator._manual_overrides.get(coordinator_key):
                _LOGGER.info("Manual override cleared for %s", coordinator_key)
            coordinator._manual_overrides[coordinator_key] = None
        await self.persist()

    def apply(self, decision: dict[str, Any]) -> tuple[dict[str, Any], set[str]]:
        """Apply active manual overrides to the decision payload."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        overrides_info: dict[str, Any] = {}
        base_trace = decision.get("strategy_trace") or []
        augmented_trace = list(base_trace)
        changed_targets: set[str] = set()

        expired_keys: list[str] = []

        for coordinator_key, override in coordinator._manual_overrides.items():
            if not override:
                continue

            expires_at: datetime | None = override.get("expires_at")
            if expires_at and expires_at <= now:
                expired_keys.append(coordinator_key)
                continue

            override_value = override["value"]
            manual_reason: str = override.get("reason", "Manual override")

            if coordinator_key in ("charger_limit", "grid_setpoint"):
                previous_value = decision.get(coordinator_key)
                decision[coordinator_key] = override_value
                if previous_value != override_value:
                    changed_targets.add(coordinator_key)

                reason_key = f"{coordinator_key}_reason"
                existing_reason = decision.get(reason_key)
                if existing_reason:
                    decision[reason_key] = (
                        f"{existing_reason} (override: {manual_reason})"
                    )
                else:
                    decision[reason_key] = f"Manual override: {manual_reason}"

                overrides_info[coordinator_key] = {
                    "value": override_value,
                    "reason": manual_reason,
                    "set_at": (
                        override.get("set_at").isoformat()
                        if override.get("set_at")
                        else None
                    ),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }

            else:
                if not manual_reason or manual_reason == "Manual override":
                    manual_reason = (
                        "Manual override to charge"
                        if override_value
                        else "Manual override to wait"
                    )

                previous_value = decision.get(coordinator_key)
                decision[coordinator_key] = override_value
                if previous_value != override_value or coordinator_key in (
                    "battery_grid_charging",
                    "car_grid_charging",
                ):
                    changed_targets.add(coordinator_key)

                reason_key = f"{coordinator_key}_reason"
                existing_reason = decision.get(reason_key)
                if existing_reason:
                    decision[reason_key] = (
                        f"{existing_reason} (override: {manual_reason})"
                    )
                else:
                    decision[reason_key] = f"Manual override: {manual_reason}"

                overrides_info[coordinator_key] = {
                    "value": override_value,
                    "reason": manual_reason,
                    "set_at": (
                        override.get("set_at").isoformat()
                        if override.get("set_at")
                        else None
                    ),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }

                augmented_trace.append(
                    {
                        "strategy": "ManualOverride",
                        "priority": -1,
                        "should_charge": override_value,
                        "reason": manual_reason,
                        "target": coordinator_key,
                    }
                )

        for key in expired_keys:
            coordinator._manual_overrides[key] = None
            _LOGGER.debug("Removed expired manual override: %s", key)
        if expired_keys:
            self.schedule_persist()

        if overrides_info:
            decision["manual_overrides"] = overrides_info
            decision["strategy_trace"] = augmented_trace
        else:
            decision["manual_overrides"] = {}

        return decision, changed_targets
