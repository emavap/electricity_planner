# Electricity Planner for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/emavap/electricity_planner.svg)](https://github.com/emavap/electricity_planner/releases/)
[![License](https://img.shields.io/github/license/emavap/electricity_planner.svg)](LICENSE)

A Home Assistant integration that provides intelligent electricity usage planning, specifically designed for Belgian electricity markets with support for multiple battery systems and solar installations.

## ✨ Features

### 🔋 Multi-Battery Support
- **Huawei Luna** and **Victron** battery systems
- Multiple battery monitoring simultaneously
- Configurable SOC thresholds per setup

### ⚡ Smart Charging Decisions
- **Price-based charging** using real-time electricity prices
- **Solar-aware charging** that considers production forecasts
- **Emergency charging** when batteries drop below critical levels
- **Multi-factor algorithm** combining price, solar, and battery data

### 🚗 Car Charging Control
- **Huawei car charger** integration
- **Intelligent scheduling** during low-price or high-solar periods
- **Battery priority** ensures home batteries are maintained first

### 🌞 Solar Optimization
- **Real-time production monitoring**
- **Weather-based solar forecasting**
- **Grid feed-in optimization** to minimize unnecessary charging

## 🏗️ Architecture

### Core Components
1. **Entity-Based Configuration**: Select any compatible entities during setup
2. **Decision Engine**: Multi-factor algorithm for optimal charging decisions  
3. **Coordinator**: Real-time data coordination and state management
4. **Control Interface**: Switches and sensors for monitoring and control

### Decision Algorithm Flow
```
Price Analysis + Battery Status + Solar Forecast → Charging Decision
```

The algorithm considers:
- Current vs. forecasted electricity prices
- Individual battery SOC levels and capacity
- Solar production forecast and current output
- User-defined thresholds and preferences
- Grid feed-in compensation rates

## 📦 Installation

### Via HACS (Recommended)
1. Open HACS in Home Assistant
2. Go to "Integrations"
3. Click the three dots in the top right corner
4. Select "Custom repositories"
5. Add this repository URL: `https://github.com/emavap/electricity_planner`
6. Select category "Integration"
7. Click "Add"
8. Find "Electricity Planner" in the list and install
9. Restart Home Assistant
10. Go to Settings → Devices & Services → Add Integration
11. Search for "Electricity Planner" and add it

### Manual Installation
1. Download the latest release from [releases](https://github.com/emavap/electricity_planner/releases)
2. Extract the `electricity_planner` folder to your `custom_components` directory
3. Restart Home Assistant
4. Add the integration through the UI

## ⚙️ Configuration

### Required Entities
- **Electricity Price Entity**: Sensor providing current price (€/kWh)
- **Battery SOC Entities**: One or more battery state-of-charge sensors (%)

### Optional Entities  
- **Battery Capacity Entities**: Battery capacity information
- **Solar Forecast Entity**: Solar production forecast data
- **Solar Production Entity**: Current solar production (kW)
- **Car Charger Entity**: Switch for car charger control
- **Grid Power Entity**: Grid power consumption/production

### Settings
| Setting | Default | Description |
|---------|---------|-------------|
| Min SOC Threshold | 20% | Minimum battery level to maintain |
| Max SOC Threshold | 90% | Target maximum battery level |
| Price Threshold | 0.15 €/kWh | Price below which charging is economical |
| Solar Forecast Hours | 12 | Hours ahead to consider for solar planning |
| Car Charging Hours | 8 | Preferred car charging duration |

## 📊 Created Entities

### Sensors
- `sensor.electricity_planner_charging_decision` - Overall charging status
- `sensor.electricity_planner_battery_analysis` - Battery status and SOC data
- `sensor.electricity_planner_price_analysis` - Price analysis and thresholds
- `sensor.electricity_planner_solar_analysis` - Solar production and forecast

### Binary Sensors
- `binary_sensor.electricity_planner_battery_charging_recommended`
- `binary_sensor.electricity_planner_car_charging_recommended`
- `binary_sensor.electricity_planner_low_electricity_price`
- `binary_sensor.electricity_planner_solar_production_active`
- `binary_sensor.electricity_planner_battery_needs_charging`

### Switches
- `switch.electricity_planner_car_charger_control` - Manual car charger control
- `switch.electricity_planner_auto_charging_mode` - Enable/disable automatic mode

## 🔧 Automation Examples

### Emergency Battery Charging
```yaml
automation:
  - alias: "Emergency Battery Charging Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_needs_charging
        to: "on"
    action:
      - service: notify.mobile_app_your_device
        data:
          title: "Battery Alert"
          message: "Emergency charging activated - battery below {{ states('sensor.electricity_planner_battery_analysis') }}%"
```

### Automatic Car Charging
```yaml
automation:
  - alias: "Auto Car Charging"
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
      - service: notify.mobile_app_your_device
        data:
          message: "Car charging started - {{ state_attr('binary_sensor.electricity_planner_car_charging_recommended', 'reason') }}"
```

## 🇧🇪 Belgium Specific Features

This integration is optimized for the Belgian electricity market:
- **Dynamic Pricing**: Compatible with variable electricity pricing
- **Solar Feed-in**: Considers Belgian feed-in tariff structures
- **Peak Shaving**: Reduces consumption during high-price periods
- **Regulatory Compliance**: Follows Belgian residential battery storage guidelines

## 🔗 Compatibility

### Electricity Price Sources
- [Nord Pool](https://github.com/custom-components/nordpool) integration
- [Entsoe-e](https://github.com/JaccoR/hass-entso-e) integration
- Any sensor providing price in €/kWh

### Battery Systems
- Huawei Luna (via [Huawei Solar](https://github.com/wlcrs/huawei_solar) integration)
- Victron (via [Victron](https://github.com/Marty56/Home-Assistant-Victron) integration)
- Any battery system with SOC sensor

### Solar Systems
- Huawei Solar inverters
- SolarEdge systems
- Any system providing production sensors

### Car Chargers
- Huawei car chargers
- Any Home Assistant compatible charger switch

## 🐛 Troubleshooting

### Common Issues
1. **No charging decisions**: Verify electricity price entity is providing valid data
2. **Car charger not responding**: Check that the car charger entity is a switch type
3. **Solar data missing**: Ensure solar entities are correctly configured and providing data

### Debug Logging
Add this to your `configuration.yaml`:
```yaml
logger:
  default: warning
  logs:
    custom_components.electricity_planner: debug
```

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⭐ Support

If you find this integration useful, please consider starring the repository!

For issues and feature requests, please visit the [Issues](https://github.com/emavap/electricity_planner/issues) page.