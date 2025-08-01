# Electricity Planner for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/emavap/electricity_planner.svg)](https://github.com/emavap/electricity_planner/releases/)
[![License](https://img.shields.io/github/license/emavap/electricity_planner.svg)](LICENSE)

A Home Assistant integration that provides intelligent electricity usage planning decisions, specifically designed for Belgian electricity markets. The integration analyzes battery status, solar forecasts, and electricity prices to provide **boolean recommendations** for when to charge batteries and cars from the grid. **Power control is handled by external systems.**

## ✨ Features

### 🔋 Multi-Battery Support
- **Huawei Luna** and **Victron** battery systems
- Multiple battery monitoring simultaneously
- Configurable SOC thresholds per setup

### ⚡ Smart Grid Charging Decisions
- **Price-threshold based** - Only recommends grid charging when rates are below threshold
- **Solar-aware decisions** that prefer solar over grid power
- **Simple boolean outputs** - True only when grid charging is economically favorable
- **No emergency overrides** - External systems handle emergency situations
- **Rate-focused logic** - Returns False (0) when rates are too high

### 🚗 Car Grid Charging Recommendations
- **Intelligent scheduling** recommendations during low-price periods
- **Solar preference** - avoids grid charging when solar is available
- **Battery priority** ensures home batteries are maintained first
- **Time-based logic** for optimal night charging

### 🌞 Solar Optimization
- **Real-time production monitoring**
- **Weather-based solar forecasting**
- **Grid feed-in optimization** to minimize unnecessary charging

## 🏗️ Architecture

### Core Components
1. **Entity-Based Configuration**: Select any compatible entities during setup
2. **Decision Engine**: Multi-factor algorithm for optimal grid charging decisions  
3. **Coordinator**: Real-time data coordination and state management
4. **Boolean Outputs**: Binary sensors indicating when to charge from grid

### Decision Algorithm Flow
```
Price Analysis + Battery Status + Solar Forecast → Grid Charging Decisions
```

The algorithm provides **two key boolean outputs**:
- **`binary_sensor.battery_grid_charging`** - True when batteries should be charged from grid
- **`binary_sensor.car_grid_charging`** - True when car should be charged from grid

Decision factors:
- Current vs. forecasted electricity prices
- Individual battery SOC levels and capacity
- Solar production forecast and current output
- User-defined thresholds and preferences
- Time-of-day considerations

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
- `sensor.electricity_planner_grid_charging_decision` - Overall grid charging status
- `sensor.electricity_planner_battery_analysis` - Battery status and SOC data
- `sensor.electricity_planner_price_analysis` - Price analysis and thresholds
- `sensor.electricity_planner_solar_analysis` - Solar production and forecast

### Binary Sensors (Key Outputs)
- **`binary_sensor.electricity_planner_battery_grid_charging`** - ✅ **Charge batteries from grid** (True only when price favorable)
- **`binary_sensor.electricity_planner_car_grid_charging`** - ✅ **Charge car from grid** (True only when price favorable)
- `binary_sensor.electricity_planner_low_electricity_price` - Price below threshold
- `binary_sensor.electricity_planner_solar_production_active` - Solar currently producing

## 🔧 Integration with External Systems

The integration provides **boolean decision outputs** that can be used by other systems to control actual charging hardware.

### Battery Grid Charging Control
```yaml
automation:
  - alias: "Control Battery Charging from Grid"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_grid_charging
    action:
      - service: >
          {% if trigger.to_state.state == 'on' %}
            your_battery_system.start_grid_charging
          {% else %}
            your_battery_system.stop_grid_charging
          {% endif %}
        data:
          entity_id: your.battery_charger_entity
      - service: notify.mobile_app_your_device
        data:
          message: "Battery grid charging {{ 'started' if trigger.to_state.state == 'on' else 'stopped' }} - {{ state_attr('binary_sensor.electricity_planner_battery_grid_charging', 'reason') }}"
```

### Car Grid Charging Control
```yaml
automation:
  - alias: "Control Car Charging from Grid"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_car_grid_charging
    action:
      - service: >
          {% if trigger.to_state.state == 'on' %}
            switch.turn_on
          {% else %}
            switch.turn_off
          {% endif %}
        data:
          entity_id: switch.your_car_charger
      - service: notify.mobile_app_your_device
        data:
          message: "Car grid charging {{ 'started' if trigger.to_state.state == 'on' else 'stopped' }} - {{ state_attr('binary_sensor.electricity_planner_car_grid_charging', 'reason') }}"
```

### Price Alert Notification
```yaml
automation:
  - alias: "Low Price Alert"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_low_electricity_price
        to: "on"
    action:
      - service: notify.mobile_app_your_device
        data:
          title: "Low Electricity Price"
          message: "Electricity price is now {{ states('sensor.electricity_planner_price_analysis') }}€/kWh - good time to charge!"
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

### External Charging Control Systems
- Any battery management system with HA integration
- Car chargers with Home Assistant switch entities
- Custom charging controllers via automations

## 🐛 Troubleshooting

### Common Issues
1. **Binary sensors always False**: Check that electricity price is below your configured threshold
2. **No price data**: Verify electricity price entity is providing valid numeric data
3. **Solar data missing**: Ensure solar entities are correctly configured and providing numeric data
4. **Unexpected recommendations**: Verify price threshold setting - integration only recommends grid charging when price is below threshold

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