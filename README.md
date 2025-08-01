# Electricity Planner for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/emavap/electricity_planner.svg)](https://github.com/emavap/electricity_planner/releases/)
[![License](https://img.shields.io/github/license/emavap/electricity_planner.svg)](LICENSE)

A Home Assistant integration that provides intelligent electricity usage planning decisions, specifically designed for Belgian electricity markets. The integration analyzes battery status, solar forecasts, and electricity prices to provide **boolean recommendations** for when to charge batteries and cars from the grid. **Power control is handled by external systems.**

## ✨ Features

### 🔋 Multi-Battery Support
- **Any battery system** with SOC and capacity sensors
- Multiple battery monitoring simultaneously
- Brand-independent - works with any Home Assistant battery integration

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

The algorithm uses **comprehensive Nord Pool data** and provides **two key boolean outputs**:
- **`binary_sensor.battery_grid_charging`** - True when batteries should be charged from grid
- **`binary_sensor.car_grid_charging`** - True when car should be charged from grid

Decision factors:
- **Nord Pool pricing**: Current vs. highest/lowest prices, next hour trends
- **Price positioning**: Where current price sits in daily range (0-100%)
- **Battery status**: SOC levels, total capacity, remaining capacity needs
- **Power flow**: House consumption, solar surplus, car charging power
- **Smart logic**: Very low prices (bottom 30%), price trend improvements
- **Solar preference**: Uses solar surplus instead of grid when available

## 🔄 Decision Sequences & Logic

### Battery Grid Charging Decision Sequence

The integration evaluates battery grid charging in this specific order:

1. **🔋 Battery Status Check**
   ```
   ❌ No batteries configured → FALSE
   ❌ Batteries full (>90% SOC) → FALSE
   ✅ Continue to price analysis
   ```

2. **💰 Price Threshold Check**
   ```
   ❌ Price above threshold → FALSE ("Price too high")
   ✅ Price below threshold → Continue
   ```

3. **☀️ Solar Surplus Check**
   ```
   ❌ Significant solar surplus (>1kW) → FALSE ("Use solar instead")
   ✅ No/low solar surplus → Continue
   ```

4. **📊 Smart Price Analysis**
   ```
   ✅ Very low price (bottom 30% of daily range) → TRUE
   ❌ Batteries >30% SOC and price not very low → FALSE ("No need to charge")
   ❌ Otherwise → FALSE ("Price OK but not optimal")
   ```

**Important**: Battery charging only occurs if batteries are below 30% combined SOC OR it's a very low price day (bottom 30% of daily range).

### Car Grid Charging Decision Sequence

The integration evaluates car grid charging in this specific order:

1. **💰 Price Threshold Check**
   ```
   ❌ Price above threshold → FALSE ("Price too high")
   ✅ Price below threshold → Continue
   ```

2. **📊 Price-Only Analysis**
   ```
   ✅ Very low price (bottom 30% daily range) → TRUE
   ❌ Price improving next hour → FALSE ("Wait for better price")
   ✅ Any low price (below threshold) → TRUE
   ❌ Otherwise → FALSE ("Price not favorable")
   ```

**Note**: Car charging is purely price-based. Solar surplus is not considered as it's typically insufficient for car charging needs.

## 📈 Price Analysis Logic

### Price Positioning Calculation
```python
price_position = (current_price - lowest_price) / (highest_price - lowest_price)
# Result: 0.0 = lowest price of day, 1.0 = highest price of day
```

### Classification System
- **Very Low Price**: Position < 0.3 (bottom 30% of daily range)
- **Low Price**: Below user threshold (default 0.15 €/kWh)
- **Price Improving**: Next hour price < current price
- **Price Worsening**: Next hour price > current price

### Decision Priority

#### Battery Charging:
1. **🚫 Hard Stop**: Price above threshold → Always FALSE
2. **🌞 Solar First**: Use available solar surplus before grid
3. **💎 Very Low Prices**: Bottom 30% of daily range → TRUE
4. **🔋 SOC Check**: Batteries >30% + not very low price → FALSE
5. **📊 Position-based**: Reject if price position not optimal

#### Car Charging:
1. **🚫 Hard Stop**: Price above threshold → Always FALSE
2. **💎 Very Low Prices**: Bottom 30% of daily range → TRUE
3. **⏰ Wait for Better**: Price improving next hour → FALSE (wait)
4. **💰 Low Price**: Any price below threshold → TRUE

## 🔄 Real-time Updates

The integration updates every **5 minutes** and immediately when any tracked entity changes:

- **Nord Pool prices** (current, highest, lowest, next)
- **Battery SOC and capacity** values
- **House consumption** changes
- **Solar surplus** changes
- **Car charging power** changes

Each update triggers a complete re-evaluation of both battery and car charging recommendations.

## 📋 Example Scenarios

### Scenario 1: Very Low Price Period
```
Current Price: 0.05 €/kWh (lowest of day: 0.05, highest: 0.25)
Price Position: 0% (bottom of daily range)
Solar Surplus: 500W
Battery SOC: 60%
Time: 14:00

Decision:
✅ Battery Grid Charging: TRUE ("Very low price - bottom 30% of daily range")
✅ Car Grid Charging: TRUE ("Very low price - bottom 30% of daily range")
```

### Scenario 2: Solar Surplus Available (Battery Only)
```
Current Price: 0.12 €/kWh (threshold: 0.15)
Solar Surplus: 3000W
Battery SOC: 70%
Time: 12:00

Decision:
❌ Battery Grid Charging: FALSE ("Solar surplus available - use solar instead")
✅ Car Grid Charging: TRUE ("Low price - below threshold")
```

### Scenario 3: Price Improving Next Hour
```
Current Price: 0.14 €/kWh (threshold: 0.15)
Next Hour Price: 0.10 €/kWh
Price Position: 57% (middle of daily range, assuming low: 0.05, high: 0.25)
Battery SOC: 65% (above 30% threshold)
Solar Surplus: 200W
Time: 15:00

Decision:
❌ Battery Grid Charging: FALSE ("Price OK but not optimal - current: 0.140€/kWh, position: 57%")
❌ Car Grid Charging: FALSE ("Price improving next hour (0.100€/kWh) - wait for better price")
```

### Scenario 4: High Price Period
```
Current Price: 0.22 €/kWh (threshold: 0.15)
Price Position: 85% (top of daily range)
Battery SOC: 40%
Time: 18:00

Decision:
❌ Battery Grid Charging: FALSE ("Price too high - 0.220€/kWh > threshold 0.150€/kWh")
❌ Car Grid Charging: FALSE ("Price too high - 0.220€/kWh > threshold 0.150€/kWh")
```

### Scenario 5: Low Battery Level
```
Current Price: 0.13 €/kWh (threshold: 0.15)
Price Position: 40% (middle range)
Solar Surplus: 0W
Battery SOC: 25% (below 30% threshold)

Decision:
❌ Battery Grid Charging: FALSE ("Price OK but not optimal - position: 40%")
✅ Car Grid Charging: TRUE ("Low price - below threshold")
```

### Scenario 6: Standard Low Price with Good Batteries
```
Current Price: 0.13 €/kWh (threshold: 0.15)
Price Position: 40% (middle range)
Solar Surplus: 0W
Battery SOC: 80% (above 30% threshold)

Decision:
❌ Battery Grid Charging: FALSE ("Batteries above 30% and price not very low")
✅ Car Grid Charging: TRUE ("Low price - below threshold")
```

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

#### Nord Pool Price Entities
- **Current Price Entity**: Current electricity price (€/kWh) 
- **Highest Price Entity**: Highest price today (€/kWh)
- **Lowest Price Entity**: Lowest price today (€/kWh)
- **Next Price Entity**: Next hour price (€/kWh)

#### Battery Entities
- **Battery SOC Entities**: One or more battery state-of-charge sensors (%)
- **Battery Capacity Entities**: Battery capacity sensors (kWh)

#### Power Flow Entities
- **House Consumption Entity**: Current house power consumption (W)
- **Solar Surplus Entity**: Current solar surplus = production - consumption (W)

### Optional Entities  
- **Car Charging Power Entity**: Current car charging power (W)

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
- `sensor.electricity_planner_battery_analysis` - Battery status, SOC and capacity data
- `sensor.electricity_planner_price_analysis` - Comprehensive Nord Pool price analysis
- `sensor.electricity_planner_power_analysis` - House consumption, solar surplus, car charging power

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
- **[Nord Pool](https://github.com/custom-components/nordpool) integration** (recommended)
  - Provides current, highest, lowest, and next hour prices
  - Essential for comprehensive price analysis and positioning
- Any integration providing the 4 required price entities in €/kWh

### Battery Systems
- **Any battery system** providing SOC (%) and capacity (kWh) sensors
- Examples: Huawei Luna, Victron, Tesla Powerwall, LG Chem, BYD, etc.
- Works with any Home Assistant battery integration

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
1. **Binary sensors always False**: Check that electricity price is below your configured threshold and verify all required Nord Pool entities are configured
2. **Missing Nord Pool data**: Ensure all 4 price entities (current, highest, lowest, next) are available and providing numeric data
3. **Power flow issues**: Verify house consumption and solar surplus entities are providing valid data in Watts
4. **Battery analysis errors**: Check that battery SOC and capacity entities are configured and providing numeric data
5. **Unexpected recommendations**: Integration uses comprehensive price positioning - check price analysis sensor for detailed reasoning

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

## 🎨 Lovelace Dashboard Card

A beautiful, comprehensive dashboard card is provided to visualize the decision making and current situation.

### Card Features

- **🎯 Main Decision Display**: Shows current charging recommendations with color-coded status
- **💰 Price Analysis**: Current price with daily positioning (0-100%)
- **📊 Daily Price Range**: Visual bar showing where current price sits in daily range
- **🔋 Battery Status**: SOC levels, battery count, min/max values
- **⚡ Power Flow**: House consumption, solar surplus, car charging power
- **📈 Price Trends**: Next hour price and trend direction
- **✅ Decision Details**: Detailed reasoning for each recommendation

### Prerequisites

Install these custom cards via HACS:
- [Mushroom Cards](https://github.com/piitaya/lovelace-mushroom)
- [Bar Card](https://github.com/custom-cards/bar-card)

### Installation

1. **Install Prerequisites**:
   - Install "Mushroom" via HACS Frontend
   - Install "Bar Card" via HACS Frontend

2. **Add the Card**:
   - Copy the contents of [`lovelace-card.yaml`](lovelace-card.yaml)
   - In Home Assistant, go to your dashboard
   - Click "Edit Dashboard" → "Add Card" → "Manual"
   - Paste the YAML content

3. **Customize** (optional):
   - Adjust colors in the `severity` sections
   - Modify price thresholds for color coding
   - Change card arrangement as needed

### Card Preview

The card displays:

```
┌─────────────────────────────────────┐
│ ⚡ Electricity Planner              │
│ 🔋⚡ Charge Both from Grid          │
├─────────────────────────────────────┤
│ 💰 0.087€/kWh    📈 Improving      │
│ Position: 15% of daily range        │
├─────────────────────────────────────┤
│ ████░░░░░░░░░░░░░░░░░ 15%           │
│ Low: 0.05€ | Current: 0.087€ | High: 0.25€
├─────────────────────────────────────┤
│ 🟢 Battery Grid  │  🟢 Car Grid    │
│ Very low price   │  Very low price │
├─────────────────────────────────────┤
│ 🔋 Battery: 67%  │  ⚡ House: 2.1kW │
│ 3 batteries      │  Solar: 500W    │
├─────────────────────────────────────┤
│ 🟢 Very Low Price │ 🟢 Price OK    │
│ Threshold: 0.15€  │ Below threshold │
└─────────────────────────────────────┘
```

### Color Coding

- 🟢 **Green**: Favorable conditions (charge recommended, very low price)
- 🟠 **Orange**: Moderate conditions (low price, medium battery)
- 🔴 **Red**: Unfavorable conditions (high price, low battery, don't charge)

### Real-time Updates

The card automatically updates as the integration's sensors change, providing real-time visibility into:
- Current charging decisions and reasoning
- Price positioning within daily range
- Battery status across all configured batteries
- Power consumption and solar surplus
- Price trends and next hour forecasts

## ⭐ Support

If you find this integration useful, please consider starring the repository!

For issues and feature requests, please visit the [Issues](https://github.com/emavap/electricity_planner/issues) page.