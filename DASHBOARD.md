# Electricity Planner Dashboards

This repository ships four dashboard assets:

1. `custom_components/electricity_planner/dashboard_template.yaml`
   The canonical shared managed dashboard template used by the integration.
2. `custom_components/electricity_planner/dashboard_template_3phase_appendix.yaml`
   The managed three-phase appendix that is appended automatically for three-phase entries.
3. `electricity_planner_dashboard.yaml`
   A bundled static single-phase example for manual dashboards.
4. `electricity_planner_3phase_dashboard.yaml`
   A bundled static three-phase example with the shared single-phase sections plus per-phase status.

## Card Requirements

Managed dashboards require:

- [Gauge Card Pro](https://github.com/benjamin-dcs/gauge-card-pro)
- [ApexCharts Card](https://github.com/RomRider/apexcharts-card)
- [Button Card](https://github.com/custom-cards/button-card)

Three-phase managed and bundled dashboards additionally require:

- [Template Entity Row](https://github.com/thomasloven/lovelace-template-entity-row)
- [card-mod](https://github.com/thomasloven/lovelace-card-mod)

## Recommended Usage

- Use the managed dashboard if you want the layout that stays in sync with the current integration release in either topology.
- Use `electricity_planner_dashboard.yaml` if you want a static single-phase dashboard you can edit manually.
- Use `electricity_planner_3phase_dashboard.yaml` if you need the per-phase operational view for three-phase systems.

## Current Dashboard Controls

The current arbitrage-related dashboard surface is:

- `switch.electricity_planner_battery_dump_to_grid` as `Arbitrage mode`
- `number.electricity_planner_battery_dump_target_soc` as `Arbitrage Reserve SOC`
- `Arbitrage Threshold` line on the price chart when arbitrage mode is active

The dashboards also keep the normal battery controls visible:

- `number.electricity_planner_max_soc_threshold`
- `number.electricity_planner_max_soc_threshold_sunny`
- `number.electricity_planner_sunny_forecast_threshold_kwh`
- `switch.electricity_planner_disable_battery_charging`

`battery_dump_deadline_hour` is configurable in the integration options flow and is not currently exposed as a dashboard entity.

## Main Entities Used By Current YAML

These are the primary entity IDs referenced by the shipped dashboard YAML:

- `sensor.electricity_planner_current_electricity_price`
- `sensor.electricity_planner_diagnostics_monitoring_current_feed_in_price`
- `binary_sensor.electricity_planner_battery_charge_from_grid`
- `binary_sensor.electricity_planner_car_charge_from_grid`
- `binary_sensor.electricity_planner_data_nord_pool_available`
- `sensor.electricity_planner_battery_soc_average`
- `sensor.electricity_planner_grid_setpoint`
- `sensor.electricity_planner_decision_diagnostics`
- `sensor.electricity_planner_diagnostics_monitoring_nord_pool_prices`
- `number.electricity_planner_battery_dump_target_soc`
- `number.electricity_planner_max_soc_threshold`
- `number.electricity_planner_max_soc_threshold_sunny`
- `number.electricity_planner_sunny_forecast_threshold_kwh`
- `switch.electricity_planner_battery_dump_to_grid`
- `switch.electricity_planner_car_permissive_mode`
- `switch.electricity_planner_disable_battery_charging`

Managed dashboards also include richer diagnostic cards for:

- `binary_sensor.electricity_planner_solar_feed_in_grid`
- `binary_sensor.electricity_planner_solar_derating_alarm`
- `sensor.electricity_planner_inverter_derating_target`
- `sensor.electricity_planner_diagnostics_monitoring_entity_status`

## Single-Phase Vs Three-Phase

Both managed topologies now share the same pricing, threshold, forecast, and manual-override surface.

Three-phase managed and bundled dashboards additionally include:

- detailed L1/L2/L3 status with per-phase template rows
- per-phase power/component summaries
- a power-distribution chart for grid setpoints across phases

## Manual Dashboard Setup

If you want to create your own dashboard manually:

1. Install the required frontend cards.
2. Start from `custom_components/electricity_planner/dashboard_template.yaml` for the shared layout.
3. If you need a manual three-phase dashboard, append or reference the phase-specific blocks from `electricity_planner_3phase_dashboard.yaml`.
4. Replace any entity IDs if your installation uses custom entity names.
5. For multiple planner instances, ensure manual override buttons send the correct `entry_id`.

## Notes

- The integration-generated managed dashboard is the reference layout for both single-phase and three-phase installs.
- The bundled YAML files are examples shipped with the release and may be customized independently after import.
