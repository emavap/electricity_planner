"""Transport cost resolution and history lookup.

Extracted from ``coordinator.py`` as a standalone collaborator. Responsible
for resolving per-interval transport costs (either from built-in day/night
components or from a legacy external entity with recorder history) and
maintaining the cached lookup used by the price timeline builder.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ENERGY_COST_GSC,
    CONF_ENERGY_COST_WKK,
    CONF_ENERGY_TAX_ACCIJNS,
    CONF_ENERGY_TAX_BIJDRAGE,
    CONF_P1_TARIFF_ENTITY,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
)
from .helpers import (
    calculate_transport_cost_from_components,
    is_day_tariff,
    resolve_transport_cost_from_lookup,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class TransportCostResolver:
    """Resolves transport costs and maintains recorder-history lookup cache."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    def has_builtin(self) -> bool:
        """Check if built-in transport cost components are configured."""
        return self._coordinator.config.get(CONF_TRANSPORT_COST_DAY) is not None

    def resolve(
        self,
        transport_lookup: list[dict[str, Any]] | None,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float | None:
        """Resolve transport cost for a specific timestamp."""
        if self.has_builtin():
            return self.resolve_builtin(start_time_utc, reference_now)

        return resolve_transport_cost_from_lookup(
            transport_lookup,
            start_time_utc,
            reference_now=reference_now,
        )

    def resolve_builtin(
        self,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float:
        """Calculate transport cost from built-in components."""
        coordinator = self._coordinator
        if reference_now is None:
            reference_now = dt_util.utcnow()

        p1_tariff_code = None
        current_interval_age = (reference_now - start_time_utc).total_seconds()
        if 0 <= current_interval_age < 900:  # Current 15-minute slot only
            p1_entity = coordinator.config.get(CONF_P1_TARIFF_ENTITY)
            if p1_entity:
                state = coordinator.hass.states.get(p1_entity)
                if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    p1_tariff_code = state.state

        day = is_day_tariff(start_time_utc, p1_tariff_code)

        return calculate_transport_cost_from_components(
            is_day=day,
            transport_day=coordinator.config.get(
                CONF_TRANSPORT_COST_DAY, DEFAULT_TRANSPORT_COST_DAY
            ),
            transport_night=coordinator.config.get(
                CONF_TRANSPORT_COST_NIGHT, DEFAULT_TRANSPORT_COST_NIGHT
            ),
            accijns=coordinator.config.get(
                CONF_ENERGY_TAX_ACCIJNS, DEFAULT_ENERGY_TAX_ACCIJNS
            ),
            bijdrage=coordinator.config.get(
                CONF_ENERGY_TAX_BIJDRAGE, DEFAULT_ENERGY_TAX_BIJDRAGE
            ),
            gsc=coordinator.config.get(CONF_ENERGY_COST_GSC, DEFAULT_ENERGY_COST_GSC),
            wkk=coordinator.config.get(CONF_ENERGY_COST_WKK, DEFAULT_ENERGY_COST_WKK),
        )

    def maybe_log_status(
        self, status: str, message: str | None, *args: Any
    ) -> None:
        """Log transport cost status changes without spamming."""
        coordinator = self._coordinator
        if status == coordinator._transport_cost_last_log or message is None:
            coordinator._transport_cost_last_log = status
            return

        _LOGGER.warning(message, *args)
        coordinator._transport_cost_last_log = status

    def build_fallback_lookup(
        self, current_transport_cost: float | None
    ) -> list[dict[str, Any]]:
        """Build a lookup that uses the current transport cost for all hours."""
        if current_transport_cost is None:
            return []
        return [
            {
                "start": None,
                "cost": current_transport_cost,
            }
        ]

    async def get_lookup(
        self, current_transport_cost: float | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        """Return cached transport cost lookup built from recorder history."""
        coordinator = self._coordinator
        transport_entity = coordinator.config.get(CONF_TRANSPORT_COST_ENTITY)
        if not transport_entity:
            coordinator._transport_cost_lookup = []
            coordinator._transport_cost_status = "not_configured"
            return [], "not_configured"

        now = dt_util.utcnow()
        # Refresh every 30 minutes at most
        if (
            coordinator._transport_cost_lookup_time
            and now - coordinator._transport_cost_lookup_time < timedelta(minutes=30)
        ):
            cached_cost = (
                coordinator._transport_cost_lookup[0].get("cost")
                if coordinator._transport_cost_lookup
                else None
            )
            if not (
                coordinator._transport_cost_status in {"fallback_current", "pending_history"}
                and cached_cost != current_transport_cost
            ):
                return coordinator._transport_cost_lookup, coordinator._transport_cost_status

        try:
            try:
                from homeassistant.components.recorder import get_instance as get_recorder_instance
            except ImportError:
                get_recorder_instance = None  # Older HA or test shims may not expose get_instance

            from homeassistant.components.recorder.history import get_significant_states

            end_time = dt_util.now()
            start_time = end_time - timedelta(days=7)

            if get_recorder_instance is not None:
                recorder = get_recorder_instance(coordinator.hass)
                states = await recorder.async_add_executor_job(
                    get_significant_states,
                    coordinator.hass,
                    start_time,
                    end_time,
                    [transport_entity],
                )
            else:
                # Fallback for environments without recorder.get_instance (tests/shims)
                states = await coordinator.hass.async_add_executor_job(
                    get_significant_states,
                    coordinator.hass,
                    start_time,
                    end_time,
                    [transport_entity],
                )

            if not states or transport_entity not in states:
                fallback_lookup = self.build_fallback_lookup(current_transport_cost)
                coordinator._transport_cost_lookup = fallback_lookup
                if fallback_lookup:
                    coordinator._transport_cost_status = "fallback_current"
                    self.maybe_log_status(
                        "fallback_current",
                        "Using current transport cost value for all hours due to missing history on %s.",
                        transport_entity,
                    )
                else:
                    coordinator._transport_cost_status = "pending_history"
                    self.maybe_log_status(
                        "pending_history",
                        "No transport cost history available for %s. "
                        "Nord Pool prices will exclude transport cost until 7 days of history accumulate.",
                        transport_entity,
                    )
                coordinator._transport_cost_lookup_time = now
                return coordinator._transport_cost_lookup, coordinator._transport_cost_status

            # First collect all valid cost changes with timestamps. Pre-parse
            # the local-time representation here so resolve() doesn't have to
            # call dt_util.parse_datetime() once per entry per interval (the
            # 192-interval timeline rebuild would otherwise spend ~134 k
            # ISO-8601 parses per cycle on a full 7-day history lookup).
            raw_changes: list[dict[str, Any]] = []
            for state in states[transport_entity]:
                value = state.state
                if value in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    continue
                try:
                    cost = float(value)
                    timestamp = dt_util.as_utc(state.last_changed)
                    raw_changes.append(
                        {
                            "start": timestamp.isoformat(),
                            "cost": cost,
                            "_local": dt_util.as_local(timestamp),
                        }
                    )
                except (ValueError, TypeError, AttributeError):
                    continue

            # Sort by timestamp first
            raw_changes.sort(key=lambda entry: entry["start"])

            # Then remove duplicate consecutive values
            changes: list[dict[str, Any]] = []
            last_cost: float | None = None
            for change in raw_changes:
                cost = change["cost"]
                if last_cost is None or abs(cost - last_cost) > 1e-9:
                    changes.append(change)
                    last_cost = cost

            coordinator._transport_cost_lookup = changes
            coordinator._transport_cost_status = "applied" if changes else "pending_history"
            coordinator._transport_cost_lookup_time = now

            if coordinator._transport_cost_status == "pending_history":
                self.maybe_log_status(
                    "pending_history",
                    "Transport cost history for %s is incomplete. "
                    "Nord Pool prices will exclude transport cost until 7 days of history accumulate.",
                    transport_entity,
                )
            else:
                self.maybe_log_status("applied", None)

            return coordinator._transport_cost_lookup, coordinator._transport_cost_status

        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            fallback_lookup = self.build_fallback_lookup(current_transport_cost)
            coordinator._transport_cost_lookup = fallback_lookup
            if fallback_lookup:
                coordinator._transport_cost_status = "fallback_current"
                self.maybe_log_status(
                    "fallback_current",
                    "Using current transport cost value for all hours after history lookup failure on %s.",
                    transport_entity,
                )
            else:
                coordinator._transport_cost_status = "error"
                self.maybe_log_status(
                    "error",
                    "Failed to build transport cost lookup from history for %s: %s. "
                    "Nord Pool prices will exclude transport cost.",
                    transport_entity,
                    err,
                )
            coordinator._transport_cost_lookup_time = now
            return coordinator._transport_cost_lookup, coordinator._transport_cost_status
