"""Safety tests for the virtual battery-energy ledger used by arbitrage export."""

from __future__ import annotations

import math

import pytest

from custom_components.electricity_planner.energy_ledger import (
    ENERGY_STORAGE_ROUNDING_KWH,
    MAX_LOTS,
    BatteryEnergyLedger,
    EnergyLot,
    EnergyOrigin,
)

# Round-trip efficiency / degradation / minimum profit used across the tests.
RTE = 0.9
DEGRADATION = 0.0
PROFIT = 0.02


def _exportable(ledger, requested, reserve, sell, **overrides):
    return ledger.exportable_stored_kwh(
        requested,
        reserve,
        sell,
        overrides.get("rte", RTE),
        overrides.get("degradation", DEGRADATION),
        overrides.get("profit", PROFIT),
    )


# --- lot bookkeeping -------------------------------------------------------


def test_discharge_consumes_the_newest_energy_first() -> None:
    """Discharge is marginal: it touches the most recently stored energy."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 4)
    ledger.add(EnergyOrigin.SOLAR, 3)

    assert ledger.discharge(2) == pytest.approx(2)

    assert [(lot.origin, lot.energy_kwh) for lot in ledger.lots] == [
        (EnergyOrigin.UNKNOWN, pytest.approx(4)),
        (EnergyOrigin.SOLAR, pytest.approx(1)),
    ]


def test_discharge_spanning_lots_pops_them_in_reverse_order() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 4)
    ledger.add(EnergyOrigin.SOLAR, 3)
    ledger.add(EnergyOrigin.GRID, 1, 0.1)

    assert ledger.discharge(5) == pytest.approx(5)

    assert [lot.origin for lot in ledger.lots] == [EnergyOrigin.UNKNOWN]
    assert ledger.total_kwh == pytest.approx(3)


@pytest.mark.parametrize("energy", [None, float("nan"), "2", True])
def test_discharge_ignores_unusable_input(energy) -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 2)

    assert ledger.discharge(energy) == 0
    assert ledger.total_kwh == pytest.approx(2)


def test_export_stops_at_a_lot_fully_inside_the_reserve() -> None:
    """The walk stops at the reserve boundary, not only at an unknown lot."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 4)
    ledger.add(EnergyOrigin.SOLAR, 3)

    # The reserve covers the whole older lot, so only the newer one is offered.
    assert _exportable(ledger, 7, 4, 0.20) == pytest.approx(3)


def test_discharge_beyond_the_ledger_truncates_without_going_negative() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 2)

    assert ledger.discharge(5) == pytest.approx(2)
    assert ledger.lots == ()
    assert ledger.total_kwh == 0


@pytest.mark.parametrize("energy", [0, -1, float("nan"), float("inf"), None, True, "3"])
def test_add_ignores_unusable_energy(energy) -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, energy)
    assert ledger.lots == ()


@pytest.mark.parametrize("cost", [None, float("nan"), "0.1", True])
def test_grid_lot_without_a_usable_price_becomes_unknown(cost) -> None:
    """An unpriced grid lot cannot be proven profitable, so it is not grid."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 2, cost)

    assert [lot.origin for lot in ledger.lots] == [EnergyOrigin.UNKNOWN]
    assert ledger.lots[0].buy_cost_eur_per_kwh is None
    assert _exportable(ledger, 2, 0, 5.0) == 0


def test_reset_to_unknown_replaces_all_attribution() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 2, 0.1)
    ledger.add(EnergyOrigin.SOLAR, 1)

    ledger.reset_to_unknown(7.5)

    assert [(lot.origin, lot.energy_kwh) for lot in ledger.lots] == [
        (EnergyOrigin.UNKNOWN, pytest.approx(7.5))
    ]


def test_reset_to_unknown_with_empty_battery_clears_the_ledger() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 1)

    ledger.reset_to_unknown(0.0)

    assert ledger.lots == ()


# --- required export price -------------------------------------------------


def test_required_export_price_scales_the_purchase_by_the_round_trip_loss() -> None:
    assert BatteryEnergyLedger.required_export_price(
        0.10, 0.9, 0.01, 0.02
    ) == pytest.approx(0.10 / 0.9 + 0.03)


def test_required_export_price_credits_a_negative_purchase_price() -> None:
    """Being paid to consume earns more per exported kWh, not less."""
    required = BatteryEnergyLedger.required_export_price(-0.05, 0.9, 0.0, 0.02)

    assert required == pytest.approx(-0.05 / 0.9 + 0.02)
    assert required < 0


@pytest.mark.parametrize(
    "args",
    [
        (0.1, 0.0, 0.0, 0.0),  # zero efficiency
        (0.1, 1.5, 0.0, 0.0),  # efficiency above 1
        (0.1, float("nan"), 0.0, 0.0),
        (float("nan"), 0.9, 0.0, 0.0),
        (0.1, 0.9, -0.01, 0.0),  # negative degradation
        (0.1, 0.9, 0.0, -0.01),  # negative required profit
    ],
)
def test_required_export_price_rejects_unusable_settings(args) -> None:
    assert BatteryEnergyLedger.required_export_price(*args) is None


# --- export eligibility ----------------------------------------------------


def test_reserve_is_netted_off_the_oldest_energy() -> None:
    """Unknown energy held as reserve never blocks eligible energy above it."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 5)
    ledger.add(EnergyOrigin.SOLAR, 3)

    assert _exportable(ledger, 3, 5, 0.20) == pytest.approx(3)


def test_reserve_larger_than_the_ledger_blocks_every_export() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 4)
    ledger.add(EnergyOrigin.SOLAR, 1)

    assert _exportable(ledger, 5, 6, 0.20) == 0


def test_unknown_energy_partly_above_the_reserve_is_never_exported() -> None:
    """Only the reserve-covered share of an unknown lot is neutralised."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 5)
    ledger.add(EnergyOrigin.SOLAR, 3)

    # 1 kWh of the unknown lot now sits above the reserve and stops the walk.
    assert _exportable(ledger, 10, 4, 0.20) == pytest.approx(3)


def test_unknown_energy_on_top_blocks_the_eligible_energy_underneath() -> None:
    """A discharge cannot reach a deeper lot without moving the newer one."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 3)
    ledger.add(EnergyOrigin.UNKNOWN, 1)

    assert _exportable(ledger, 4, 0, 0.20) == 0


def test_expensive_newer_grid_energy_blocks_cheaper_older_grid_energy() -> None:
    """The marginal kWh is the newest one; a loss-making lot stops the walk."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 1, 0.10)
    ledger.add(EnergyOrigin.GRID, 2, 0.30)

    assert _exportable(ledger, 3, 0, 0.20) == 0


def test_profitable_newer_grid_energy_exports_up_to_the_next_ineligible_lot() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 2, 0.30)
    ledger.add(EnergyOrigin.GRID, 1, 0.10)

    # 0.10/0.9 + 0.02 = 0.1311 <= 0.20, while 0.30/0.9 + 0.02 = 0.3533 > 0.20.
    assert _exportable(ledger, 3, 0, 0.20) == pytest.approx(1)


def test_grid_energy_at_a_negative_purchase_price_exports_below_zero() -> None:
    """Energy bought at a negative all-in price is profitable at a low sale."""
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 2, -0.05)

    required = -0.05 / RTE + PROFIT
    assert _exportable(ledger, 2, 0, required + 0.001) == pytest.approx(2)
    assert _exportable(ledger, 2, 0, required - 0.001) == 0


def test_solar_energy_must_still_cover_wear_and_the_required_margin() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 4)

    assert _exportable(ledger, 4, 0, 0.04, degradation=0.01, profit=0.02) == 4
    assert _exportable(ledger, 4, 0, 0.02, degradation=0.01, profit=0.02) == 0


def test_export_is_limited_to_the_requested_energy() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 10)

    assert _exportable(ledger, 2.5, 0, 0.20) == pytest.approx(2.5)


def test_exportable_does_not_consume_the_ledger() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 4)

    _exportable(ledger, 4, 0, 0.20)

    assert ledger.total_kwh == pytest.approx(4)


@pytest.mark.parametrize(
    "requested,reserve,sell",
    [
        (0, 0, 0.2),
        (-1, 0, 0.2),
        (float("nan"), 0, 0.2),
        (2, -1, 0.2),
        (2, float("nan"), 0.2),
        (2, 0, None),
        (2, 0, float("nan")),
    ],
)
def test_exportable_rejects_unusable_inputs(requested, reserve, sell) -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 4)

    assert _exportable(ledger, requested, reserve, sell) == 0


@pytest.mark.parametrize(
    "overrides",
    [{"rte": 0.0}, {"rte": 1.5}, {"degradation": -0.01}, {"profit": -0.01}],
)
def test_exportable_fails_closed_on_unusable_cost_settings(overrides) -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.SOLAR, 4)

    assert _exportable(ledger, 4, 0, 5.0, **overrides) == 0


# --- bounded growth --------------------------------------------------------


def test_repeated_charges_from_the_same_source_coalesce() -> None:
    """Refresh-by-refresh charging must not append a lot per refresh."""
    ledger = BatteryEnergyLedger()
    for _ in range(50):
        ledger.add(EnergyOrigin.SOLAR, 0.1)

    assert len(ledger.lots) == 1
    assert ledger.lots[0].origin is EnergyOrigin.SOLAR
    assert ledger.total_kwh == pytest.approx(5.0)


def test_grid_charges_at_the_same_price_coalesce() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 1, 0.1)
    ledger.add(EnergyOrigin.GRID, 2, 0.1)

    assert len(ledger.lots) == 1
    assert ledger.lots[0].energy_kwh == pytest.approx(3)
    assert ledger.lots[0].buy_cost_eur_per_kwh == pytest.approx(0.1)


@pytest.mark.parametrize(
    "origin,cost",
    [
        # A different price must stay a separate lot: it has its own break-even.
        (EnergyOrigin.GRID, 0.2),
        # A different origin is priced by a different rule.
        (EnergyOrigin.SOLAR, None),
        (EnergyOrigin.UNKNOWN, None),
    ],
)
def test_lots_that_price_differently_never_merge(origin, cost) -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.GRID, 1, 0.1)
    ledger.add(origin, 1, cost)

    assert len(ledger.lots) == 2


def test_coalescing_keeps_export_eligibility_unchanged() -> None:
    """Merging must not change a single answer the ledger gives."""
    merged = BatteryEnergyLedger()
    merged.add(EnergyOrigin.UNKNOWN, 2)
    for _ in range(4):
        merged.add(EnergyOrigin.GRID, 0.5, 0.05)

    assert len(merged.lots) == 2
    assert _exportable(merged, 10, 2, 0.12) == pytest.approx(2.0)
    assert _exportable(merged, 10, 2, 0.05) == pytest.approx(0.0)


def test_lot_count_is_bounded_for_an_alternating_charge_pattern() -> None:
    """Charging alternately from an attributable and unknown source is bounded."""
    ledger = BatteryEnergyLedger()
    for step in range(4 * MAX_LOTS):
        ledger.add(EnergyOrigin.GRID, 0.01, 0.05 + step / 1000)
        ledger.add(EnergyOrigin.UNKNOWN, 0.01)

    assert len(ledger.lots) <= MAX_LOTS
    assert ledger.total_kwh == pytest.approx(2 * 4 * MAX_LOTS * 0.01)
    # The oldest energy is what gets folded, and it is folded into unknown,
    # which can only ever block an export.
    assert ledger.lots[0].origin is EnergyOrigin.UNKNOWN
    assert ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_folding_the_oldest_lots_never_widens_export_eligibility() -> None:
    ledger = BatteryEnergyLedger()
    # Distinct prices so nothing coalesces, all far below the export break-even.
    for step in range(2 * MAX_LOTS):
        ledger.add(EnergyOrigin.GRID, 1.0, 0.05 + step / 100000)

    # Everything above the reserve is still priced from the newest lot down,
    # and the folded unknown block sits below it.
    exportable = _exportable(ledger, 10, 0, 0.12)
    assert exportable == pytest.approx(10.0)
    assert _exportable(ledger, 10, ledger.total_kwh, 0.12) == pytest.approx(0.0)


def test_storage_rounding_tolerance_grows_with_the_lot_count() -> None:
    """A long-running ledger needs its stored rounding error accounted for."""
    ledger = BatteryEnergyLedger()
    for step in range(10):
        ledger.add(EnergyOrigin.GRID, 1 / 3, 0.05 + step / 1000)

    assert ledger.storage_rounding_tolerance_kwh == pytest.approx(
        10 * ENERGY_STORAGE_ROUNDING_KWH
    )
    stored_total = sum(lot["energy_kwh"] for lot in ledger.as_dict()["lots"])
    assert abs(stored_total - ledger.total_kwh) <= ledger.storage_rounding_tolerance_kwh


# --- persistence -----------------------------------------------------------


def test_storage_round_trip_preserves_order_origin_and_price() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 5)
    ledger.add(EnergyOrigin.GRID, 2, -0.05)
    ledger.add(EnergyOrigin.SOLAR, 1)

    restored = BatteryEnergyLedger.from_dict(ledger.as_dict())

    assert restored is not None
    assert [
        (lot.origin, lot.energy_kwh, lot.buy_cost_eur_per_kwh) for lot in restored.lots
    ] == [
        (EnergyOrigin.UNKNOWN, pytest.approx(5), None),
        (EnergyOrigin.GRID, pytest.approx(2), pytest.approx(-0.05)),
        (EnergyOrigin.SOLAR, pytest.approx(1), None),
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "ledger",
        {},
        {"version": 2, "lots": []},
        {"version": 1},
        {"version": 1, "lots": {}},
        {"version": 1, "lots": ["grid"]},
        {"version": 1, "lots": [{"energy_kwh": 1}]},
        {"version": 1, "lots": [{"origin": "wind", "energy_kwh": 1}]},
        {"version": 1, "lots": [{"origin": "solar", "energy_kwh": "nan"}]},
        {"version": 1, "lots": [{"origin": "solar", "energy_kwh": float("nan")}]},
        {"version": 1, "lots": [{"origin": "solar", "energy_kwh": float("inf")}]},
        {"version": 1, "lots": [{"origin": "solar", "energy_kwh": -1}]},
        {"version": 1, "lots": [{"origin": "solar", "energy_kwh": 0}]},
        # A grid lot must carry a usable price.
        {"version": 1, "lots": [{"origin": "grid", "energy_kwh": 1}]},
        {
            "version": 1,
            "lots": [
                {"origin": "grid", "energy_kwh": 1, "buy_cost_eur_per_kwh": "0.1"}
            ],
        },
        # A non-grid lot must not carry one.
        {
            "version": 1,
            "lots": [{"origin": "solar", "energy_kwh": 1, "buy_cost_eur_per_kwh": 0.1}],
        },
    ],
)
def test_malformed_storage_fails_closed(payload) -> None:
    assert BatteryEnergyLedger.from_dict(payload) is None


def test_one_malformed_lot_invalidates_the_whole_payload() -> None:
    """Keeping the good lots would claim energy the battery no longer has."""
    payload = {
        "version": 1,
        "lots": [
            {"origin": "solar", "energy_kwh": 3, "buy_cost_eur_per_kwh": None},
            {"origin": "grid", "energy_kwh": 2},
        ],
    }

    assert BatteryEnergyLedger.from_dict(payload) is None


def test_empty_stored_ledger_loads_as_an_empty_ledger() -> None:
    restored = BatteryEnergyLedger.from_dict({"version": 1, "lots": []})

    assert restored is not None
    assert restored.lots == ()
    assert restored.total_kwh == 0


def test_serialized_lot_values_are_finite_and_rounded() -> None:
    ledger = BatteryEnergyLedger([EnergyLot(EnergyOrigin.GRID, 1 / 3, 1 / 7)])

    payload = ledger.as_dict()["lots"][0]

    assert math.isfinite(payload["energy_kwh"])
    assert payload["energy_kwh"] == round(1 / 3, 6)
    assert payload["buy_cost_eur_per_kwh"] == round(1 / 7, 6)


def test_summary_reports_energy_per_origin() -> None:
    ledger = BatteryEnergyLedger()
    ledger.add(EnergyOrigin.UNKNOWN, 5)
    ledger.add(EnergyOrigin.GRID, 2, 0.1)
    ledger.add(EnergyOrigin.SOLAR, 1)

    assert ledger.summary() == {
        "lot_count": 3,
        "total_kwh": pytest.approx(8),
        "energy_by_origin": {
            "unknown": pytest.approx(5),
            "grid": pytest.approx(2),
            "solar": pytest.approx(1),
        },
        "newest_lot_origin": "solar",
    }
