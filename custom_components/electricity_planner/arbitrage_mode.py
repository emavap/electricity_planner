"""Arbitrage mode deadline computation and plan building.

Extracted from ``coordinator.py`` as a standalone collaborator. Responsible
for resolving the local deadline after which no more arbitrage export should
occur, and for constructing the arbitrage plan dict used by sensors and the
decision engine.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_GRID_POWER,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
)
from .helpers import coerce_integral_range, resolve_local_deadline

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class ArbitrageModePlanner:
    """Computes arbitrage deadlines and builds the current arbitrage plan."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    def deadline(self, now: datetime) -> datetime:
        """Return the export deadline: next occurrence of the configured local hour."""
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

    def _effective_reserve_soc(self, data: dict[str, Any]) -> tuple[float, str]:
        """Return the effective arbitrage reserve SOC and the reason.

        When tomorrow's solar forecast exceeds the sunny trigger threshold,
        use the sunny-day override (lower, to sell more battery capacity before
        a sunny day); otherwise use the base value.
        """
        config = self._coordinator.config

        base = float(
            config.get(
                CONF_ARBITRAGE_MODE_RESERVE_SOC, DEFAULT_ARBITRAGE_MODE_RESERVE_SOC
            )
        )
        sunny = float(
            config.get(
                CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
            )
        )
        sunny_threshold_kwh = float(
            config.get(
                CONF_SUNNY_FORECAST_THRESHOLD_KWH,
                DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
            )
        )

        solar_forecast = data.get("solar_forecast_production")
        try:
            solar_forecast_val = (
                float(solar_forecast) if solar_forecast is not None else None
            )
        except (TypeError, ValueError):
            solar_forecast_val = None

        if solar_forecast_val is not None and solar_forecast_val >= sunny_threshold_kwh:
            return min(100.0, max(0.0, sunny)), "sunny_day"

        return min(100.0, max(0.0, base)), "normal"

    def build_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build the current arbitrage plan and status."""
        coordinator = self._coordinator
        enabled = coordinator.is_arbitrage_mode_enabled()
        reserve_soc, reserve_soc_source = self._effective_reserve_soc(data)

        plan: dict[str, Any] = {
            "enabled": enabled,
            "active": False,
            "reason": "Arbitrage mode disabled",
            "reserve_soc": round(reserve_soc, 1),
            "reserve_soc_source": reserve_soc_source,
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
        if not enabled:
            return plan

        battery_details = data.get("battery_details") or []
        if not battery_details:
            plan["reason"] = "Arbitrage mode enabled but no battery data is available"
            return plan

        available_energy_kwh = 0.0
        for battery in battery_details:
            capacity = float(battery.get("capacity") or 0.0)
            soc = float(battery.get("soc") or 0.0)
            if capacity <= 0:
                continue
            # Treat the arbitrage reserve as a fleet-wide floor: batteries already
            # below the target must offset the excess of batteries above it.
            available_energy_kwh += capacity * (soc - reserve_soc) / 100.0

        available_energy_kwh = max(0.0, available_energy_kwh)
        plan["available_energy_kwh"] = round(available_energy_kwh, 3)
        if available_energy_kwh <= 0:
            plan["reason"] = (
                f"Arbitrage reserve target already reached (net energy above {reserve_soc:.0f}% is unavailable)"
            )
            return plan

        max_battery_power = int(
            coordinator.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
        )
        max_grid_power = int(
            coordinator.config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)
        )
        configured_export_cap = int(
            coordinator.config.get(
                CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
                DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER,
            )
        )
        automatic_export_cap = min(max_battery_power, max_grid_power)
        export_power_cap = (
            automatic_export_cap
            if configured_export_cap <= 0
            else min(configured_export_cap, automatic_export_cap)
        )
        plan["configured_export_cap_w"] = export_power_cap
        if export_power_cap <= 0:
            plan["reason"] = "Arbitrage mode enabled but no export power is available"
            return plan

        required_duration_hours = available_energy_kwh / (export_power_cap / 1000)
        required_duration = timedelta(hours=required_duration_hours)
        plan["required_duration_hours"] = round(required_duration_hours, 2)
        return self._finalize_plan(
            plan,
            data,
            required_duration,
            export_power_cap,
        )

    def _finalize_plan(
        self,
        plan: dict[str, Any],
        data: dict[str, Any],
        required_duration: timedelta,
        export_power_cap: int,
    ) -> dict[str, Any]:
        """Select eligible export slots and decorate the plan with status/reason."""
        coordinator = self._coordinator

        def _iso_local(dt_obj: datetime) -> str:
            return dt_util.as_local(dt_obj).isoformat()

        now = dt_util.utcnow()
        timeline = coordinator._build_feedin_price_timeline(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            now,
        )
        if not timeline:
            plan["reason"] = (
                "Arbitrage mode enabled but no feed-in price timeline is available"
            )
            return plan

        feedin_threshold = float(
            coordinator.config.get(
                CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
            )
        )
        deadline = self.deadline(now)
        plan["deadline"] = _iso_local(deadline)
        slot_selection = coordinator._select_export_slots(
            timeline,
            now,
            required_duration,
            feedin_threshold,
            latest_end=deadline,
        )
        if slot_selection is None:
            plan["reason"] = (
                f"No eligible feed-in slots are currently available above {feedin_threshold:.3f}€/kWh before {plan['deadline']}"
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
        plan["selected_slots_count"] = int(
            slot_selection.get("selected_slots_count", 0)
        )
        plan["slots_cover_full_arbitrage"] = bool(
            slot_selection.get("covers_full_export", False)
        )
        arbitrage_price_threshold = slot_selection.get("export_price_threshold")
        current_slot_price = slot_selection.get("current_slot_price")
        plan["arbitrage_price_threshold"] = (
            round(float(arbitrage_price_threshold), 4)
            if arbitrage_price_threshold is not None
            else None
        )
        plan["current_slot_price"] = (
            round(float(current_slot_price), 4)
            if current_slot_price is not None
            else None
        )

        if (
            current_slot_price is not None
            and arbitrage_price_threshold is not None
            and float(current_slot_price) >= float(arbitrage_price_threshold)
        ):
            plan["active"] = True
            plan["export_power"] = export_power_cap
            if plan["slots_cover_full_arbitrage"]:
                plan["reason"] = (
                    f"Arbitrage export active at {float(current_slot_price):.3f}€/kWh "
                    f"(arbitrage threshold {float(arbitrage_price_threshold):.3f}€/kWh)"
                )
            else:
                plan["reason"] = (
                    f"Arbitrage export active at {float(current_slot_price):.3f}€/kWh using the best available slots "
                    f"(arbitrage threshold {float(arbitrage_price_threshold):.3f}€/kWh)"
                )
        else:
            if plan["slots_cover_full_arbitrage"]:
                plan["reason"] = (
                    f"Arbitrage mode armed for the top {plan['selected_slots_count']} eligible slots "
                    f"with arbitrage threshold {float(arbitrage_price_threshold):.3f}€/kWh"
                )
            else:
                plan["reason"] = (
                    f"Arbitrage mode armed for the best available {plan['selected_slots_count']} eligible slots "
                    f"with arbitrage threshold {float(arbitrage_price_threshold):.3f}€/kWh"
                )

        return plan
