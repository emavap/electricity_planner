# Electricity Planner - Smart Energy Management System

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/emavap/electricity_planner.svg)](https://github.com/emavap/electricity_planner/releases/)
[![License](https://img.shields.io/github/license/emavap/electricity_planner.svg)](LICENSE)

A comprehensive Home Assistant custom integration for intelligent electricity management with dynamic pricing markets. This integration optimizes battery and electric vehicle charging based on Nord Pool pricing, solar production forecasts, and **configurable safety parameters**. All decisions are boolean outputs with detailed reasoning and comprehensive diagnostics for validation.

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
- **Dedicated solar forecast entities** for accurate production prediction
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

### 🌞 Solar Forecast Analysis

The integration supports dedicated solar forecast entities for accurate production prediction:

#### Forecast Entity Priority

1. **Best**: Tomorrow + Today comparison
   ```python
   solar_production_factor = min(1.0, forecast_tomorrow / forecast_today)
   ```

2. **Good**: Remaining today forecast  
   ```python
   solar_production_factor = min(1.0, forecast_remaining_today / 5.0)  # 5kWh typical daily minimum
   ```

3. **Basic**: Hourly forecasts
   ```python
   solar_production_factor = min(1.0, max(current_hour, next_hour) / 2.0)  # 2kWh/hour good production
   ```

4. **Fallback**: Safe default (50% solar factor) when no entities configured

#### Solar Production Categories
- **Excellent**: Solar factor > 80% - Skip charging, wait for solar
- **Good**: Solar factor > 60% - Moderate charging decisions
- **Moderate**: Solar factor > 30% - Normal charging logic  
- **Poor**: Solar factor ≤ 30% - Prefer grid charging when cheap

## 🧠 Decision Logic Documentation

### Overview

The Electricity Planner uses a hierarchical decision engine that evaluates multiple factors to make intelligent charging recommendations. All decisions are boolean outputs (charge/don't charge) with detailed reasoning provided for validation.

### Core Decision Flow

```
1. Data Validation → 2. Power Allocation → 3. Decision Logic → 4. Safety Validation → 5. Output Generation
```

## 📊 Power Allocation Logic

### Solar Power Allocation (Hierarchical Priority)

```python
Available Solar Surplus = Solar Production - House Consumption
```

**Priority Order:**
1. **Current Car Consumption** (if car is charging > min_car_charging_threshold)
2. **Battery Charging** (if SOC < max_soc_threshold AND not batteries_full)
3. **Additional Car Charging** (if average_soc >= max_soc_threshold - 10%)
4. **Remaining Solar** (available for export or curtailment)

**Safety Limits Applied:**
- Battery allocation: `min(available_solar, max_battery_power, significant_solar_threshold)`
- Car allocation: `min(available_solar, max_car_power)`
- Total validation: `total_allocated <= solar_surplus` (with emergency correction if exceeded)

## 🔋 Battery Charging Decision Logic

### Decision Hierarchy (Evaluated in Order)

#### 1. **EMERGENCY CHARGING** (Highest Priority)
```python
if average_soc < emergency_soc_threshold:
    return CHARGE  # Override all other logic
```

#### 2. **SOLAR PRIORITY** 
```python
if solar_for_batteries > 0:
    return NO_GRID_CHARGE  # Use solar instead
```

#### 3. **VERY LOW PRICE** 
```python
if price_position <= (very_low_price_threshold / 100):
    return CHARGE  # Bottom X% of daily price range
```

#### 4. **PREDICTIVE CHARGING** (with Emergency Overrides)
```python
if is_low_price AND significant_price_drop AND average_soc > predictive_charging_min_soc:
    # Check emergency overrides
    if average_soc < emergency_soc_override OR 
       (is_night AND winter_season AND average_soc < winter_night_soc_override):
        return CHARGE  # Emergency override
    return NO_CHARGE  # Wait for better price
```

#### 5. **TIME-AWARE CHARGING** (During Low Price Periods)

**Solar Peak Hours Logic:**
```python
if is_solar_peak AND average_soc > 30 AND solar_forecast_factor > 0.6:
    if average_soc < solar_peak_emergency_soc:
        return CHARGE  # Emergency override
    return NO_CHARGE  # Wait for solar
```

**Winter Night Logic:**
```python
if is_night AND winter_season AND average_soc < 60:
    return CHARGE  # Aggressive winter charging
```

**Standard Night Logic:**
```python
if is_night AND average_soc < 60:
    return CHARGE  # Off-peak charging
```

**Winter Day Logic:**
```python
if winter_season AND average_soc < 50:
    return CHARGE  # Compensate for shorter days
```

#### 6. **SOC-BASED CHARGING** (During Low Price Periods)

**Critical Low:** `average_soc < 30%` → **ALWAYS CHARGE**
**Low + Poor Forecast:** `30% ≤ SOC < 40% AND solar_forecast < poor_solar_forecast_threshold` → **CHARGE**
**Medium + Excellent Forecast:** `30% ≤ SOC ≤ 60% AND solar_forecast > excellent_solar_forecast_threshold` → **NO CHARGE**
**Medium:** `SOC < 50%` → **CHARGE**

## 🚗 Car Charging Decision Logic

### Decision Hierarchy

#### 1. **Solar-Only Charging**
```python
if solar_for_car > 0:
    return CHARGE_WITH_SOLAR  # Use allocated solar power
```

#### 2. **Predictive Price Logic**
```python
if is_low_price AND significant_price_drop:
    return NO_CHARGE  # Wait for better price
```

#### 3. **Very Low Price**
```python
if very_low_price:
    return CHARGE  # Bottom X% of daily range
```

#### 4. **Price Improvement Check**
```python
if next_price < current_price:
    return NO_CHARGE  # Wait for better price
```

#### 5. **Standard Low Price**
```python
if is_low_price:
    return CHARGE
```

## ⚡ Power Output Calculations

### Charger Limit Logic

```python
if car_not_charging:
    return 0

if car_solar_only AND solar_for_car > 0:
    return min(solar_for_car, max_car_power)

if average_soc < max_soc_threshold:
    # Car gets grid setpoint, surplus goes to batteries
    return min(max_grid_setpoint, max_car_power)
else:
    # Car can use solar surplus + grid setpoint
    return min(solar_surplus + max_grid_setpoint, max_car_power)
```

### Grid Setpoint Logic

```python
max_grid_setpoint = min(monthly_grid_peak * 0.9, max_grid_power) if monthly_grid_peak > 2500 else 2500

# Case 1: Car charging + Battery < max_soc
if car_charging AND average_soc < max_soc_threshold:
    return min(car_charging_power, charger_limit, max_grid_setpoint)

# Case 2: Car charging + Battery >= max_soc  
if car_charging AND average_soc >= max_soc_threshold:
    car_grid_need = max(0, car_charging_power - allocated_solar)
    if battery_grid_charging:
        battery_power = min(max_grid_setpoint - car_grid_need, max_battery_power)
        return min(car_grid_need + battery_power, max_grid_power)
    return min(car_grid_need, max_grid_power)

# Case 3: No car, battery charging
if battery_grid_charging:
    return min(max_grid_setpoint, max_battery_power, max_grid_power)

# Case 4: No charging
return 0
```

## 🌞 Solar Feed-in Logic

```python
if remaining_solar <= 0:
    return NO_FEEDIN

if current_price >= feedin_price_threshold:
    return ENABLE_FEEDIN
else:
    return DISABLE_FEEDIN  # Keep surplus local
```

## ⚙️ Configurable Safety Parameters

All safety limits are user-configurable through the Home Assistant UI:

### Power Limits
- **max_battery_power**: Maximum battery charging power (Default: 3000W)
- **max_car_power**: Maximum car charging power (Default: 11000W)  
- **max_grid_power**: Absolute grid power safety limit (Default: 15000W)
- **min_car_charging_threshold**: Minimum power to consider car "charging" (Default: 100W)

### SOC Thresholds
- **emergency_soc_threshold**: True emergency SOC (Default: 15%)
- **emergency_soc_override**: SOC for predictive/solar overrides (Default: 25%)
- **winter_night_soc_override**: Winter night emergency charging SOC (Default: 40%)
- **solar_peak_emergency_soc**: Solar peak hours emergency SOC (Default: 25%)
- **predictive_charging_min_soc**: Minimum SOC for predictive logic (Default: 30%)

### Price & Solar Thresholds
- **price_threshold**: Base price threshold for "low price" (Default: €0.15/kWh)
- **very_low_price_threshold**: Percentage of daily range for "very low" (Default: 30%)
- **significant_solar_threshold**: Minimum solar surplus considered significant (Default: 1000W)
- **feedin_price_threshold**: Minimum price to enable solar export (Default: €0.05/kWh)

### Forecast Thresholds
- **poor_solar_forecast_threshold**: Below this % = poor forecast (Default: 40%)
- **excellent_solar_forecast_threshold**: Above this % = excellent forecast (Default: 80%)

## 🔍 Decision Validation

### Using the Diagnostics Sensor

The integration provides a comprehensive `Decision Diagnostics` sensor that exposes all decision parameters:

```yaml
# Example attributes available for validation
sensor.electricity_planner_decision_diagnostics:
  state: "charging_battery"
  attributes:
    decisions:
      battery_grid_charging: true
      battery_reason: "Low price (€0.12/kWh) - SOC 45% < 50% charging"
    power_allocation:
      solar_for_batteries: 0
      total_allocated: 150
      allocation_reason: "Car using 150W, 0W to batteries..."
    validation_flags:
      price_data_valid: true
      power_allocation_valid: true
      emergency_override_active: false
    configured_limits:
      max_battery_power: 3000
      emergency_soc_override: 25
```

### Validation Checklist

**Price Validation:**
- ✅ `price_data_valid = true`
- ✅ `current_price` matches external Nord Pool data
- ✅ `price_position` calculation: `(current - lowest) / (highest - lowest)`

**Power Allocation Validation:**
- ✅ `power_allocation_valid = true` (total_allocated ≤ solar_surplus)
- ✅ `solar_for_batteries` + `solar_for_car` + `car_current_solar_usage` = `total_allocated`
- ✅ Individual allocations respect configured limits

**SOC Logic Validation:**
- ✅ Emergency overrides activate below configured thresholds
- ✅ Predictive logic only applies above `predictive_charging_min_soc`
- ✅ Time-aware logic respects season and time contexts

**Safety Validation:**
- ✅ `charger_limit` ≤ `max_car_power`
- ✅ `grid_setpoint` ≤ `max_grid_power`
- ✅ Battery allocation ≤ `max_battery_power`

## 🚨 Emergency Override Logic

The system implements multiple layers of emergency overrides to prevent battery discharge during critical situations:

1. **True Emergency**: `SOC < emergency_soc_threshold` → Charge regardless of price
2. **Predictive Override**: `SOC < emergency_soc_override` → Override price waiting logic
3. **Winter Night Override**: `SOC < winter_night_soc_override` during winter nights
4. **Solar Peak Override**: `SOC < solar_peak_emergency_soc` during solar peak hours

## 📈 Time Context Logic

**Time Periods Defined:**
- **Night**: 22:00 - 06:00 (off-peak grid rates)
- **Early Morning**: 06:00 - 09:00 (pre-solar period)
- **Solar Peak**: 10:00 - 16:00 (maximum solar production)
- **Evening**: 17:00 - 21:00 (peak consumption period)
- **Winter Season**: November, December, January, February

## 🔧 Configuration Through Home Assistant UI

The integration provides a comprehensive configuration flow:

1. **Entity Selection**: Configure Nord Pool, battery, solar, and car entities
2. **Basic Thresholds**: Set SOC limits, price thresholds, and solar parameters
3. **Safety Limits**: Configure power limits and emergency overrides

All parameters are reconfigurable through Home Assistant's integration options without restarting the integration.

## 🎯 Usage Examples

### Example 1: Winter Night Emergency Override
- **Conditions**: 02:00 AM, December, SOC 35%, Low price with significant drop expected
- **Normal Logic**: Wait for price drop
- **Emergency Override**: SOC 35% < 40% (winter_night_soc_override) → **CHARGE**
- **Reason**: "Emergency override - SOC 35% too low to wait for price drop (winter night: true)"

### Example 2: Solar Peak Conservation
- **Conditions**: 12:00 PM, SOC 35%, Low price, Excellent solar forecast (85%)
- **Solar Peak Logic**: Normally wait for solar
- **Emergency Override**: SOC 35% > 25% (solar_peak_emergency_soc) → **NO CHARGE**
- **Reason**: "Solar peak hours - SOC 35% sufficient, awaiting solar production (forecast: 85%)"

### Example 3: Power Allocation Validation
- **Available Solar**: 2500W
- **Car Drawing**: 3000W
- **Allocation**: Car current usage: 2500W, Batteries: 0W, Car additional: 0W, Remaining: 0W
- **Validation**: `total_allocated (2500W) ≤ solar_surplus (2500W)` ✅

### Example 4: Predictive Charging with Emergency Override
- **Conditions**: 23:00 PM, SOC 20%, Low price (€0.10/kWh), Next hour: €0.05/kWh
- **Predictive Logic**: Would normally wait for better price
- **Emergency Override**: SOC 20% < 25% (emergency_soc_override) → **CHARGE**
- **Reason**: "Emergency override - SOC 20% too low to wait for price drop"

### Example 5: Configured Safety Limits Applied
- **Solar Surplus**: 15000W (unusual spike)
- **Max Battery Power**: 5000W (user configured)
- **Max Car Power**: 7000W (user configured)
- **Allocation**: Batteries: 5000W, Car: 7000W, Remaining: 3000W
- **Safety**: All allocations respect configured limits

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

The integration updates every **30 seconds** and immediately when any tracked entity changes:

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

### Scenario 7: Price Dropping Significantly with Well-Charged Batteries
```
Current Price: 0.027 €/kWh (threshold: 0.15)
Next Hour Price: 0.01 €/kWh (63% price drop)
Battery SOC: 60%
Solar Surplus: 0W
Time: Morning

Decision:
❌ Battery Grid Charging: FALSE ("Batteries at 60% - price dropping significantly next hour (0.027→0.01€/kWh)")
✅ Car Grid Charging: TRUE ("Low price - below threshold")
```

### Scenario 8: Solar Production with Well-Charged Batteries
```
Current Price: 0.027 €/kWh (threshold: 0.15)
Next Hour Price: 0.025 €/kWh
Battery SOC: 60%
Solar Surplus: 218W
Time: Daytime

Decision:
❌ Battery Grid Charging: FALSE ("Batteries well charged (60%) with solar production (218W) - use solar instead")
✅ Car Grid Charging: TRUE ("Low price - below threshold")
```

### Scenario 9: Price Jump Expected - Charge Now
```
Current Price: 0.12 €/kWh (threshold: 0.15)
Next Hour Price: 0.20 €/kWh (67% price increase)
Battery SOC: 45%
Solar Surplus: 0W

Decision:
✅ Battery Grid Charging: TRUE ("Price OK but not optimal - current: 0.120€/kWh, position: 35%")
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
- **Solar Production Entity**: Current solar production (W)
- **House Consumption Entity**: Current house power consumption (W)

### Optional Entities  
- **Car Charging Power Entity**: Current car charging power (W)
- **Monthly Grid Peak Entity**: Current month grid peak (W)

#### Solar Forecast Entities (Optional but Recommended)
- **Solar Forecast Current Entity**: Solar forecast for current hour (kWh)
- **Solar Forecast Next Entity**: Solar forecast for next hour (kWh)
- **Solar Forecast Today Entity**: Total solar forecast for today (kWh)
- **Solar Forecast Remaining Today Entity**: Remaining solar forecast for today (kWh)
- **Solar Forecast Tomorrow Entity**: Solar forecast for tomorrow (kWh)

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
- `sensor.electricity_planner_power_analysis` - Solar production, house consumption, calculated surplus, car charging power

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

## 🌍 Universal Compatibility Features

This integration is optimized for dynamic electricity markets:
- **Dynamic Pricing**: Compatible with variable electricity pricing (Nord Pool and similar markets)
- **Solar Feed-in**: Considers feed-in tariff structures and export optimization
- **Peak Shaving**: Reduces consumption during high-price periods
- **Regulatory Compliance**: Configurable safety limits for residential battery storage

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


## ⭐ Support

If you find this integration useful, please consider starring the repository!

For issues and feature requests, please visit the [Issues](https://github.com/emavap/electricity_planner/issues) page.