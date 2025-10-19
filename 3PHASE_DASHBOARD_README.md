# Three-Phase Dashboard for Electricity Planner

This dashboard provides detailed visibility into per-phase power allocations and charging decisions for three-phase electrical systems.

## Features

### Per-Phase Information (L1, L2, L3)
Each phase displays:
- **Battery charging ON/OFF** - Whether batteries on this phase should charge from grid
- **Car charging ON/OFF** - Whether car chargers on this phase should charge from grid
- **Grid setpoint** - Total watts to import from grid on this phase
- **Grid components breakdown** - Battery allocation + Car allocation
- **Charger limit** - Maximum car charger power on this phase
- **Assigned batteries** - Number of batteries and capacity share on this phase
- **Reasons** - Detailed explanation for each charging decision

### Overall Dashboard Elements
- **Price gauges** - Current buy price with dynamic thresholds
- **Battery SOC** - Average state of charge across all batteries
- **Aggregated decisions** - Overall battery/car charging status
- **Power distribution chart** - Visual comparison of grid setpoints across phases

## Installation

### Required HACS Components
1. **Gauge Card Pro** – For price gauges  
   `https://github.com/benjamin-dcs/gauge-card-pro`

2. **ApexCharts Card** – For power distribution visualization  
   `https://github.com/RomRider/apexcharts-card`

3. **Template Entity Row** – For dynamic per-phase data display  
   `https://github.com/thomasloven/lovelace-template-entity-row`

### Setup Steps

1. Install required HACS components above
2. Copy `electricity_planner_3phase_dashboard.yaml` to your Home Assistant config
3. Add to your `configuration.yaml`:
   ```yaml
   lovelace:
     mode: yaml
     dashboards:
       electricity-planner-3phase:
         mode: yaml
         title: 3-Phase Planner
         icon: mdi:electric-switch
         show_in_sidebar: true
         filename: electricity_planner_3phase_dashboard.yaml
   ```
4. Restart Home Assistant
5. Navigate to the "3-Phase Planner" dashboard in the sidebar

## How It Works

### Phase Decision Logic
The integration computes an **overall charging decision** based on aggregated system totals (all phases combined), then distributes power allocations across the three phases:

1. **Battery power distribution** - Proportional to capacity
   - If Phase 1 has 10kWh and Phase 2 has 5kWh:
     - Phase 1 gets 66.7% of battery charging power
     - Phase 2 gets 33.3% of battery charging power

2. **Car power distribution** - Equal across phases with car sensors
   - If 11kW car charging and 2 phases have car sensors:
     - Each phase gets 5.5kW allocation

3. **Phase-specific ON/OFF**
   - Battery charging is ON for a phase only if:
     - Overall decision says charge batteries AND
     - Phase has assigned batteries
   - Car charging is ON for a phase only if:
     - Overall decision says charge car AND
     - Phase has car sensor configured

### Reading the Dashboard

#### Status Indicators
- **Green background** - Battery charging active on this phase
- **Orange background** - Car charging active on this phase
- **Grey background** - No charging on this phase

#### Power Values
```
Grid: 3000W          ← Total grid import on this phase
Battery: 2000W       ← Portion allocated for battery charging
Car: 1000W           ← Portion allocated for car charging
Limit: 5500W         ← Maximum car charger can draw
```

#### Capacity Share
Shows what percentage of total battery capacity is on each phase:
- `2 batteries (40%)` - 2 batteries assigned, representing 40% of total capacity
- Used for proportional power distribution

## Example Scenario

### System Configuration
- **Phase 1**: 10kWh battery + car charger
- **Phase 2**: 5kWh battery
- **Phase 3**: 5kWh battery + car charger

### When Battery Charging is ON (6kW total)
- **Phase 1**: 3kW battery allocation (50% of capacity)
- **Phase 2**: 1.5kW battery allocation (25% of capacity)
- **Phase 3**: 1.5kW battery allocation (25% of capacity)

### When Car Charging is ON (11kW total)
- **Phase 1**: 5.5kW car allocation (has car sensor)
- **Phase 2**: 0kW (no car sensor)
- **Phase 3**: 5.5kW car allocation (has car sensor)

### Total Grid Setpoints
- **Phase 1**: 8.5kW (3kW battery + 5.5kW car)
- **Phase 2**: 1.5kW (1.5kW battery + 0kW car)
- **Phase 3**: 7kW (1.5kW battery + 5.5kW car)

## Troubleshooting

### "Phase data unavailable"
- Check that Electricity Planner is configured in three-phase mode
- Verify that phase sensors are configured for all three phases
- Check that batteries are assigned to phases

### Template errors in entities card
- Ensure Home Assistant is at least version 2024.4.0
- Install `custom:template-entity-row` from HACS if not working
- Alternative: Use standard attribute display (less formatting)

### Chart not showing data
- Install ApexCharts Card from HACS
- Verify `phase_results` attribute exists on binary sensors:
  ```
  Developer Tools → States → binary_sensor.electricity_planner_battery_charge_from_grid
  Look for "phase_results" in attributes
  ```

### Missing card_mod styling
The colored backgrounds are optional enhancements. The dashboard works without them.
To enable:
1. Install `card-mod` from HACS: https://github.com/thomasloven/lovelace-card-mod
2. Restart Home Assistant

## Integration with Automations

Use the per-phase data in your automations:

```yaml
# Example: Turn on Phase 1 battery charger when allowed
automation:
  - alias: "Phase 1 Battery Charging"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_charge_from_grid
    condition:
      - condition: template
        value_template: >
          {% set phase_results = state_attr('binary_sensor.electricity_planner_battery_charge_from_grid', 'phase_results') %}
          {% set phase1 = phase_results.phase_1 if phase_results else None %}
          {{ phase1 and phase1.battery_grid_charging }}
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.phase1_battery_charger

# Example: Set Phase 1 grid setpoint
automation:
  - alias: "Phase 1 Grid Setpoint"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_charge_from_grid
    action:
      - service: number.set_value
        target:
          entity_id: number.phase1_grid_import_limit
        data:
          value: >
            {% set phase_results = state_attr('binary_sensor.electricity_planner_battery_charge_from_grid', 'phase_results') %}
            {% set phase1 = phase_results.phase_1 if phase_results else None %}
            {{ phase1.grid_setpoint if phase1 else 0 }}
```

## Support

For issues or questions:
- Check main dashboard for price/threshold issues
- Verify phase configuration in integration settings
- Review logs: Settings → System → Logs → Filter for "electricity_planner"
- See main README.md for general integration documentation
