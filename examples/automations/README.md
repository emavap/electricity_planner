# Example Automations for Electricity Planner

This folder contains example Home Assistant automations that integrate with the Electricity Planner integration.

## Available Automations

### 1. Car Charger Dynamic Control (`car_charger_dynamic_control.yaml`)

**Purpose**: Automatically controls EV charger based on Electricity Planner decisions with price-based cutoff.

**Features**:
- Turns charger on/off based on grid charging recommendations
- Adjusts charging power dynamically based on planner limits
- Implements price threshold override for battery protection
- Handles charger initialization and status monitoring
- Supports solar surplus charging

**Required Entities**:
- `binary_sensor.electricity_planner_car_charge_from_grid` - Charging decision
- `sensor.electricity_planner_car_charger_limit` - Power limit
- `sensor.electricity_planner_current_electricity_price` - Current price
- `input_number.car_charger_price_threshold` - User-defined price cutoff
- `sensor.huawei_charger_plugged_in` - Car connection status
- `switch.evcharger` - Charger on/off control
- `number.huawei_charger_dynamic_power_limit` - Power limit control

**Triggers**:
- State changes of planner entities (with 2-minute debounce)
- Car plugged in status changes
- Charger turned on
- Every 5 minutes

---

### 2. Solar Feed-in Control (`solar_feedin_control.yaml`)

**Purpose**: Controls solar inverter power limit to prevent grid feed-in when not allowed by the planner.

**Features**:
- Automatically derates inverter when feeding to grid is not profitable
- Gradually increases power limit when feed-in is allowed
- Adjusts based on actual grid power flow
- Protects against excessive grid export

**Required Entities**:
- `binary_sensor.electricity_planner_solar_feed_in_grid` - Feed-in decision
- `sensor.grid` - Grid power (positive = importing, negative = exporting)
- `number.huawei_inverter_power_limit` - Inverter power limit control

**Triggers**:
- State changes of planner or grid sensor
- Every 30 seconds

**Logic**:
- When feed-in NOT allowed and exporting > 50W: Reduce limit by 120% of export power
- When feed-in allowed or importing > 50W: Increase limit by 200W (max 4400W)

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

## Installation

1. Copy the desired automation YAML file(s) to your Home Assistant configuration
2. Adjust entity IDs to match your specific setup:
   - Replace `huawei_charger_*` with your charger entities
   - Replace `victron_*` with your Victron entities
   - Replace `sensor.grid` with your grid power sensor
3. Create any required `input_number` helpers (e.g., `car_charger_price_threshold`)
4. Reload automations in Home Assistant

## Entity ID Reference

### Automation Control Entities (Essential)
These are the primary entities your automations should monitor:
- `binary_sensor.electricity_planner_battery_charge_from_grid`
- `binary_sensor.electricity_planner_car_charge_from_grid`
- `binary_sensor.electricity_planner_solar_feed_in_grid` (not yet created by code)
- `sensor.electricity_planner_car_charger_limit`
- `sensor.electricity_planner_grid_setpoint`

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
- Adjust `price_threshold` via the input_number helper to change when battery protection kicks in
- Modify `difference_w > 500` to change power adjustment sensitivity
- Change time patterns to suit your charging schedule

### Solar Feed-in Automation
- Adjust `grid_power > 50` threshold to change sensitivity
- Modify `reduction = (grid_power * 1.2)` multiplier for faster/slower response
- Change `increase = 200` to adjust ramp-up speed
- Adjust max limit `4400` to match your inverter capacity

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

These examples are for **Electricity Planner v2.5.0+**.

**Note on Entity Naming**: Most entities use simple names like `sensor.electricity_planner_current_electricity_price`. Some newer threshold/margin sensors use longer names with `diagnostics_monitoring_` prefix (e.g., `sensor.electricity_planner_diagnostics_monitoring_price_threshold`). Check Developer Tools → States to see the exact entity IDs in your installation.
