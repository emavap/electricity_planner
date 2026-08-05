"""Authoritative grid-power sign conversions.

``grid_power`` is the raw value read from this installation's ``sensor.grid``
P1 template.  Its convention is deliberately the inverse of the Planner ESS
target convention:

* raw sensor: negative = import, positive = export;
* Planner setpoint: positive = import, negative = export.

Keep every conversion here.  Do not add ad-hoc unary-minus expressions at
call sites: this distinction is safety-critical for peak limiting, EV
feedback, and inverter derating.
"""

from __future__ import annotations


def sensor_grid_to_planner_power(raw_power_w: float) -> float:
    """Convert raw P1 sensor power to the Planner setpoint convention."""
    return -raw_power_w


def sensor_grid_import_w(raw_power_w: float) -> float:
    """Return the import magnitude from a raw P1 sensor reading."""
    return max(0.0, sensor_grid_to_planner_power(raw_power_w))


def sensor_grid_export_w(raw_power_w: float) -> float:
    """Return the export magnitude from a raw P1 sensor reading."""
    return max(0.0, raw_power_w)
