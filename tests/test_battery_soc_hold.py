"""Tests for holding a battery's last valid SOC across a sensor dropout.

A battery whose SOC sensor briefly goes unavailable used to be dropped from the
fleet entirely, silently re-weighting the capacity-weighted average onto the
remaining batteries. With mismatched capacities that shifts the average far
enough to cross ``max_soc_threshold`` and flip charging decisions even though no
charge physically moved.
"""

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.electricity_planner.battery_analysis import (
    BatteryAnalysisCalculator,
)
from custom_components.electricity_planner.const import (
    BATTERY_SOC_HOLD_SECONDS,
    CONF_BATTERY_CAPACITIES,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_SOC_THRESHOLD,
)
from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine

# Mirrors the reported installation: a 30 kWh pack at 47% alongside a 10 kWh
# pack at 8%. Weighted together they sit below a 40% threshold; the large pack
# alone sits above it.
LARGE_BATTERY = "sensor.victron_system_battery_soc"
SMALL_BATTERY = "sensor.batteries_state_of_capacity"


def _engine(config=None):
    base_config = {
        CONF_MAX_GRID_POWER: 12000,
        CONF_MAX_BATTERY_POWER: 4000,
        CONF_MAX_CAR_POWER: 7000,
        CONF_MAX_SOC_THRESHOLD: 40,
        CONF_BATTERY_CAPACITIES: {
            LARGE_BATTERY: 30.0,
            SMALL_BATTERY: 10.0,
        },
    }
    if config:
        base_config.update(config)
    return ChargingDecisionEngine(hass=None, config=base_config)


def _calculator(config=None) -> BatteryAnalysisCalculator:
    return _engine(config)._battery_analysis


def _both_available():
    return [
        {"entity_id": LARGE_BATTERY, "soc": 47.0},
        {"entity_id": SMALL_BATTERY, "soc": 8.0},
    ]


def _small_unavailable():
    return [
        {"entity_id": LARGE_BATTERY, "soc": 47.0},
        {"entity_id": SMALL_BATTERY, "soc": None},
    ]


def test_weighted_average_with_both_batteries_present():
    """Baseline: (47*30 + 8*10) / 40 = 37.25%, below the 40% threshold."""
    calc = _calculator()

    result = calc.analyze(_both_available())

    assert result["average_soc"] == pytest.approx(37.25)
    assert result["batteries_count"] == 2
    assert result["batteries_full"] is False
    assert "soc_hold_active" not in result


def test_dropout_holds_last_soc_instead_of_reweighting():
    """The regression under test: a dropout must not move the average."""
    calc = _calculator()
    calc.analyze(_both_available())

    result = calc.analyze(_small_unavailable())

    # Without the hold this returns 47.0 — the large battery alone — which
    # crosses the 40% threshold and reports the fleet as full.
    assert result["average_soc"] == pytest.approx(37.25)
    assert result["batteries_count"] == 2
    assert result["batteries_full"] is False
    assert result["soc_hold_active"] is True
    assert result["soc_held_entity_ids"] == [SMALL_BATTERY]


def test_dropout_does_not_flip_batteries_full_across_threshold():
    """Guard the decision-visible flag the flicker actually toggled."""
    calc = _calculator()

    assert calc.analyze(_both_available())["batteries_full"] is False
    assert calc.analyze(_small_unavailable())["batteries_full"] is False


def test_held_soc_expires_after_hold_window():
    """Beyond the hold window the stale reading is discarded, not trusted."""
    calc = _calculator()
    start = dt_util.utcnow()

    calc.analyze(_both_available())

    expired = start + timedelta(seconds=BATTERY_SOC_HOLD_SECONDS + 1)
    result = calc.analyze(_small_unavailable(), _now=expired)

    assert result["average_soc"] == pytest.approx(47.0)
    assert result["batteries_count"] == 1
    assert "soc_hold_active" not in result


def test_held_soc_still_valid_just_inside_hold_window():
    """A reading inside the window is still used."""
    calc = _calculator()
    start = dt_util.utcnow()

    calc.analyze(_both_available())

    inside = start + timedelta(seconds=BATTERY_SOC_HOLD_SECONDS - 1)
    result = calc.analyze(_small_unavailable(), _now=inside)

    assert result["average_soc"] == pytest.approx(37.25)
    assert result["batteries_count"] == 2


def test_recovered_sensor_refreshes_the_hold():
    """A fresh reading replaces the held value and resets its age."""
    calc = _calculator()
    calc.analyze(_both_available())

    recovered = [
        {"entity_id": LARGE_BATTERY, "soc": 47.0},
        {"entity_id": SMALL_BATTERY, "soc": 20.0},
    ]
    assert calc.analyze(recovered)["average_soc"] == pytest.approx(
        (47.0 * 30 + 20.0 * 10) / 40
    )

    # The newer 20% value is what gets held, not the original 8%.
    held = calc.analyze(_small_unavailable())
    assert held["average_soc"] == pytest.approx((47.0 * 30 + 20.0 * 10) / 40)


def test_no_hold_without_a_prior_valid_reading():
    """A battery unavailable since startup must not fabricate a value."""
    calc = _calculator()

    result = calc.analyze(_small_unavailable())

    assert result["average_soc"] == pytest.approx(47.0)
    assert result["batteries_count"] == 1
    assert "soc_hold_active" not in result


def test_all_batteries_unavailable_reports_unavailable():
    """Holding one battery must not mask a total loss of battery data."""
    calc = _calculator()
    calc.analyze(_both_available())

    both_gone = [
        {"entity_id": LARGE_BATTERY, "soc": None},
        {"entity_id": SMALL_BATTERY, "soc": None},
    ]
    held = calc.analyze(both_gone)

    # Within the window both are held, so the fleet is still reported.
    assert held["batteries_available"] is True
    assert held["average_soc"] == pytest.approx(37.25)

    expired = dt_util.utcnow() + timedelta(seconds=BATTERY_SOC_HOLD_SECONDS + 1)
    gone = calc.analyze(both_gone, _now=expired)
    assert gone["batteries_available"] is False
    assert gone["average_soc"] is None


def test_out_of_range_soc_is_not_held():
    """An implausible reading is rejected and never cached."""
    calc = _calculator()

    out_of_range = [
        {"entity_id": LARGE_BATTERY, "soc": 47.0},
        {"entity_id": SMALL_BATTERY, "soc": 140.0},
    ]
    assert calc.analyze(out_of_range)["batteries_count"] == 1

    result = calc.analyze(_small_unavailable())
    assert result["batteries_count"] == 1
    assert "soc_hold_active" not in result


def test_hold_survives_settings_refresh():
    """refresh_settings() rebuilds the calculator; the hold must persist."""
    engine = _engine()
    engine._battery_analysis.analyze(_both_available())

    engine.refresh_settings(
        {
            CONF_MAX_GRID_POWER: 12000,
            CONF_MAX_BATTERY_POWER: 4000,
            CONF_MAX_CAR_POWER: 7000,
            CONF_MAX_SOC_THRESHOLD: 40,
            CONF_BATTERY_CAPACITIES: {
                LARGE_BATTERY: 30.0,
                SMALL_BATTERY: 10.0,
            },
        }
    )

    result = engine._battery_analysis.analyze(_small_unavailable())
    assert result["average_soc"] == pytest.approx(37.25)
    assert result["soc_hold_active"] is True


def test_unweighted_average_also_holds():
    """Without configured capacities the simple mean is held too."""
    calc = _calculator({CONF_BATTERY_CAPACITIES: {}})

    calc.analyze(_both_available())
    result = calc.analyze(_small_unavailable())

    assert result["average_soc"] == pytest.approx(27.5)
    assert result["batteries_count"] == 2


def test_battery_without_entity_id_is_not_cached():
    """Missing entity_id cannot be keyed, so it is simply dropped."""
    calc = _calculator()

    calc.analyze([{"entity_id": LARGE_BATTERY, "soc": 47.0}, {"soc": 8.0}])
    result = calc.analyze([{"entity_id": LARGE_BATTERY, "soc": 47.0}, {"soc": None}])

    assert result["batteries_count"] == 1
    assert "soc_hold_active" not in result


# ---------------------------------------------------------------------------
# resolve_for_publication — the coordinator-facing seam
# ---------------------------------------------------------------------------


def test_publication_substitutes_held_soc():
    """Published data must carry the held value, not None."""
    calc = _calculator()
    calc.analyze(_both_available())

    published = calc.resolve_for_publication(_small_unavailable())

    assert [b["soc"] for b in published] == [47.0, 8.0]
    assert all(b["soc"] is not None for b in published)


def test_publication_drops_unusable_readings():
    """A battery with no held value must not publish soc=None downstream.

    negative_buy.py coerces a missing SOC to 0.0, which would overstate battery
    headroom, so an unresolvable battery is dropped instead of published.
    """
    calc = _calculator()

    published = calc.resolve_for_publication(_small_unavailable())

    assert [b["entity_id"] for b in published] == [LARGE_BATTERY]


def test_publication_records_holds_itself():
    """Publication must seed the cache; it runs before analyze() each refresh.

    The coordinator publishes data before the decision engine calls analyze, so
    if publication did not record, the cache would always be one refresh behind
    and the first dropout would have nothing to hold.
    """
    calc = _calculator()

    calc.resolve_for_publication(_both_available())

    assert set(calc.soc_hold_state) == {LARGE_BATTERY, SMALL_BATTERY}
    published = calc.resolve_for_publication(_small_unavailable())
    assert [b["soc"] for b in published] == [47.0, 8.0]


def test_publication_expires_stale_holds():
    """Beyond the window a held reading is discarded, not published."""
    calc = _calculator()
    start = dt_util.utcnow()
    calc.resolve_for_publication(_both_available())

    expired = start + timedelta(seconds=BATTERY_SOC_HOLD_SECONDS + 1)
    published = calc.resolve_for_publication(_small_unavailable(), _now=expired)

    assert [b["entity_id"] for b in published] == [LARGE_BATTERY]
    assert SMALL_BATTERY not in calc.soc_hold_state


def test_engine_exposes_publication_helper():
    """The coordinator calls through the engine, so the seam must exist."""
    engine = _engine()
    engine._battery_analysis.analyze(_both_available())

    published = engine.resolve_held_battery_soc(_small_unavailable())

    assert [b["soc"] for b in published] == [47.0, 8.0]


def test_coordinator_shaped_input_round_trip():
    """End-to-end on the real input shape the coordinator emits.

    The coordinator previously omitted unavailable batteries entirely; it now
    emits {"entity_id": ..., "soc": None}. This guards that contract, since a
    bare omission would leave the hold logic with nothing to match on.
    """
    engine = _engine()

    engine._battery_analysis.analyze(_both_available())

    coordinator_emits = [
        {"entity_id": LARGE_BATTERY, "soc": 47.0},
        {"entity_id": SMALL_BATTERY, "soc": None},
    ]
    published = engine.resolve_held_battery_soc(coordinator_emits)
    analysis = engine._battery_analysis.analyze(published)

    assert analysis["average_soc"] == pytest.approx(37.25)
    assert analysis["batteries_count"] == 2
    assert analysis["batteries_full"] is False


def test_omitted_battery_cannot_be_held():
    """Documents why the coordinator must emit a placeholder, not omit."""
    engine = _engine()
    engine._battery_analysis.analyze(_both_available())

    # Pre-fix coordinator behaviour: the battery is simply absent.
    omitted = [{"entity_id": LARGE_BATTERY, "soc": 47.0}]
    analysis = engine._battery_analysis.analyze(
        engine.resolve_held_battery_soc(omitted)
    )

    # Nothing can be held, so the average re-weights and crosses the threshold.
    assert analysis["average_soc"] == pytest.approx(47.0)
    assert analysis["batteries_full"] is True
