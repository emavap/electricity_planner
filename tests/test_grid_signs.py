"""Regression tests for the raw P1 versus Planner setpoint sign boundary."""

from custom_components.electricity_planner.grid_signs import (
    sensor_grid_export_w,
    sensor_grid_import_w,
    sensor_grid_to_planner_power,
)


def test_raw_p1_import_converts_to_positive_planner_power() -> None:
    """Raw P1 import is negative, while a Planner target is positive."""
    assert sensor_grid_import_w(-6500.0) == 6500.0
    assert sensor_grid_export_w(-6500.0) == 0.0
    assert sensor_grid_to_planner_power(-6500.0) == 6500.0


def test_raw_p1_export_converts_to_negative_planner_power() -> None:
    """Raw P1 export is positive, while a Planner export target is negative."""
    assert sensor_grid_import_w(6500.0) == 0.0
    assert sensor_grid_export_w(6500.0) == 6500.0
    assert sensor_grid_to_planner_power(6500.0) == -6500.0
