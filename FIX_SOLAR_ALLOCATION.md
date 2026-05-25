# Solar Allocation Fix: Idle EV Reserve Curtailment

## Bug Confirmation

The reported bug is valid.

`max_soc_threshold_solar` is meant to reserve solar surplus for EV charging once
the batteries have reached a useful SOC. Before this fix, that reserve applied
even when the EV could not use the power:

1. `battery_allocation()` stopped allocating to batteries once average SOC was
   at or above `max_soc_threshold_solar - soc_safety_margin`.
2. `allocate()` then tried `bootstrap_car_allocation()` only because the car was
   not currently charging.
3. If the idle-car bootstrap gate failed, `solar_for_car` stayed at `0`.
4. The unallocated power became `remaining_solar`.
5. When feed-in was disabled or unattractive, `inverter_derating.py` could then
   curtail production even though the batteries still had room under the normal
   battery ceiling.

This makes `max_soc_threshold_solar` behave like an unconditional battery cap,
instead of an EV-reservation threshold.

## Fix Implemented

Changed `custom_components/electricity_planner/solar_allocation.py`.

The allocator now keeps the existing priority order:

1. Allocate the normal battery reserve using `max_soc_threshold_solar`.
2. If the car is charging, keep the existing active-car allocation behavior.
3. If the car is idle, try the existing bootstrap allocation.
4. If idle-car bootstrap returns `0`, allocate the otherwise-leftover solar back
   to batteries using `battery_analysis["max_soc_threshold"]` or
   `settings.max_soc_threshold`.

`battery_allocation()` now accepts optional parameters:

- `soc_cap`: lets callers choose the SOC ceiling instead of always using
  `max_soc_threshold_solar`.
- `safety_margin`: defaults to the existing solar safety margin, but the idle
  fallback uses `0` so it can allocate solar up to the selected normal ceiling.

The fallback is bounded by remaining battery inverter headroom:

```python
fallback_headroom = max(0, settings.max_battery_power - solar_for_batteries)
```

That keeps total `solar_for_batteries` within `max_battery_power` and preserves
the existing allocation validator contract.

## Test Coverage

Added a regression test in `tests/test_decision_engine_car.py`:

- `test_idle_car_solar_falls_back_to_batteries_above_solar_threshold`

It covers the failure window:

- car is not charging
- average SOC is above `max_soc_threshold_solar - 5`
- min SOC is below `max_soc_threshold_solar - 10`, so EV bootstrap is not offered
- batteries are below the normal max SOC ceiling

Expected result: solar goes to batteries instead of becoming `remaining_solar`.

Existing solar allocation tests are in `tests/test_decision_engine_car.py`; there
is no dedicated `test_solar_allocation.py`.

## Edge Cases

- Active EV charging is unchanged; it still gets surplus after the battery
  reserve slice.
- Idle EV bootstrap is unchanged when it succeeds; the fallback only runs when
  `solar_for_car == 0`.
- Missing battery telemetry still returns no battery allocation.
- Batteries at or above the selected fallback cap still leave solar as remaining.
- Total battery allocation remains capped by `max_battery_power`.
- Sunny-day grid SOC limits are applied later in the decision engine today, so
  this fallback uses the `max_soc_threshold` present in `battery_analysis`. If
  solar allocation should also honor sunny-day limits, the decision flow should
  pass a sunny-adjusted battery analysis into `_allocate_solar_power()`.

## Test Results

Targeted suite:

```text
pytest tests/test_decision_engine_car.py -q
24 passed
```

Full suite:

```text
pytest
562 passed
```
