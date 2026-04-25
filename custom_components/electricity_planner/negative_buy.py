"""Negative Arbitrage Buy mode planner.

Symmetric counterpart to :mod:`arbitrage_mode`. When a user enables the
Negative Arbitrage Buy switch and the net buy price drops below the
configured threshold (default ``-0.05€/kWh``), this planner schedules
forced grid-charging during the cheapest upcoming slots so the battery
is filled while the grid is paying us to consume.

The plan is consumed by the decision engine, which forces battery grid
charging while ``active`` is True and curtails solar production once
``max_soc_threshold`` is reached.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_NEGATIVE_BUY_THRESHOLD,
)
from .helpers import coerce_integral_range, resolve_local_deadline

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class NegativeBuyPlanner:
    """Compute the Negative Arbitrage Buy plan and deadline."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    def deadline(self, now: datetime) -> datetime:
        """Return the buy-window deadline: next occurrence of the shared arbitrage hour.

        Negative Arbitrage Buy and arbitrage selling share the same configured
        deadline (``CONF_ARBITRAGE_MODE_DEADLINE_HOUR``) so users only manage one
        cutoff for both directions of arbitrage planning.
        """
        coordinator = self._coordinator
        now_local = dt_util.as_local(now)
        configured_deadline_hour = coordinator.config.get(
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
            DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
        )
        deadline_hour = coerce_integral_range(
            configured_deadline_hour,
            min_value=0,
            max_value=23,
        )
        if deadline_hour is None:
            deadline_hour = DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR

        deadline_local = resolve_local_deadline(now_local.date(), deadline_hour)
        if now_local >= deadline_local:
            deadline_local = resolve_local_deadline(
                now_local.date() + timedelta(days=1),
                deadline_hour,
            )
        return dt_util.as_utc(deadline_local)

    def build_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build the current Negative Arbitrage Buy plan and status."""
        coordinator = self._coordinator
        enabled = coordinator.is_negative_buy_mode_enabled()
        threshold = float(
            coordinator.config.get(
                CONF_NEGATIVE_BUY_THRESHOLD,
                DEFAULT_NEGATIVE_BUY_THRESHOLD,
            )
        )
        max_soc_threshold = float(
            coordinator.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)
        )

        plan: dict[str, Any] = {
            "enabled": enabled,
            "active": False,
            "solar_curtail_active": False,
            "reason": "Negative Arbitrage Buy mode disabled",
            "threshold": round(threshold, 4),
            "max_soc_threshold": round(max_soc_threshold, 1),
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
        if not enabled:
            return plan

        battery_details = data.get("battery_details") or []
        if not battery_details:
            plan["reason"] = "Negative Arbitrage Buy mode enabled but no battery data is available"
            return plan

        required_energy_kwh = 0.0
        for battery in battery_details:
            capacity = float(battery.get("capacity") or 0.0)
            soc = float(battery.get("soc") or 0.0)
            if capacity <= 0:
                continue
            # Treat the SOC ceiling as a fleet-wide cap mirroring arbitrage_mode:
            # batteries already above the cap offset deficits below it.
            required_energy_kwh += capacity * (max_soc_threshold - soc) / 100.0

        required_energy_kwh = max(0.0, required_energy_kwh)
        plan["required_energy_kwh"] = round(required_energy_kwh, 3)
        if required_energy_kwh <= 0:
            # Batteries already topped up: keep solar curtailment armed so we
            # still maximize paid-to-consume kWh while the slot is negative.
            current_price = self._resolve_current_price(data)
            plan["current_slot_price"] = (
                round(float(current_price), 4) if current_price is not None else None
            )
            if current_price is not None and float(current_price) <= threshold:
                plan["solar_curtail_active"] = True
                plan["reason"] = (
                    f"Battery at SOC ceiling {max_soc_threshold:.0f}% - solar curtailed at "
                    f"{float(current_price):.3f}€/kWh to maximize paid-to-consume import"
                )
            else:
                plan["reason"] = (
                    f"Battery already at or above SOC ceiling {max_soc_threshold:.0f}% - "
                    "nothing to buy"
                )
            return plan

        max_battery_power = int(
            coordinator.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
        )
        max_grid_power = int(
            coordinator.config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)
        )
        import_power_cap = min(max_battery_power, max_grid_power)
        plan["configured_import_cap_w"] = import_power_cap
        if import_power_cap <= 0:
            plan["reason"] = "Negative Arbitrage Buy mode enabled but no import power is available"
            return plan

        required_duration_hours = required_energy_kwh / (import_power_cap / 1000)
        required_duration = timedelta(hours=required_duration_hours)
        plan["required_duration_hours"] = round(required_duration_hours, 2)
        return self._finalize_plan(
            plan, data, required_duration, import_power_cap, threshold,
        )

    def _finalize_plan(
        self,
        plan: dict[str, Any],
        data: dict[str, Any],
        required_duration: timedelta,
        import_power_cap: int,
        threshold: float,
    ) -> dict[str, Any]:
        """Select cheapest buy slots and decorate the plan with status/reason."""
        coordinator = self._coordinator

        def _iso_local(dt_obj: datetime) -> str:
            return dt_util.as_local(dt_obj).isoformat()

        now = dt_util.utcnow()
        timeline = coordinator._build_price_timeline(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            data.get("transport_cost_lookup"),
            data.get("transport_cost"),
            now,
        )
        if not timeline:
            plan["reason"] = (
                "Negative Arbitrage Buy mode enabled but no price timeline is available"
            )
            return plan

        deadline = self.deadline(now)
        plan["deadline"] = _iso_local(deadline)
        slot_selection = coordinator._select_buy_slots(
            timeline,
            now,
            required_duration,
            threshold,
            latest_end=deadline,
        )
        if slot_selection is None:
            plan["reason"] = (
                f"No eligible buy slots are currently available below {threshold:.3f}€/kWh "
                f"before {plan['deadline']}"
            )
            return plan

        selected_slots = slot_selection.get("selected_slots", [])
        plan["selected_slots"] = [
            {
                "start": _iso_local(slot["start"]),
                "end": _iso_local(slot["end"]),
                "price": round(slot["price"], 4),
            }
            for slot in selected_slots
        ]
        plan["selected_slots_count"] = int(slot_selection.get("selected_slots_count", 0))
        plan["slots_cover_full_charge"] = bool(slot_selection.get("covers_full_charge", False))
        buy_price_threshold = slot_selection.get("buy_price_threshold")
        current_slot_price = slot_selection.get("current_slot_price")
        plan["buy_price_threshold"] = (
            round(float(buy_price_threshold), 4)
            if buy_price_threshold is not None
            else None
        )
        plan["current_slot_price"] = (
            round(float(current_slot_price), 4)
            if current_slot_price is not None
            else None
        )

        if (
            current_slot_price is not None
            and buy_price_threshold is not None
            and float(current_slot_price) <= float(buy_price_threshold)
        ):
            plan["active"] = True
            plan["solar_curtail_active"] = True
            plan["import_power"] = import_power_cap
            if plan["slots_cover_full_charge"]:
                plan["reason"] = (
                    f"Negative Arbitrage Buy active at {float(current_slot_price):.3f}€/kWh "
                    f"(buy threshold {float(buy_price_threshold):.3f}€/kWh)"
                )
            else:
                plan["reason"] = (
                    f"Negative Arbitrage Buy active at {float(current_slot_price):.3f}€/kWh "
                    f"using the cheapest available slots "
                    f"(buy threshold {float(buy_price_threshold):.3f}€/kWh)"
                )
        else:
            if plan["slots_cover_full_charge"]:
                plan["reason"] = (
                    f"Negative Arbitrage Buy armed for the cheapest {plan['selected_slots_count']} "
                    f"eligible slots with buy threshold {float(buy_price_threshold):.3f}€/kWh"
                )
            else:
                plan["reason"] = (
                    f"Negative Arbitrage Buy armed for the cheapest available "
                    f"{plan['selected_slots_count']} eligible slots with buy threshold "
                    f"{float(buy_price_threshold):.3f}€/kWh"
                )

        return plan

    def _resolve_current_price(self, data: dict[str, Any]) -> float | None:
        """Return the net buy price of the slot covering ``now`` (or None)."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        timeline = coordinator._build_price_timeline(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            data.get("transport_cost_lookup"),
            data.get("transport_cost"),
            now,
        )
        if not timeline:
            return None
        for start, end, price in timeline:
            if start <= now < end:
                return float(price)
        return None
