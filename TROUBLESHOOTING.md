# 🔧 Electricity Planner Troubleshooting Guide

This guide helps you diagnose and fix common issues with the Electricity Planner integration.

## 📋 Table of Contents

1. [Quick Diagnostics](#quick-diagnostics)
2. [Common Issues](#common-issues)
3. [Data Validation](#data-validation)
4. [Decision Logic Issues](#decision-logic-issues)
5. [Performance Problems](#performance-problems)
6. [Advanced Debugging](#advanced-debugging)

## 🚀 Quick Diagnostics

### Step 1: Check the Decision Diagnostics Sensor

The most powerful troubleshooting tool is the **Decision Diagnostics** sensor:

1. Go to **Developer Tools** → **States**
2. Search for `sensor.electricity_planner_decision_diagnostics`
3. Click on it and examine the attributes

Key things to check:
- `validation_flags` → All should be `true` for normal operation
- `decisions` → Shows current charging decisions and reasons
- `power_allocation` → Verify solar allocation is correct
- `configured_limits` → Ensure your settings are applied

### Step 2: Verify Data Availability

Check `sensor.electricity_planner_data_unavailable_duration`:
- Value should be `0` when data is available
- Non-zero values indicate missing data (in seconds)

### Step 3: Enable Debug Logging

Add to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.electricity_planner: debug
    custom_components.electricity_planner.coordinator: debug
    custom_components.electricity_planner.decision_engine: debug
```

Then restart Home Assistant and check logs.

## 🔴 Common Issues

### Issue: Binary sensors always show False

**Symptoms:** 
- `binary_sensor.electricity_planner_battery_grid_charging` always off
- `binary_sensor.electricity_planner_car_grid_charging` always off

**Possible Causes & Solutions:**

1. **Price above threshold**
   - Check: `sensor.electricity_planner_price_analysis` → `current_price`
   - Compare with: `sensor.electricity_planner_price_threshold`
   - Solution: Adjust price threshold in integration options or wait for lower prices

2. **Missing Nord Pool data**
   - Check: All 4 Nord Pool entities have numeric values
   - Required entities:
     - Current price entity
     - Highest price entity  
     - Lowest price entity
     - Next hour price entity
   - Solution: Fix Nord Pool integration first

3. **Battery already full**
   - Check: `sensor.electricity_planner_battery_soc_average`
   - Compare with: Max SOC threshold (default 90%)
   - Solution: Normal behavior - batteries don't need charging

4. **Solar surplus available**
   - Check: `sensor.electricity_planner_solar_surplus_power`
   - Solution: Normal behavior - using solar instead of grid

**Diagnostic Query:**
```yaml
# Check in Developer Tools → Template
{{ state_attr('sensor.electricity_planner_decision_diagnostics', 'validation_flags') }}
{{ state_attr('sensor.electricity_planner_price_analysis', 'is_low_price') }}
{{ state_attr('sensor.electricity_planner_battery_analysis', 'average_soc') }}
```

### Issue: Unexpected charging recommendations

**Symptoms:**
- Charging when price seems high
- Not charging when price seems low

**Check Decision Reasoning:**

1. Look at the main decision sensor:
   ```
   sensor.electricity_planner_charging_recommendation_reason
   ```
   This shows the exact reason for current decision

2. Check emergency overrides:
   ```yaml
   {{ state_attr('sensor.electricity_planner_decision_diagnostics', 'validation_flags')['emergency_override_active'] }}
   ```

3. Check predictive logic:
   ```yaml
   {{ state_attr('sensor.electricity_planner_price_analysis', 'significant_price_drop') }}
   ```

**Common Reasons:**
- **Emergency charging**: SOC below 15% overrides price
- **Very low price**: Bottom 30% of daily range triggers charging
- **Predictive waiting**: Significant price drop expected next hour
- **Time-based logic**: Night/winter charging at different thresholds

### Issue: Car charger limit stuck at 1400W

**Symptoms:**
- `sensor.electricity_planner_car_charger_limit` always shows 1400W

**Cause:** Car charging not allowed due to high price

**Check:**
```yaml
{{ states('binary_sensor.electricity_planner_car_grid_charging') }}
{{ state_attr('sensor.electricity_planner_car_charger_limit', 'charger_limit_reason') }}
```

**Solution:** 
- This is intentional safety behavior
- 1.4kW limit when grid charging not recommended
- Will increase when price drops below threshold

### Issue: Missing battery data

**Symptoms:**
- Battery SOC shows 0 or unavailable
- Charging decisions show "Battery data unavailable"

**Check Entity Configuration:**

1. Verify battery entities exist:
   ```yaml
   # List your configured battery entities
   {{ state_attr('sensor.electricity_planner_decision_diagnostics', 'battery_analysis')['batteries_count'] }}
   ```

2. Check individual battery sensors in Developer Tools

3. Ensure sensors provide numeric values (0-100)

**Solution:**
- Reconfigure integration with correct battery entities
- Fix underlying battery sensor integration

## 📊 Data Validation

### Price Data Validation

Check all price components:

```yaml
# Template to check price data
{% set price = state_attr('sensor.electricity_planner_price_analysis', 'current_price') %}
{% set highest = state_attr('sensor.electricity_planner_price_analysis', 'highest_price') %}
{% set lowest = state_attr('sensor.electricity_planner_price_analysis', 'lowest_price') %}
{% set position = state_attr('sensor.electricity_planner_price_analysis', 'price_position') %}

Current: {{ price }} €/kWh
Highest: {{ highest }} €/kWh
Lowest: {{ lowest }} €/kWh
Position: {{ (position * 100) | round(0) }}% of daily range
Valid: {{ price is not none and highest is not none and lowest is not none }}
```

### Power Allocation Validation

Verify solar allocation doesn't exceed available:

```yaml
{% set allocation = state_attr('sensor.electricity_planner_decision_diagnostics', 'power_allocation') %}
{% set solar = state_attr('sensor.electricity_planner_power_analysis', 'solar_surplus') %}

Solar surplus: {{ solar }}W
Allocated to batteries: {{ allocation['solar_for_batteries'] }}W
Allocated to car: {{ allocation['solar_for_car'] }}W
Car current usage: {{ allocation['car_current_solar_usage'] }}W
Total allocated: {{ allocation['total_allocated'] }}W
Valid: {{ allocation['total_allocated'] <= solar }}
```

### Battery Capacity Weighting

If using multiple batteries with different capacities:

```yaml
{% set battery = state_attr('sensor.electricity_planner_battery_analysis', 'capacity_weighted') %}
Weighted averaging: {{ battery }}
{% if not battery %}
  ⚠️ Configure battery capacities in integration options for accurate SOC
{% endif %}
```

## 🧠 Decision Logic Issues

### Understanding Price Positioning

The integration uses **price position** (0-100% of daily range):

```yaml
{% set pos = state_attr('sensor.electricity_planner_price_analysis', 'price_position') %}
{% set very_low = state_attr('sensor.electricity_planner_very_low_price_threshold', 'threshold_decimal') %}

Current position: {{ (pos * 100) | round(1) }}%
Very low threshold: {{ (very_low * 100) | round(0) }}%
Is very low: {{ pos <= very_low }}
```

**Interpretation:**
- 0% = Lowest price of the day
- 100% = Highest price of the day
- <30% = Very low price (default)

### Time-Based Logic

Check current time context:

```yaml
{% set time = state_attr('sensor.electricity_planner_decision_diagnostics', 'time_context') %}
Current hour: {{ time['current_hour'] }}
Night (22-06): {{ time['is_night'] }}
Solar peak (10-16): {{ time['is_solar_peak'] }}
Winter season: {{ time['winter_season'] }}
```

**Impact on decisions:**
- **Night + Winter**: Charge at SOC <60%
- **Night only**: Charge at SOC <60%  
- **Solar peak**: Avoid charging if SOC >30% and good forecast
- **Winter day**: Charge at SOC <50%

### Solar Forecast Impact

Check how solar forecast affects decisions:

```yaml
{% set forecast = state_attr('sensor.electricity_planner_decision_diagnostics', 'solar_forecast') %}
Forecast available: {{ forecast['forecast_available'] }}
Solar factor: {{ (forecast['solar_production_factor'] * 100) | round(0) }}%
Expected production: {{ forecast['expected_solar_production'] }}
Influencing decisions: {{ forecast['solar_production_factor'] != 0.5 }}
```

**Thresholds:**
- >80% = Excellent (avoid grid charging)
- >60% = Good (moderate charging)
- >30% = Moderate (normal logic)
- ≤30% = Poor (prefer grid when cheap)

## ⚡ Performance Problems

### Slow Updates

**Check update frequency:**
```yaml
Last update: {{ states.sensor.electricity_planner_charging_recommendation_reason.last_updated }}
Update interval: 30 seconds max, 10 seconds min on entity changes
```

**Solutions:**
1. Check CPU usage of Home Assistant
2. Reduce number of monitored entities
3. Increase minimum update interval in code

### High CPU Usage

**Potential causes:**
- Too frequent entity updates
- Large battery array (many SOC sensors)
- Complex solar forecast entities

**Solutions:**
1. Reduce update triggers
2. Simplify entity configuration
3. Disable unused forecast entities

## 🔬 Advanced Debugging

### Manual Decision Testing

Test decision logic with specific scenarios:

```python
# In Developer Tools → Services
service: electricity_planner.test_decision
data:
  current_price: 0.10
  highest_price: 0.30
  lowest_price: 0.05
  battery_soc: 45
  solar_surplus: 2000
```

### Export Diagnostics

1. Go to **Settings** → **Devices & Services**
2. Find Electricity Planner
3. Click the three dots → **Download diagnostics**
4. Share the JSON file for support

### Database Queries

Check historical decisions:

```sql
-- In Developer Tools → SQL
SELECT 
  state,
  attributes,
  last_updated
FROM states
WHERE entity_id = 'sensor.electricity_planner_decision_diagnostics'
ORDER BY last_updated DESC
LIMIT 10;
```

## 📞 Getting Help

If issues persist after troubleshooting:

1. **Enable debug logging** (see above)
2. **Collect diagnostics** (Download from integration page)
3. **Document the issue**:
   - What you expected
   - What actually happened
   - Current price and battery SOC
   - Decision reasoning shown
4. **Create issue**: https://github.com/emavap/electricity_planner/issues

### Information to Include

```yaml
# Run this template and include output
Integration version: {{ device_attr('sensor.electricity_planner_charging_recommendation_reason', 'sw_version') }}
HA version: {{ states('sensor.version') }}

Validation flags:
{{ state_attr('sensor.electricity_planner_decision_diagnostics', 'validation_flags') | to_json }}

Current decisions:
{{ state_attr('sensor.electricity_planner_decision_diagnostics', 'decisions') | to_json }}

Configuration:
{{ state_attr('sensor.electricity_planner_decision_diagnostics', 'configured_limits') | to_json }}
```

## 🎯 Common Fixes Summary

| Problem | Quick Fix |
|---------|-----------|
| No charging despite low price | Check battery SOC, might be full |
| Charging at high price | Check for emergency SOC override |
| Always False decisions | Verify Nord Pool entities have data |
| 1.4kW car limit | Normal when price above threshold |
| Missing battery data | Reconfigure with valid SOC entities |
| Delayed updates | Check entity update frequency |
| Wrong decisions | Check Decision Diagnostics sensor |

## 💡 Pro Tips

1. **Monitor trends**: Use the Hourly History sensor for patterns
2. **Adjust thresholds**: Fine-tune based on your price patterns
3. **Use automations**: Act on binary sensors, not analysis sensors
4. **Check solar forecast**: Ensure forecast entities are configured
5. **Validate allocation**: Solar allocation should never exceed surplus

---

Remember: The Decision Diagnostics sensor (`sensor.electricity_planner_decision_diagnostics`) is your best friend for troubleshooting!
