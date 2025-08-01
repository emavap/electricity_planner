# Electricity Planner Integration

This integration provides intelligent electricity usage planning **decisions** for Home Assistant, specifically designed for Belgian electricity markets. It analyzes battery status, solar forecasts, and electricity prices to provide **boolean recommendations** for when to charge from the grid. **The integration does not control hardware directly** - it provides decision outputs for external charging control systems.

## Key Features

### 🔋 Multi-Battery Support
- **Huawei Luna**: Full support for SOC monitoring and charging control
- **Victron**: Compatible with Victron battery systems
- **Flexible Configuration**: Add any battery entity via the GUI setup

### ⚡ Smart Grid Charging Decisions
- **Price-Based Analysis**: Uses electricity price data to determine optimal grid charging times
- **Solar Integration**: Considers solar production and forecasts to minimize unnecessary grid usage
- **Emergency Logic**: Provides emergency charging recommendations when batteries are critical
- **Multi-Factor Algorithm**: Combines price, solar forecast, and battery status

### 🚗 Car Grid Charging Recommendations
- **Boolean Outputs**: Provides true/false recommendations for car grid charging
- **Intelligent Scheduling**: Recommends charging during low-price periods when solar isn't available
- **Battery Priority**: Ensures home batteries are maintained before recommending car charging

### 🌞 Solar Optimization
- **Production Monitoring**: Tracks current solar panel output
- **Forecast Integration**: Uses weather-based solar forecasts
- **Grid Feed-in Optimization**: Prevents unnecessary charging when solar surplus is expected

## Configuration

### Required Entities
1. **Electricity Price Entity**: Sensor providing current electricity price (€/kWh)
2. **Battery SOC Entities**: One or more sensors providing battery state of charge (%)

### Optional Entities
- **Battery Capacity Entities**: Sensors providing battery capacity information
- **Solar Forecast Entity**: Sensor providing solar production forecast
- **Solar Production Entity**: Sensor providing current solar production (kW)
- **Grid Power Entity**: Sensor providing grid power usage

### Settings
- **Min SOC Threshold**: Minimum battery level to maintain (default: 20%)
- **Max SOC Threshold**: Target maximum battery level (default: 90%)
- **Price Threshold**: Price below which charging is considered economical (default: 0.15 €/kWh)
- **Solar Forecast Hours**: Hours ahead to consider for solar planning (default: 12)
- **Car Charging Hours**: Preferred car charging duration (default: 8)

## Created Entities

### Sensors
- **Grid Charging Decision**: Overall grid charging recommendation status
- **Battery Analysis**: Battery status with average SOC and individual battery data
- **Price Analysis**: Current price analysis and recommendations
- **Solar Analysis**: Solar production and forecast information

### Binary Sensors (Key Outputs)
- **Battery Grid Charging**: ✅ **True when batteries should be charged from grid**
- **Car Grid Charging**: ✅ **True when car should be charged from grid**
- **Low Electricity Price**: True when price is below threshold
- **Solar Production Active**: True when solar panels are producing
- **Battery Needs Charging**: True when battery is below minimum threshold

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