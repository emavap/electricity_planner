"""Negative Arbitrage Buy mode planner.

Symmetric counterpart to :mod:`arbitrage_mode`. When a user enables the
Negative Arbitrage Buy switch and the net buy price is at or below the
configured threshold (default ``-0.05€/kWh``), this planner schedules
forced grid import for eligible slots while the grid is paying us to consume.

The plan is consumed by the decision engine, which forces battery grid
charging while ``active`` is True and curtails solar production during
paid-to-consume slots.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_MAX_GRID_POWER,
    CONF_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_MAX_GRID_POWER,
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

        plan: dict[str, Any] = {
            "enabled": enabled,
            "active": False,
            "solar_curtail_active": False,
            "reason": "Negative Arbitrage Buy mode disabled",
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
        if not enabled:
            return plan

        battery_details = data.get("battery_details") or []
        battery_headroom_kwh = 0.0
        for battery in battery_details:
            capacity = float(battery.get("capacity") or 0.0)
            soc = float(battery.get("soc") or 0.0)
            if capacity <= 0:
                continue
            battery_headroom_kwh += capacity * max(0.0, 100.0 - soc) / 100.0

        plan["required_energy_kwh"] = round(battery_headroom_kwh, 3)
        max_grid_power = int(
            coordinator.config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)
        )
        import_power_cap = max_grid_power
        plan["configured_import_cap_w"] = import_power_cap
        if import_power_cap <= 0:
            plan["reason"] = (
                "Negative Arbitrage Buy mode enabled but no import power is available"
            )
            return plan

        if battery_headroom_kwh > 0:
            required_duration_hours = battery_headroom_kwh / (import_power_cap / 1000)
            plan["required_duration_hours"] = round(required_duration_hours, 2)

        return self._finalize_plan(plan, data, import_power_cap, threshold)

    def _finalize_plan(
        self,
        plan: dict[str, Any],
        data: dict[str, Any],
        import_power_cap: int,
        threshold: float,
    ) -> dict[str, Any]:
        """Select eligible buy slots and decorate the plan with status/reason."""
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
        eligible_slots: list[dict[str, Any]] = []
        selected_duration = timedelta(0)
        current_slot_price: float | None = None
        for start, end, price in timeline:
            if start >= deadline:
                continue
            if end > deadline:
                end = deadline
            if end <= now or float(price) > threshold:
                continue

            segment_start = max(start, now)
            if end <= segment_start:
                continue
            if start <= now < end:
                current_slot_price = float(price)
            duration = end - segment_start
            selected_duration += duration
            eligible_slots.append(
                {
                    "start": segment_start,
                    "end": end,
                    "price": float(price),
                    "duration": duration,
                }
            )

        if not eligible_slots:
            plan["reason"] = (
                f"No eligible buy slots are currently available at or below {threshold:.3f}€/kWh "
                f"before {plan['deadline']}"
            )
            return plan

        plan["selected_slots"] = [
            {
                "start": _iso_local(slot["start"]),
                "end": _iso_local(slot["end"]),
                "price": round(slot["price"], 4),
            }
            for slot in eligible_slots
        ]
        plan["selected_slots_count"] = len(eligible_slots)
        required_duration = timedelta(hours=float(plan["required_duration_hours"] or 0))
        plan["slots_cover_full_charge"] = (
            required_duration <= timedelta(0) or selected_duration >= required_duration
        )
        buy_price_threshold = max(float(slot["price"]) for slot in eligible_slots)
        plan["buy_price_threshold"] = round(buy_price_threshold, 4)
        plan["current_slot_price"] = (
            round(float(current_slot_price), 4)
            if current_slot_price is not None
            else None
        )

        if current_slot_price is not None and float(current_slot_price) <= threshold:
            plan["active"] = True
            plan["solar_curtail_active"] = True
            plan["import_power"] = import_power_cap
            plan["reason"] = (
                f"Negative Arbitrage Buy active at {float(current_slot_price):.3f}€/kWh "
                f"(configured threshold {threshold:.3f}€/kWh)"
            )
        else:
            plan["reason"] = (
                f"Negative Arbitrage Buy armed for {plan['selected_slots_count']} "
                f"eligible slots at or below {threshold:.3f}€/kWh"
            )

        return plan
