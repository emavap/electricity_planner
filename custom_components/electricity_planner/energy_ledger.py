"""Marginal (LIFO) virtual battery-energy ledger for profitable arbitrage export.

The ledger is an accounting model, not a battery telemetry replacement.  It
tracks *stored* kWh (SOC x capacity) in lots ordered oldest-first, so index 0 is
the energy physically at the bottom of the battery and the last lot is the most
recently charged energy.

Two consequences drive the whole policy:

* the arbitrage reserve floor is never discharged, so it is netted off the
  *oldest* lots -- that is exactly the energy that stays behind;
* a discharge can only reach a deeper lot after moving everything stacked on
  top of it, so eligibility is evaluated from the newest lot downwards and the
  first ineligible lot stops the walk.

Together they let energy charged above the reserve be exported while unknown
energy sitting in (or under) the reserve can never leak out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any

# Lot energies are persisted rounded to 6 decimals; comparisons use a matching
# tolerance so serialisation round-trips never resurrect zero-sized lots.
ENERGY_EPSILON_KWH = 1e-6
ENERGY_STORAGE_DECIMALS = 6
# Worst-case error a single lot picks up from being rounded for storage.
ENERGY_STORAGE_ROUNDING_KWH = 0.5 * 10**-ENERGY_STORAGE_DECIMALS
# Hard ceiling on the number of lots.  Charging alternately from an attributable
# and an unattributable source appends lots that never coalesce, so a
# long-running ledger needs a bound that does not depend on the charge pattern.
# The oldest lots are folded into one unknown lot when the ceiling is reached:
# that energy sits at the bottom of the battery (inside the never-discharged
# reserve) and turning it unknown can only ever block an export, never allow one.
MAX_LOTS = 256
LEDGER_STORAGE_VERSION = 1


class EnergyOrigin(StrEnum):
    """Auditable origin assigned to virtual battery energy."""

    GRID = "grid"
    SOLAR = "solar"
    UNKNOWN = "unknown"


def _is_number(value: Any) -> bool:
    """Return True for a real, finite float/int (``bool`` is not a number here)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
    )


@dataclass
class EnergyLot:
    """One lot of virtual battery energy measured in stored kWh."""

    origin: EnergyOrigin
    energy_kwh: float
    buy_cost_eur_per_kwh: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize a validated lot for Home Assistant Store."""
        return {
            "origin": self.origin.value,
            "energy_kwh": round(self.energy_kwh, ENERGY_STORAGE_DECIMALS),
            "buy_cost_eur_per_kwh": (
                None
                if self.buy_cost_eur_per_kwh is None
                else round(self.buy_cost_eur_per_kwh, ENERGY_STORAGE_DECIMALS)
            ),
        }

    def can_absorb(self, origin: EnergyOrigin, buy_cost: float | None) -> bool:
        """Return True when energy of this origin/price is indistinguishable.

        Two lots may only merge when nothing about the export decision could
        tell them apart: same origin, and for grid energy the same buy price at
        the precision the ledger is stored with.
        """
        if origin is not self.origin:
            return False
        if origin is not EnergyOrigin.GRID:
            return True
        if self.buy_cost_eur_per_kwh is None or buy_cost is None:
            return False
        return round(self.buy_cost_eur_per_kwh, ENERGY_STORAGE_DECIMALS) == round(
            buy_cost, ENERGY_STORAGE_DECIMALS
        )


class BatteryEnergyLedger:
    """Reserve-netted, marginal-energy lot accounting with export eligibility."""

    def __init__(self, lots: list[EnergyLot] | None = None) -> None:
        self._lots = [
            lot for lot in (lots or []) if lot.energy_kwh > ENERGY_EPSILON_KWH
        ]
        self._enforce_lot_limit()

    @property
    def lots(self) -> tuple[EnergyLot, ...]:
        """Return lot snapshots, oldest first, for diagnostics and tests."""
        return tuple(self._lots)

    @property
    def total_kwh(self) -> float:
        """Return the total accounted stored energy."""
        return sum(lot.energy_kwh for lot in self._lots)

    @property
    def storage_rounding_tolerance_kwh(self) -> float:
        """Return how far a stored copy of this ledger may sum away from it.

        Every lot is rounded on the way to disk, so a consistency check against
        the recorded observation has to allow the accumulated rounding error of
        all lots or it would reject a perfectly valid long-running ledger.
        """
        return len(self._lots) * ENERGY_STORAGE_ROUNDING_KWH

    def reset_to_unknown(self, total_kwh: float) -> None:
        """Drop all attribution and hold ``total_kwh`` as unprovable inventory."""
        self._lots = []
        if _is_number(total_kwh) and total_kwh > ENERGY_EPSILON_KWH:
            self._lots.append(EnergyLot(EnergyOrigin.UNKNOWN, float(total_kwh)))

    def add(
        self,
        origin: EnergyOrigin,
        energy_kwh: float,
        buy_cost: float | None = None,
    ) -> None:
        """Push a validated positive lot on top; invalid input is ignored."""
        if not _is_number(energy_kwh) or energy_kwh <= ENERGY_EPSILON_KWH:
            return
        if origin is EnergyOrigin.GRID:
            # A negative all-in purchase price is real (paid to consume) and
            # must be preserved with its sign: it lowers the price at which the
            # lot becomes profitable instead of making the lot unattributable.
            if not _is_number(buy_cost):
                origin = EnergyOrigin.UNKNOWN
                buy_cost = None
            else:
                buy_cost = float(buy_cost)
        else:
            buy_cost = None
        # Repeated refreshes charging from the same source at the same price
        # produce lots that are indistinguishable for every decision this class
        # makes.  Merging them into the newest lot keeps the ledger small
        # without changing any answer it gives.
        if self._lots and self._lots[-1].can_absorb(origin, buy_cost):
            self._lots[-1].energy_kwh += float(energy_kwh)
            return
        self._lots.append(EnergyLot(origin, float(energy_kwh), buy_cost))
        self._enforce_lot_limit()

    def _enforce_lot_limit(self) -> None:
        """Fold the oldest lots into one unknown lot once ``MAX_LOTS`` is hit."""
        excess = len(self._lots) - MAX_LOTS
        if excess <= 0:
            return
        folded = sum(lot.energy_kwh for lot in self._lots[: excess + 1])
        self._lots[: excess + 1] = [EnergyLot(EnergyOrigin.UNKNOWN, folded)]

    def discharge(self, energy_kwh: float) -> float:
        """Consume the newest energy first and return the accounted kWh."""
        if not _is_number(energy_kwh):
            return 0.0
        remaining = max(0.0, float(energy_kwh))
        consumed = 0.0
        while remaining > ENERGY_EPSILON_KWH and self._lots:
            lot = self._lots[-1]
            used = min(lot.energy_kwh, remaining)
            lot.energy_kwh -= used
            remaining -= used
            consumed += used
            if lot.energy_kwh <= ENERGY_EPSILON_KWH:
                self._lots.pop()
        return consumed

    @staticmethod
    def required_export_price(
        buy_cost: float,
        round_trip_efficiency: float,
        degradation_cost: float,
        minimum_profit: float,
    ) -> float | None:
        """Return the minimum feed-in price per *exported* kWh for one lot.

        ``buy_cost`` is the all-in import price per grid kWh and may be
        negative.  Delivering one exported kWh consumed ``1 /
        round_trip_efficiency`` imported kWh, so the purchase term scales with
        the round-trip loss for both signs: a negative price earns a larger
        credit exactly as a positive price costs more.  ``degradation_cost``
        and ``minimum_profit`` are expressed per exported kWh.
        """
        if not _is_number(round_trip_efficiency) or not 0 < round_trip_efficiency <= 1:
            return None
        if not _is_number(buy_cost):
            return None
        if not _is_number(degradation_cost) or degradation_cost < 0:
            return None
        if not _is_number(minimum_profit) or minimum_profit < 0:
            return None
        return buy_cost / round_trip_efficiency + degradation_cost + minimum_profit

    def exportable_stored_kwh(
        self,
        requested_kwh: float,
        reserve_kwh: float,
        sell_price: float | None,
        round_trip_efficiency: float,
        degradation_cost: float,
        minimum_profit: float,
    ) -> float:
        """Return stored kWh above the reserve that may be exported, unconsumed.

        The reserve floor is netted off the oldest lots because that energy is
        never discharged.  Eligibility is then evaluated from the newest lot
        downwards and stops at the first lot that may not be sold, because a
        discharge cannot reach past it.
        """
        if not _is_number(requested_kwh) or requested_kwh <= 0:
            return 0.0
        if not _is_number(sell_price):
            return 0.0
        if not _is_number(reserve_kwh) or reserve_kwh < 0:
            return 0.0
        # Self-produced energy has no purchase cost, but still has to cover
        # wear and the required margin before it is worth exporting.
        solar_required = self.required_export_price(
            0.0, round_trip_efficiency, degradation_cost, minimum_profit
        )
        if solar_required is None:
            return 0.0

        remaining_reserve = float(reserve_kwh)
        above_reserve: list[float] = []
        for lot in self._lots:
            held = min(lot.energy_kwh, remaining_reserve)
            remaining_reserve -= held
            above_reserve.append(lot.energy_kwh - held)
        if remaining_reserve > ENERGY_EPSILON_KWH:
            # The ledger cannot even account for the protected reserve, so no
            # accounted energy is above the floor.
            return 0.0

        allowed = 0.0
        for lot, free_kwh in zip(reversed(self._lots), reversed(above_reserve)):
            if allowed >= requested_kwh - ENERGY_EPSILON_KWH:
                break
            if free_kwh <= ENERGY_EPSILON_KWH:
                # This lot is entirely inside the reserve, and so is every
                # older lot beneath it.
                break
            if lot.origin is EnergyOrigin.UNKNOWN:
                break
            if lot.origin is EnergyOrigin.GRID:
                required = (
                    None
                    if lot.buy_cost_eur_per_kwh is None
                    else self.required_export_price(
                        lot.buy_cost_eur_per_kwh,
                        round_trip_efficiency,
                        degradation_cost,
                        minimum_profit,
                    )
                )
                if required is None or sell_price < required:
                    break
            elif sell_price < solar_required:
                break
            allowed += min(free_kwh, requested_kwh - allowed)
        return max(0.0, round(allowed, 6))

    def summary(self) -> dict[str, Any]:
        """Return a compact, non-sensitive snapshot for diagnostics."""
        energy_by_origin: dict[str, float] = {}
        for lot in self._lots:
            energy_by_origin[lot.origin.value] = round(
                energy_by_origin.get(lot.origin.value, 0.0) + lot.energy_kwh, 6
            )
        return {
            "lot_count": len(self._lots),
            "total_kwh": round(self.total_kwh, 6),
            "energy_by_origin": energy_by_origin,
            "newest_lot_origin": self._lots[-1].origin.value if self._lots else None,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return the durable storage payload."""
        return {
            "version": LEDGER_STORAGE_VERSION,
            "lots": [lot.as_dict() for lot in self._lots],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "BatteryEnergyLedger | None":
        """Return the stored ledger, or ``None`` when it cannot be trusted.

        A single malformed lot invalidates the whole payload.  Dropping it
        instead would leave the surviving lots claiming energy the battery no
        longer holds, which is precisely the state that must never be sold.
        """
        if (
            not isinstance(payload, dict)
            or payload.get("version") != LEDGER_STORAGE_VERSION
        ):
            return None
        raw_lots = payload.get("lots")
        if not isinstance(raw_lots, list):
            return None
        lots: list[EnergyLot] = []
        for raw in raw_lots:
            if not isinstance(raw, dict):
                return None
            try:
                origin = EnergyOrigin(raw["origin"])
            except (KeyError, TypeError, ValueError):
                return None
            energy = raw.get("energy_kwh")
            if not _is_number(energy) or energy <= 0:
                return None
            cost = raw.get("buy_cost_eur_per_kwh")
            if origin is EnergyOrigin.GRID:
                if not _is_number(cost):
                    return None
            elif cost is not None:
                return None
            lots.append(
                EnergyLot(
                    origin,
                    float(energy),
                    float(cost) if origin is EnergyOrigin.GRID else None,
                )
            )
        return cls(lots)
