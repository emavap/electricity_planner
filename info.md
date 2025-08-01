# Electricity Planner Integration

This integration provides intelligent electricity usage planning **decisions** for Home Assistant, specifically designed for Belgian electricity markets. It analyzes battery status, solar forecasts, and electricity prices to provide **boolean recommendations** for when to charge from the grid. **The integration does not control hardware directly** - it provides decision outputs for external charging control systems.

## Key Features

### 🔋 Multi-Battery Support
- **Any battery system** with SOC and capacity sensors
- **Brand independent**: Works with any Home Assistant battery integration
- **Multiple batteries**: Monitor several battery systems simultaneously

### ⚡ Smart Grid Charging Decisions
- **Price-Threshold Based**: Only recommends grid charging when electricity rates are below threshold
- **Solar Integration**: Prefers solar over grid power when available
- **No Emergency Overrides**: External systems handle emergency situations
- **Rate-Focused Logic**: Returns False when rates are too high, letting Home Assistant know it's not convenient

### 🚗 Car Grid Charging Recommendations
- **Price-only logic**: Simple low-price based recommendations
- **No time restrictions**: Charges anytime when price is favorable
- **No solar consideration**: Solar surplus typically insufficient for car charging

### 🌞 Solar Optimization
- **Production Monitoring**: Tracks current solar panel output
- **Forecast Integration**: Uses weather-based solar forecasts
- **Grid Feed-in Optimization**: Prevents unnecessary charging when solar surplus is expected

## Configuration

### Required Entities

#### Nord Pool Price Entities (Required)
1. **Current Price Entity**: Current electricity price (€/kWh)
2. **Highest Price Entity**: Highest price today (€/kWh) 
3. **Lowest Price Entity**: Lowest price today (€/kWh)
4. **Next Price Entity**: Next hour price (€/kWh)

#### Battery Entities (Required)
5. **Battery SOC Entities**: One or more battery state-of-charge sensors (%)
6. **Battery Capacity Entities**: Battery capacity sensors (kWh)

#### Power Flow Entities (Required)  
7. **House Consumption Entity**: Current house power consumption (W)
8. **Solar Surplus Entity**: Current solar surplus = production - consumption (W)

### Optional Entities
- **Car Charging Power Entity**: Current car charging power (W)

### Settings
- **Min SOC Threshold**: Minimum battery level to maintain (default: 20%)
- **Max SOC Threshold**: Target maximum battery level (default: 90%)
- **Price Threshold**: Price below which charging is considered economical (default: 0.15 €/kWh)
- **Solar Forecast Hours**: Hours ahead to consider for solar planning (default: 12)
- **Car Charging Hours**: Preferred car charging duration (default: 8)

## Created Entities

### Sensors
- **Grid Charging Decision**: Overall grid charging recommendation status
- **Battery Analysis**: Battery status with SOC, capacity, and remaining capacity data
- **Price Analysis**: Comprehensive Nord Pool price analysis with positioning and trends
- **Power Analysis**: House consumption, solar surplus, and car charging power flow

### Binary Sensors (Key Outputs)
- **Battery Grid Charging**: ✅ **True when batteries should be charged from grid** (only when price favorable)
- **Car Grid Charging**: ✅ **True when car should be charged from grid** (only when price favorable)
- **Low Electricity Price**: True when price is below threshold
- **Solar Production Active**: True when solar panels are producing

## Decision Algorithm

The integration uses a sophisticated multi-factor decision algorithm:

### Battery Charging Priority
1. **Emergency Charging**: Immediate charging if any battery below minimum SOC
2. **Price vs Solar**: Compares current price with solar forecast
3. **Capacity Planning**: Considers remaining capacity and expected solar production
4. **Grid Optimization**: Minimizes grid usage during high solar production

### Car Charging Logic
1. **Battery Priority**: Ensures home batteries are adequately charged first
2. **Night Charging**: Prefers charging during night hours with low prices
3. **Solar Utilization**: Charges during solar surplus periods
4. **Price Optimization**: Only charges when prices are below threshold

## Automation Examples

### Battery Emergency Charging
```yaml
automation:
  - alias: "Emergency Battery Charging"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_needs_charging
        to: "on"
    action:
      - service: notify.mobile_app
        data:
          message: "Emergency battery charging activated - SOC below minimum threshold"
```

### Car Charging on Low Prices
```yaml
automation:
  - alias: "Car Charging Low Price"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_car_charging_recommended
        to: "on"
    condition:
      - condition: state
        entity_id: switch.electricity_planner_auto_charging_mode
        state: "on"
    action:
      - service: switch.turn_on
        entity_id: switch.electricity_planner_car_charger_control
```

## Belgium Specific Considerations

This integration is optimized for the Belgian electricity market:
- **Dynamic Pricing**: Works with variable electricity pricing
- **Solar Feed-in**: Considers Belgian solar feed-in tariffs
- **Grid Optimization**: Reduces peak consumption during high-price periods
- **Battery Regulations**: Complies with Belgian residential battery storage guidelines

## Compatibility

### Electricity Price Sources
- Nord Pool integration
- Entsoe-e integration  
- Any sensor providing price in €/kWh

### Battery Systems
- Huawei Luna (via Huawei Solar integration)
- Victron (via Victron integration)
- Any battery system providing SOC sensor

### Solar Systems
- Huawei Solar inverters
- SolarEdge systems
- Any solar system providing production sensors

### Car Chargers
- Huawei car chargers
- Any Home Assistant compatible car charger switch

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/emavap/electricity_planner).

## Version History

### v1.0.0
- Initial release
- Multi-battery support (Huawei Luna, Victron)
- Solar forecast integration
- Car charging control
- Belgian market optimization
- GUI-based entity configuration