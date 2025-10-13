# ⚡ Electricity Planner Dashboard Visualizations

This document provides comprehensive dashboard card configurations for visualizing your Electricity Planner data in Home Assistant.

## 📊 Available Dashboard Cards

### NEW: **Nord Pool Price Visualization** - Full interval pricing (requires config)
- Displays available electricity prices from now into the future at full interval granularity
- Shows remaining prices for today + tomorrow's prices (when available after ~13:00 CET)
- Column chart showing all price intervals (adapts to Nord Pool's interval duration)
- **Prices include full buy price**: contract adjustments (multiplier + offset) + transport cost
- **Transport cost automatically determined** from 7-day history (handles day/night tariffs)
- Dynamic threshold lines (max threshold + intelligent dynamic threshold)
- "Now" marker at the start showing current time position
- Graph span adjusts automatically based on available data (max: until midnight tomorrow)
- Requires Nord Pool config entry configured in Electricity Planner settings
- Uses ApexCharts for professional visualization

### 1. **Main Decision Overview** - Essential charging decisions
- Current battery and car grid charging status
- Low price indicator and solar production status
- Overall grid charging decision
- Real-time status with last updated timestamps

### 2. **Price Analysis Gauges** - Visual meters
- Current electricity price with color-coded zones
- Battery SOC percentage gauge
- House power consumption meter
- Real-time needle indicators with safety zones

### 3. **Nord Pool Price Trends** - 24-hour price visualization
- Historical price data with smooth curves
- Price threshold annotations
- Current vs. historical price comparison
- Gradient fill for visual appeal

### 4. **Battery Analysis & Charging Decisions** - Multi-axis chart
- Battery SOC trends over 24 hours
- Battery and car charging decision overlay
- Dual Y-axis for percentage and binary decisions
- Color-coded charging status visualization

### 5. **Power Flow Analysis** - Energy consumption patterns
- Solar surplus production tracking
- House consumption monitoring
- Car charging power visualization
- Stacked area charts showing energy flow

### 6. **Price Position & Decision Logic** - Advanced analytics
- Price positioning within daily range (0-100%)
- Very low price threshold (30%) annotation
- Binary decision overlays
- Decision logic visualization

### 7. **Input Entities Status** - Data source monitoring
- Nord Pool price entities (current, high, low, next)
- Battery entities (SOC, capacity)
- Power flow entities (consumption, solar, car)
- Complete input data validation

### 8. **Quick Status Glance** - Mobile-friendly overview
- 5-sensor compact display
- Essential metrics at a glance
- Perfect for mobile dashboards
- Minimal space requirements

### 9. **Statistics Summary** - Weekly patterns
- Weekly charging decision patterns
- Statistical analysis (mean, max, min)
- Multi-sensor trend comparison
- Historical pattern recognition

### 10. **Decision Reasoning** - Detailed explanations
- Battery charging reasoning
- Car charging reasoning  
- Next evaluation timestamp
- Transparent decision logic

### 11. **Advanced Multi-Column Layout** - Professional view
- 4-row structured layout
- Status, metrics, analysis, and controls
- Comprehensive monitoring solution
- Professional dashboard appearance

### 12. **Integration Information** - Status and details
- Real-time integration status
- Current decision explanations
- Price positioning details
- Dynamic content with entity attributes

## 🚀 How to Use

### Step 1: Prerequisites
Before using these cards, ensure you have:

1. **Electricity Planner Integration** installed and configured
2. **Nord Pool Integration** for electricity prices
3. **Battery entities** configured in Home Assistant
4. **Power monitoring entities** for house consumption and solar

### Step 2: Install Required Custom Cards
Some advanced visualizations require custom cards:

1. **ApexCharts Card** (Required for Nord Pool price visualization)
   ```
   Install via HACS: https://github.com/RomRider/apexcharts-card
   ```
   - Required for: Nord Pool price chart, price trends, battery analysis, power flow, price position charts

2. **Button Card** (Optional)
   ```
   Install via HACS: https://github.com/custom-cards/button-card
   ```
   - Required for: Manual override buttons

### Step 3: Configure Nord Pool Integration (Required for price chart)
To enable the Nord Pool price visualization:

1. Open Electricity Planner integration settings in Home Assistant
2. Configure your Nord Pool config entry ID (from Nord Pool integration)
3. This enables fetching of full interval prices for today and tomorrow
4. The sensor `sensor.electricity_planner_diagnostics_monitoring_nordpool_prices` will be created

### Step 4: Configure Entity Names
Update the YAML configurations to match your actual entity names:

**Electricity Planner Entities:**
- `sensor.electricity_planner_price_analysis`
- `sensor.electricity_planner_battery_analysis`
- `sensor.electricity_planner_power_analysis`
- `sensor.electricity_planner_grid_charging_decision`
- `sensor.electricity_planner_diagnostics_monitoring_nordpool_prices` (NEW - for price chart)
- `binary_sensor.electricity_planner_battery_grid_charging`
- `binary_sensor.electricity_planner_car_grid_charging`
- `binary_sensor.electricity_planner_low_electricity_price`
- `binary_sensor.electricity_planner_solar_production_active`

**Nord Pool Entities (Replace with yours):**
- `sensor.nordpool_kwh_be_eur_3_10_025` → Your Nord Pool price entity

**Input Entities (Replace with yours):**
- `sensor.battery_soc` → Your battery SOC entity
- `sensor.battery_capacity` → Your battery capacity entity
- `sensor.house_consumption` → Your house power consumption entity
- `sensor.solar_surplus` → Your solar surplus entity
- `sensor.car_charging_power` → Your car charging power entity

### Step 5: Add Cards to Dashboard
1. Open Home Assistant
2. Go to **Overview** (main dashboard)
3. Click the **3-dot menu** (top right)
4. Select **Edit Dashboard**
5. Click **+ ADD CARD**
6. Select **Manual** (YAML editor)
7. Copy any card configuration from `electricity_planner_dashboard.yaml`
8. Paste it into the card editor
9. **Update entity names** to match your setup
10. Click **SAVE**

**Note**: The Nord Pool price chart requires ApexCharts Card to be installed and the Nord Pool config entry to be configured in Electricity Planner settings.

## 📱 Mobile Optimization

For mobile dashboards, recommended card order:
1. **Quick Status Glance** - Essential overview
2. **Main Decision Overview** - Current decisions
3. **Price Analysis Gauges** - Key metrics
4. **Decision Reasoning** - Why decisions were made

For tablets/desktop, use the full comprehensive layout.

## 🎨 Customization Guide

### Color Coding
- 🟢 **Green**: Favorable conditions (charging recommended, low prices, good battery level)
- 🟡 **Yellow**: Moderate conditions (medium prices, adequate battery)
- 🔴 **Red**: Unfavorable conditions (high prices, low battery, don't charge)
- 🔵 **Blue**: Information (neutral status, house consumption)
- 🟠 **Orange**: Warning/Action needed

### Gauge Ranges
Adjust these ranges in the gauge configurations based on your setup:

**Price Gauge:**
- Green: 0 - 0.15 €/kWh
- Yellow: 0.15 - 0.25 €/kWh
- Red: 0.25+ €/kWh

**Battery SOC:**
- Red: 0 - 30%
- Yellow: 30 - 60%
- Green: 60 - 100%

**House Power:**
- Green: 0 - 3000W
- Yellow: 3000 - 4500W
- Red: 4500+ W

### Chart Time Spans
Most charts show 24-hour data. Modify the `graph_span` parameter:
- `graph_span: 12h` - 12 hours
- `graph_span: 48h` - 2 days
- `graph_span: 7d` - 1 week

## 🔧 Advanced Features

### Price Threshold Annotations
Charts include visual threshold lines:
- Orange line at 0.15 €/kWh (default price threshold)
- Green line at 30% price position (very low price threshold)

### Multi-Axis Charts
Several charts use dual Y-axes:
- Left axis: Continuous values (SOC %, price position)
- Right axis: Binary decisions (ON/OFF states)

### Attribute Visualization
Cards display entity attributes like:
- `price_position` - Where current price sits in daily range
- `reason` - Detailed explanation of charging decisions
- `solar_surplus` - Current solar surplus production
- `very_low_price` - Boolean indicating very low price periods

## 🛠️ Troubleshooting

### Common Issues

**"Entity not available" errors:**
1. Check that Electricity Planner integration is working
2. Verify entity names in **Developer Tools** → **States**
3. Update entity names in YAML configurations
4. Ensure all input entities are configured

**Charts not displaying:**
1. Install ApexCharts Card via HACS
2. Restart Home Assistant after installation
3. Clear browser cache (Ctrl+Shift+R)
4. Check browser console for JavaScript errors

**Missing data in charts:**
1. Verify entities have historical data
2. Check that recorder is configured for your entities
3. Ensure integration is updating regularly
4. Validate entity state values are numeric

**Performance issues:**
1. Reduce number of cards on single dashboard
2. Increase chart update intervals
3. Reduce `graph_span` for shorter time periods
4. Use separate dashboards for different views

### Entity Validation Checklist

Before using the dashboard, verify these entities exist:

```yaml
# Required Electricity Planner entities
sensor.electricity_planner_price_analysis
sensor.electricity_planner_battery_analysis  
sensor.electricity_planner_power_analysis
binary_sensor.electricity_planner_battery_grid_charging
binary_sensor.electricity_planner_car_grid_charging

# Required input entities (names may vary)
sensor.nordpool_* (your Nord Pool entity)
sensor.*battery_soc* (your battery SOC entity)
sensor.*consumption* (your house consumption entity)
sensor.*solar* (your solar production entity)
```

## 💡 Tips & Best Practices

1. **Start Simple**: Begin with basic entity cards, then add graphs
2. **Test Entity Names**: Use Developer Tools to verify entity names
3. **Mobile First**: Design for mobile, then adapt for desktop
4. **Monitor Performance**: Too many charts can slow dashboards
5. **Regular Updates**: Update configurations as your setup changes
6. **Backup Configs**: Save working YAML configurations
7. **Use Themes**: Apply consistent Home Assistant themes
8. **Group Related Cards**: Use vertical-stack for logical grouping

## 📊 Data Insights

The dashboard enables you to:

### Monitor Patterns
- **Daily price cycles** - When electricity is cheapest
- **Charging frequency** - How often decisions trigger charging
- **Solar correlation** - How solar production affects decisions
- **Battery usage** - Discharge/charge patterns

### Optimize Settings
- **Price thresholds** - Adjust based on observed price patterns
- **SOC thresholds** - Fine-tune battery charge levels
- **Update intervals** - Balance responsiveness vs. performance

### Troubleshoot Issues
- **Decision logic** - Understand why charging was/wasn't triggered
- **Input validation** - Verify all data sources are working
- **Integration status** - Monitor for integration problems

## 🔗 Related Documentation

- [Home Assistant Dashboard Documentation](https://www.home-assistant.io/dashboards/)
- [ApexCharts Card Documentation](https://github.com/RomRider/apexcharts-card)
- [Card Configuration Reference](https://www.home-assistant.io/dashboards/cards/)
- [HACS Custom Cards](https://hacs.xyz/categories/frontend/)
- [Nord Pool Integration](https://github.com/custom-components/nordpool)

---

**Enjoy your enhanced electricity planning visualization! ⚡📊**