# Example Automations for Electricity Planner

This folder contains example Home Assistant automations that integrate with the Electricity Planner integration.

## Available Automations

### 1. Car Charger Dynamic Control (`car_charger_dynamic_control.yaml`)

**Purpose**: Automatically controls EV charging based on Electricity Planner decisions, with startup grace handling and a fail-safe cutoff if charger state stops reporting.

**Features**:
- Turns the charger on only when the planner allows charging
- Turns the charger off when charging is no longer allowed
- Turns the charger off when no car is connected
- Adjusts charging power dynamically based on planner limits
- Waits through charger initialization before enforcing sensor-dependent logic
- Cuts power if the charger stops reporting state after the grace period while charging is not allowed
- Logs a warning if the charger still does not report state while charging is allowed

**Required Entities**:
- `binary_sensor.electricity_planner_car_charge_from_grid` - Charging decision
- `sensor.electricity_planner_car_charger_limit` - Planner power limit
- `sensor.huawei_charger_plugged_in` - Car connection and charging status
- `switch.evcharger` - Charger on/off control
- `number.huawei_charger_dynamic_power_limit` - Dynamic power limit control

**Triggers**:
- State changes of planner entities (with 2-minute debounce)
- Car plugged in status changes
- Charger turned on
- Every 5 minutes

---

### 2. Solar Feed-in Control (`solar_feedin_control.yaml`)

**Purpose**: Applies a recommended inverter derating target computed directly by Electricity Planner.

**Features**:
- Uses `sensor.electricity_planner_inverter_derating_target` as the single recommended setpoint
- Keeps the Home Assistant automation simple: read planner target, write target to the inverter number
- Lets the planner own the feed-in policy and the derating logic
- Tightens the inverter limit immediately when the planner lowers the target
- Reopens the inverter only on the periodic pass so small PV fluctuations do not trigger constant writes
- Re-applies the target periodically in case the inverter misses or loses the last write

**Required Entities / Helpers**:
- `sensor.electricity_planner_inverter_derating_target` - Planner-computed inverter target
- `binary_sensor.electricity_planner_solar_feed_in_grid` - Planner feed-in policy used to restore full power immediately when export becomes allowed
- `number.inverter_power_derating` - Writable inverter power-limit entity

**Triggers**:
- State changes of the planner target sensor
- State changes of the planner feed-in policy binary sensor
- Periodic reevaluation every 5 minutes

**Logic**:
- Read the planner target sensor
- If feed-in becomes allowed, restore the planner target immediately
- If feed-in is blocked and the planner lowers the target, apply the reduction immediately once it differs by at least `100 W`
- If feed-in is blocked and the planner raises the target, only apply that reopening step on the periodic pass once it differs by at least `150 W`

---

### 3. Victron Grid Setpoint (`victron_grid_setpoint.yaml`)

**Purpose**: Updates Victron ESS grid setpoint based on Electricity Planner recommendations.

**Features**:
- Automatically syncs grid setpoint to Victron system
- Handles unavailable/unknown states gracefully
- Regular updates every 5 minutes
- Immediate updates on planner changes

**Required Entities**:
- `sensor.electricity_planner_grid_setpoint` - Target grid power
- `number.victron_victron_system_ess_acpowersetpoint` - Victron ESS setpoint control

**Triggers**:
- State changes of grid setpoint
- Every 5 minutes

---

### 4. Control Luna Battery Forcible Charge (`Control Luna Battery Forcible Charge.yaml`)

**Purpose**: Force-charges a Huawei Luna battery when `grid setpoint - house consumption` leaves enough headroom.

**Features**:
- Uses `grid setpoint - house consumption` as the excess-power calculation
- Skips runs when the required sensors are `unknown` or `unavailable`
- Starts forcible charge when excess power is above `1500W`
- Sets Luna charge power to half of the computed excess power
- Reissues forcible charge only when the target charge power changes
- Stops forcible charge when excess power falls to `1500W` or below and the battery is actively charging

**Required Entities / Helpers**:
- `sensor.electricity_planner_grid_setpoint` – Planner import headroom recommendation
- `sensor.house_power_consumption_estimated` – Current house load estimate
- `sensor.batteries_forcible_charge` – Huawei forcible-charge status entity with `mode` and `charge_power` attributes
- Huawei Luna device ID for `huawei_solar.forcible_charge`

**Triggers**:
- Grid setpoint changes
- Periodic reevaluation (every 5 minutes)

---

### 5. Recover Nord Pool When Price Sensor Is Unavailable (`Recover Nord Pool when price sensor is unavailable.yaml`)

**Purpose**: Automatically resets the Nord Pool integration when the price sensor goes `unavailable` for an extended period.

**Features**:
- Watches the configured Nord Pool current-price sensor for both `unavailable` and `unknown`
- Issues `homeassistant.reload_config_entry` for the Nord Pool config entry
- Avoids hardcoded automation IDs, so copying the example does not create duplicate-ID conflicts by itself

**Required Entities / Helpers**:
- `sensor.nord_pool_be_current_price` – Your Nord Pool current-price sensor
- Nord Pool config entry ID (configured in the automation)

**Triggers**:
- Price sensor transitions to `unavailable` for >1 minute
- Price sensor transitions to `unknown` for >1 minute

---

## Installation

1. Copy the desired automation YAML file(s) to your Home Assistant configuration
2. Adjust entity IDs to match your specific setup:
   - Replace `huawei_charger_*` with your charger entities
   - Replace `victron_*` with your Victron entities
   - Replace `sensor.grid` with your grid power sensor
   - Replace `number.inverter_power_derating` with your inverter limit entity if your Huawei setup exposes a different writable number
   - Replace example `device_id` values with the ones from your HA instance
   - Verify that every writable `number.*` entity in the examples actually exists before enabling the automation
3. Ensure the charger connection-status sensor reports valid numeric states for disconnected/connected/charging
4. Reload automations in Home Assistant

## Entity ID Reference

### Automation Control Entities (Essential)
These are the primary entities your automations should monitor:
- `binary_sensor.electricity_planner_battery_charge_from_grid`
- `binary_sensor.electricity_planner_car_charge_from_grid`
- `binary_sensor.electricity_planner_solar_feed_in_grid`
- `binary_sensor.electricity_planner_solar_derating_alarm`
- `sensor.electricity_planner_car_charger_limit`
- `sensor.electricity_planner_grid_setpoint`
- `sensor.electricity_planner_inverter_derating_target`

### Diagnostic/Monitoring Entities (Optional)
These provide additional data for advanced logic:
- `sensor.electricity_planner_current_electricity_price`
- `sensor.electricity_planner_battery_soc_average`
- `sensor.electricity_planner_solar_surplus_power`
- `sensor.electricity_planner_decision_diagnostics`
- `binary_sensor.electricity_planner_price_below_threshold`
- `binary_sensor.electricity_planner_solar_producing_power`
- `binary_sensor.electricity_planner_data_nord_pool_available`
- And many more with `diagnostics_monitoring_` prefix (see dashboard for complete list)

## Customization Tips

### Car Charger Automation
- Adjust `init_grace_seconds` to change how long the automation waits for charger initialization
- Adjust `sensor_timeout_seconds` to change when the fail-safe power cutoff activates
- Modify `difference_w > 500` to change power adjustment sensitivity
- Change the time pattern to suit your polling cadence

### Solar Feed-in Automation
- Configure `grid_power_entity` in the planner using the planner sign convention: positive = import, negative = export
- Configure `solar_production_entity` in the planner so it can track the inverter's current solar output
- Configure battery SOC entities if you want the planner to stop derating while the battery is below a chosen fill level
- Set `max_inverter_power` to your inverter's actual limit
- Set `inverter_export_limit` to the export target you want while feed-in is blocked, for example `80 W`
- Set `inverter_export_deadband` to the allowed band around that target, for example `40 W`
- Set `inverter_derating_unused_release_minutes` to how long PV may stay below the current derating cap before the planner releases back to max power. Default: `5` minutes
- Set `inverter_derating_soc_bypass_threshold` to the SOC below which the planner should stop derating and let PV charge the battery
- Keep the apply automation simple and let the planner own the derating logic

### Solar Feed-in Automation Walkthrough
Electricity Planner can now expose a direct inverter target sensor, similar to `grid_setpoint`.

The integration computes `sensor.electricity_planner_inverter_derating_target` from:
- the feed-in decision
- current solar production
- current grid power
- optional battery SOC
- configured inverter max power, export target, and SOC bypass threshold

The planner logic is:
- If feed-in is allowed, recommend full inverter power
- If feed-in is blocked and average battery SOC is below the configured bypass threshold, recommend full inverter power so solar can charge the battery
- If battery SOC is still below that threshold but export rises too high anyway, derate anyway and raise `binary_sensor.electricity_planner_solar_derating_alarm`
- Otherwise, use a simple export deadband around the configured target:
  - if export is below roughly `target - deadband`, reopen gradually instead of jumping straight to max power
  - if export is above roughly `target + deadband`, reduce from the current solar output toward the target
  - if export is inside that band, hold the previous derating target steady
- With the default `80 W` target and `40 W` deadband, that means:
  - below `40 W` export: reopen the inverter in small steps
  - between `40 W` and `120 W`: hold the current target
  - above `120 W`: derate further
- If grid telemetry is missing, fall back to a conservative cap based on house consumption plus the configured export target

That design keeps vendor-specific write behavior outside the planner:
- the planner computes a recommended target
- a normal Home Assistant automation applies that target to your inverter entity, with its own write-threshold to avoid unnecessary EEPROM-style writes

This is the same design used by `sensor.electricity_planner_grid_setpoint`, and it is easier to adapt to non-Huawei hardware.

If your installation already supports Huawei's own built-in limited feed-in / grid-tied point control, prefer that feature over an external automation loop. This example is primarily for users who need Home Assistant to enforce the planner's feed-in decision through a writable derating entity.

### Victron Setpoint Automation
- Add additional conditions for manual override modes
- Implement rate limiting if your system is sensitive to frequent changes

## Troubleshooting

**Automation not triggering:**
- Check that all entity IDs exist and are correct (use Developer Tools → States)
- Verify the Electricity Planner integration is running
- Check automation traces in Home Assistant

**Entities show as unavailable:**
- Ensure Electricity Planner is properly configured
- Check that required sensors are selected in the integration config
- Restart Home Assistant if entities were just created

**Unexpected behavior:**
- Review automation traces to see which conditions matched
- Check logbook entries for detailed messages
- Verify your threshold values are appropriate for your electricity prices

## Support

For issues with:
- **These automations**: Check the examples and adjust for your hardware
- **Electricity Planner integration**: https://github.com/emavap/electricity_planner/issues
- **Home Assistant automations**: https://community.home-assistant.io

## Version

These examples are for **Electricity Planner v3.0.0+**.

**Note on Entity Naming**: Most entities use simple names like `sensor.electricity_planner_current_electricity_price`. Some newer threshold/margin sensors use longer names with `diagnostics_monitoring_` prefix (e.g., `sensor.electricity_planner_diagnostics_monitoring_price_threshold`). Check Developer Tools → States to see the exact entity IDs in your installation.
