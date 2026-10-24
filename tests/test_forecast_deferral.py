"""Tests for the bounded forward-looking battery charge deferral."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.electricity_planner.const import (
    CONF_BATTERY_CAPACITIES,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_PRICE_THRESHOLD,
)
from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine
from custom_components.electricity_planner.strategies import (
    StrategyManager,
    evaluate_forecast_deferral,
)

NOW = datetime(2026, 10, 24, 16, 50, tzinfo=timezone.utc)


def _segments(prices, start=None, minutes=15):
    """Build consecutive future segments starting at the next interval."""
    start = start or datetime(2026, 10, 24, 17, 0, tzinfo=timezone.utc)
    out = []
    for idx, price in enumerate(prices):
        seg_start = start + timedelta(minutes=minutes * idx)
        out.append((seg_start, seg_start + timedelta(minutes=minutes), price))
    return out


def _context(**overrides):
    """Replay of the evidence snapshot: 38% SOC, 0.247 now, 0.25 battery floor."""
    context = {
        "now": NOW,
        "price_analysis": {
            "current_price": 0.247,
            "price_threshold": 0.25,
            "highest_price": 0.258,
            "lowest_price": 0.136,
            "next_price": 0.258,
            "very_low_price": False,
        },
        "battery_analysis": {
            "average_soc": 38.0,
            "min_soc": 38.0,
            "max_soc_threshold": 40,
        },
        "power_analysis": {
            "significant_solar_surplus": False,
            "solar_surplus": 0,
            "house_consumption": 419.0,
        },
        "power_allocation": {},
        # Healthy fleet by default; the real 18:49 packs are in SNAPSHOT_PACKS.
        "battery_soc_readings": (
            {"entity_id": "sensor.victron_system_battery_soc", "soc": 38.0},
            {"entity_id": "sensor.batteries_state_of_capacity", "soc": 38.0},
        ),
        "battery_capacities": {
            "sensor.victron_system_battery_soc": 30.0,
            "sensor.batteries_state_of_capacity": 10.0,
        },
        "config": {"emergency_soc_threshold": 5, "predictive_charging_min_soc": 30},
        "battery_stable_threshold": 0.25,
        # 2h at peak-ish prices then a long cheap stretch, like 24 Oct evening.
        "battery_forecast_prices": _segments([0.258] * 4 + [0.18] * 4 + [0.14] * 24),
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(context.get(key), dict):
            context[key] = {**context[key], **value}
        else:
            context[key] = value
    return context


def _baseline(context):
    """Same context without forecast data (pre-change behaviour)."""
    without = dict(context)
    without["battery_forecast_prices"] = []
    return StrategyManager(use_dynamic_threshold=True).evaluate(without)


SNAPSHOT_PACKS = {
    # 18:49 snapshot: weighted 38.25% hides a 9% 10kWh and a 48% 30kWh pack.
    "battery_soc_readings": (
        {"entity_id": "sensor.victron_system_battery_soc", "soc": 48.0},
        {"entity_id": "sensor.batteries_state_of_capacity", "soc": 9.0},
    ),
    "battery_analysis": {"average_soc": 38.25, "min_soc": 9.0, "max_soc_threshold": 40},
}


def _snapshot(**overrides):
    context = _context(**overrides)
    context.update(SNAPSHOT_PACKS)
    return context


def test_healthy_fleet_defers_instead_of_soc_based_charge():
    context = _context()
    # Without forecast the SOC-based safety net charges near the peak.
    assert _baseline(context)[0] is True

    manager = StrategyManager(use_dynamic_threshold=True)
    should_charge, reason = manager.evaluate(context)

    assert should_charge is False
    assert reason.startswith("Deferring grid charge")
    assert manager.get_last_trace()[-1]["strategy"] == "ForecastDeferralGuard"


def test_snapshot_case_is_not_deferred_weak_pack_cannot_bridge():
    """9% x 10kWh = 0.4kWh above 5% < 419W x 1.2h x 2 ≈ 1.0kWh → no deferral."""
    assert evaluate_forecast_deferral(_snapshot(), **_KW) is None


def test_snapshot_with_wrong_fallback_threshold_still_charges():
    # Pinned 0.25 fallback (pre-fix): price guard passes, SOC-based charges.
    assert StrategyManager(use_dynamic_threshold=True).evaluate(_snapshot())[0]


def test_snapshot_with_correct_average_threshold_blocks_charge():
    # Snapshot-source fix: decisions now see the 0.1584 average.
    context = _snapshot(battery_stable_threshold=0.1584)
    context["price_analysis"]["price_threshold"] = 0.1584
    should_charge, reason = StrategyManager(use_dynamic_threshold=True).evaluate(
        context
    )
    assert should_charge is False
    assert "exceeds" in reason


@pytest.mark.parametrize("forecast", [None, [], ()])
def test_missing_or_stale_forecast_never_defers(forecast):
    context = _context(battery_forecast_prices=forecast)
    should_charge, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert should_charge is True


def test_emergency_soc_is_never_deferred():
    context = _context(
        battery_analysis={"average_soc": 4.0, "min_soc": 4.0},
        price_analysis={"current_price": 0.40},
    )
    should_charge, reason = StrategyManager(use_dynamic_threshold=True).evaluate(
        context
    )
    assert should_charge is True
    assert "Emergency" in reason


def test_weakest_battery_at_emergency_blocks_deferral():
    context = _context(battery_analysis={"min_soc": 5.0})
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


@pytest.mark.parametrize("soc", [None, 30.0, 25.0])
def test_soc_unknown_or_at_reserve_does_not_defer(soc):
    context = _context(battery_analysis={"average_soc": soc})
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def test_settings_reserve_takes_precedence_over_config():
    class _Settings:
        predictive_min_soc = 40
        emergency_soc_threshold = 5
        soc_price_multiplier_max = 1.0
        soc_buffer_target = 0
        dynamic_threshold_confidence = 60

    context = _context(settings=_Settings())
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def test_cheaper_window_too_far_away_does_not_defer():
    late_start = NOW + timedelta(hours=7)
    context = _context(battery_forecast_prices=_segments([0.12] * 16, start=late_start))
    should_charge, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert should_charge is True


def test_short_cheap_dip_does_not_defer():
    context = _context(battery_forecast_prices=_segments([0.258, 0.12, 0.12, 0.258]))
    should_charge, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert should_charge is True


def test_small_price_improvement_does_not_defer():
    # 0.22 is only ~11% cheaper than 0.247.
    context = _context(battery_forecast_prices=_segments([0.22] * 32))
    should_charge, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert should_charge is True


def test_current_interval_segment_is_not_a_reason_to_wait():
    current = (NOW - timedelta(minutes=5), NOW + timedelta(minutes=10), 0.10)
    context = _context(battery_forecast_prices=[current])
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def test_cheap_slots_above_effective_threshold_do_not_count():
    # 0.20 is 19% cheaper but above a strict 0.15 threshold: waiting would
    # not lead to a charge later, so do not defer.
    context = _context(battery_forecast_prices=_segments([0.20] * 32))
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.15,
            emergency_threshold=5,
        )
        is None
    )


def test_hysteresis_requires_bigger_drop_to_interrupt_running_charge():
    # 0.205 is ~17% cheaper: enough to keep waiting, not to stop a charge.
    forecast = _segments([0.205] * 32)
    idle = _context(battery_forecast_prices=forecast)
    charging = _context(
        battery_forecast_prices=forecast, previous_battery_grid_charging=True
    )
    kwargs = {
        "current_price": 0.247,
        "effective_threshold": 0.25,
        "emergency_threshold": 5,
    }
    assert evaluate_forecast_deferral(idle, **kwargs) is not None
    assert evaluate_forecast_deferral(charging, **kwargs) is None


@pytest.mark.parametrize("current_price", [0.0, -0.02])
def test_non_positive_price_never_defers(current_price):
    context = _context(battery_forecast_prices=_segments([-0.10] * 32))
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=current_price,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def test_very_low_price_never_defers():
    context = _context(price_analysis={"very_low_price": True})
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def test_malformed_segment_fails_closed():
    context = _context(battery_forecast_prices=[("bad",)])
    assert (
        evaluate_forecast_deferral(
            context,
            current_price=0.247,
            effective_threshold=0.25,
            emergency_threshold=5,
        )
        is None
    )


def _engine():
    return ChargingDecisionEngine(
        hass=None,
        config={
            CONF_MAX_GRID_POWER: 12000,
            CONF_MAX_BATTERY_POWER: 4000,
            CONF_MAX_CAR_POWER: 7000,
            CONF_PRICE_THRESHOLD: 0.25,
            "buy_vat_multiplier": 1.0,  # Payload prices already all-in.
            CONF_BATTERY_CAPACITIES: {"sensor.battery_main": 10.0},
        },
    )


def _engine_payload(forecast):
    return {
        "current_price": 0.247,
        "highest_price": 0.258,
        "lowest_price": 0.136,
        "next_price": 0.258,
        "battery_stable_threshold": 0.25,
        "battery_soc": [{"entity_id": "sensor.battery_main", "soc": 38}],
        "solar_production": 0,
        "house_consumption": 400,
        "solar_surplus": 0,
        "car_charging_power": 0,
        "monthly_grid_peak": 0,
        "battery_forecast_prices": forecast,
    }


@pytest.mark.asyncio
async def test_decision_engine_wires_forecast_to_battery_only():
    real_now = datetime.now(timezone.utc)
    start = real_now + timedelta(minutes=30)
    forecast = _segments([0.14] * 16, start=start)

    baseline = await _engine().evaluate_charging_decision(_engine_payload([]))
    deferred = await _engine().evaluate_charging_decision(_engine_payload(forecast))

    assert baseline["battery_grid_charging"] is True
    assert deferred["battery_grid_charging"] is False
    assert deferred["battery_grid_charging_reason"].startswith("Deferring grid charge")
    # The car decision is independent of the battery deferral.
    assert deferred["car_grid_charging"] == baseline["car_grid_charging"]


_KW = {"current_price": 0.247, "effective_threshold": 0.25, "emergency_threshold": 5}


def test_reason_reports_weakest_pack_bridge():
    reason = evaluate_forecast_deferral(_context(), **_KW)
    # 10kWh x 33% = 3.3kWh weakest; 419W x ~1.17h x 2 ≈ 1.0kWh.
    assert "weakest battery 3.3kWh above 5% emergency" in reason


@pytest.mark.parametrize(
    "override",
    [
        {"battery_capacities": {}},
        {"battery_capacities": {"sensor.victron_system_battery_soc": 30.0}},
        {"battery_soc_readings": ()},
        {
            "battery_soc_readings": (
                {"entity_id": "sensor.victron_system_battery_soc", "soc": None},
            )
        },
        {"power_analysis": {"house_consumption": None}},
        {"power_analysis": {"house_consumption": 0}},
    ],
)
def test_unknown_bridge_inputs_keep_existing_charge(override):
    context = _context()
    context.update(override)  # replace, not merge
    assert evaluate_forecast_deferral(context, **_KW) is None
    assert StrategyManager(use_dynamic_threshold=True).evaluate(context)[0]


def test_insufficient_stored_energy_does_not_defer():
    # Weakest 3.3kWh cannot bridge 2kW x ~1.2h x 2 ≈ 4.7kWh.
    context = _context(power_analysis={"house_consumption": 2000.0})
    assert evaluate_forecast_deferral(context, **_KW) is None


def test_energy_is_never_pooled_across_packs():
    # 30kWh pack has plenty, 10kWh pack at 9% has 0.4kWh: pooled would pass.
    context = _context(
        battery_soc_readings=(
            {"entity_id": "sensor.victron_system_battery_soc", "soc": 90.0},
            {"entity_id": "sensor.batteries_state_of_capacity", "soc": 9.0},
        ),
    )
    context["battery_analysis"].pop("min_soc")
    assert evaluate_forecast_deferral(context, **_KW) is None


NAN = float("nan")
INF = float("inf")


@pytest.mark.parametrize("bad", [NAN, INF, -INF, "x", True])
@pytest.mark.parametrize(
    "where",
    ["current_price", "effective_threshold", "emergency_threshold"],
)
def test_non_finite_scalars_fail_closed(where, bad):
    kwargs = dict(_KW)
    kwargs[where] = bad
    assert evaluate_forecast_deferral(_context(), **kwargs) is None


@pytest.mark.parametrize("bad", [NAN, INF, -1.0, 101.0, "x"])
def test_non_finite_or_out_of_range_soc_fails_closed(bad):
    for override in (
        {"battery_analysis": {"average_soc": bad}},
        {"battery_analysis": {"min_soc": bad}},
    ):
        assert evaluate_forecast_deferral(_context(**override), **_KW) is None
    context = _context()
    context["battery_soc_readings"] = (
        {"entity_id": "sensor.victron_system_battery_soc", "soc": 38.0},
        {"entity_id": "sensor.batteries_state_of_capacity", "soc": bad},
    )
    assert evaluate_forecast_deferral(context, **_KW) is None


@pytest.mark.parametrize("bad", [NAN, INF, -5.0, 0.0, None])
def test_non_finite_capacity_or_load_fails_closed(bad):
    context = _context()
    context["battery_capacities"] = {
        "sensor.victron_system_battery_soc": 30.0,
        "sensor.batteries_state_of_capacity": bad,
    }
    assert evaluate_forecast_deferral(context, **_KW) is None
    load = _context(power_analysis={"house_consumption": bad})
    assert evaluate_forecast_deferral(load, **_KW) is None


def _good_forecast():
    return _segments([0.258] * 4 + [0.18] * 4 + [0.14] * 24)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda f: f + [("bad",)],
        lambda f: f + [None],
        lambda f: f + [(f[-1][1], f[-1][1] + timedelta(minutes=15), NAN)],
        lambda f: f + [(f[-1][1], f[-1][1] + timedelta(minutes=15), INF)],
        lambda f: f + [(f[-1][1], f[-1][1], 0.1)],  # zero length
        lambda f: f + [(f[-1][1], f[-1][1] - timedelta(minutes=15), 0.1)],
        lambda f: f
        + [
            (
                f[-1][1].replace(tzinfo=None),
                f[-1][1].replace(tzinfo=None) + timedelta(minutes=15),
                0.1,
            )
        ],
        lambda f: f + [("2026-10-24T20:00", "2026-10-24T20:15", 0.1)],
        lambda f: f + [f[10]],  # duplicate → overlap
        lambda f: f
        + [(f[8][0] + timedelta(minutes=5), f[8][1] + timedelta(minutes=5), 0.1)],
        lambda f: "not a list",
    ],
)
def test_malformed_forecast_rejected_wholesale(mutate):
    context = _context(battery_forecast_prices=mutate(_good_forecast()))
    assert evaluate_forecast_deferral(context, **_KW) is None


def test_overlapping_slots_cannot_inflate_window():
    # Eight copies of one 15-min cheap slot would sum to 2h if not rejected.
    slot = _segments([0.14], start=NOW + timedelta(minutes=10))[0]
    context = _context(battery_forecast_prices=[slot] * 8)
    assert evaluate_forecast_deferral(context, **_KW) is None


def test_gaps_break_cheap_run():
    # Four 15-min cheap slots separated by 15-min gaps: 1h total, not contiguous.
    start = NOW + timedelta(minutes=10)
    forecast = [
        (
            start + timedelta(minutes=30 * i),
            start + timedelta(minutes=30 * i + 15),
            0.14,
        )
        for i in range(4)
    ]
    assert (
        evaluate_forecast_deferral(_context(battery_forecast_prices=forecast), **_KW)
        is None
    )
    contiguous = _segments([0.14] * 4, start=start)
    assert evaluate_forecast_deferral(
        _context(battery_forecast_prices=contiguous), **_KW
    )


def test_expensive_slot_breaks_cheap_run():
    forecast = _segments([0.14, 0.14, 0.258, 0.14, 0.14, 0.258] * 3)
    assert (
        evaluate_forecast_deferral(_context(battery_forecast_prices=forecast), **_KW)
        is None
    )


def test_naive_now_fails_closed():
    context = _context(now=NOW.replace(tzinfo=None))
    assert evaluate_forecast_deferral(context, **_KW) is None
