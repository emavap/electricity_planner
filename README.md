# Electricity Planner

**Version 4.7.1** | **Config Schema Version 11** | **Home Assistant 2024.4+**

Electricity Planner is a Home Assistant custom integration that transforms Nord Pool market data and your home telemetry into actionable automation signals. It never controls hardware directly—instead, it delivers boolean charging decisions, recommended power limits, and comprehensive diagnostics that you wire into your battery inverter, EV charger, and home automation workflows.

---

## Table of Contents

1. [Quick Start](#1-quick-start)
2. [Decision Pipeline](#2-decision-pipeline)
3. [Pricing Models](#3-pricing-models)
4. [Solar & Battery Behaviour](#4-solar--battery-behaviour)
5. [Three-Phase Support](#5-three-phase-support)
6. [Car Charging Logic](#6-car-charging-logic)
7. [Manual Overrides & Services](#7-manual-overrides--services)
8. [Entities Reference](#8-entities-reference)
9. [Configuration Options](#9-configuration-options)
10. [Automation Examples](#10-automation-examples)
11. [Dashboard Setup](#11-dashboard-setup)
12. [Troubleshooting](#12-troubleshooting)
13. [Testing & Development](#13-testing--development)
14. [FAQ](#14-faq)
15. [Contributing](#15-contributing)

---

## 1. Quick Start

### Requirements

- **Home Assistant 2024.4+**
- **Nord Pool integration** providing:
  - `current_price`
  - `highest_price_today`
  - `lowest_price_today`
  - `next_hour_price`
- **At least one battery SOC sensor** (percentage, optional capacity in kWh)
- **Solar production** (W) and **house consumption** (W) sensors
- **Optional but recommended:**
  - EV charging power sensor (W)
  - Monthly grid peak entity (W)
  - Transport cost sensor (€/kWh)
  - Grid power sensor (W, for real-time import/export tracking)

### Installation

| Method | Steps |
|--------|-------|
| **HACS** | 1. HACS → Integrations → `+` → Custom repository `https://github.com/emavap/electricity_planner` (Integration)<br>2. Install the integration<br>3. Restart Home Assistant |
| **Manual** | 1. Download the latest release archive<br>2. Copy `custom_components/electricity_planner/` into `<config>/custom_components/`<br>3. Restart Home Assistant |

### First-Time Configuration

1. **Settings → Devices & Services → `+ Add Integration`** → search for **Electricity Planner**
2. Complete the multi-step wizard:
   - **Topology** – Single-phase or three-phase (L1/L2/L3) operation
   - **Entities** – Nord Pool, battery SOC sensors, solar/consumption, EV sensors
   - **Battery Capacities** – Per-battery kWh values and phase assignments (three-phase only)
   - **SOC Thresholds** – min/max SOC, emergency threshold, predictive charging SOC
   - **Price Thresholds** – static ceiling, very-low-price %, feed-in threshold
   - **Power Limits** – max battery/car/grid power, base grid setpoint
   - **Solar Parameters** – significant surplus threshold
   - **Advanced** – Dynamic threshold, average threshold, SOC price multiplier settings
3. Save and wait for the coordinator to populate sensors (~10–30 seconds)

### Updating Configuration

- **Settings → Devices & Services → Electricity Planner → Configure** reopens the options flow
- Forms merge your saved `options` with original data—defaults always reflect current values
- Submit only the steps you need to modify; unchanged values persist automatically

---

## 2. Decision Pipeline

The integration processes data through a multi-stage pipeline every 30 seconds (or on entity state changes):

1. **Data Validation** – Ensures every configured entity is available and convertible to numbers; falls back to safe defaults where possible

2. **Price Analysis** – Computes:
   - Price position within daily range (0–100%)
   - Volatility detection (high/medium/low)
   - Next-hour price trend
   - "Very low" percentage band check
   - Dynamic confidence score (optional)

3. **Battery Analysis** – Calculates:
   - Capacity-weighted average SOC across multiple batteries
   - Min/max SOC thresholds
   - Remaining headroom
   - Emergency SOC detection

4. **Power Analysis** – Measures:
   - Real-time solar production
   - House consumption (with and without EV)
   - Solar surplus available for batteries/EV/export
   - "Significant surplus" flag when exceeding configured threshold

5. **Solar Allocation** – Distributes surplus:
   - Reserve for current EV draw
   - Then batteries
   - Bonus EV power once batteries near target SOC
   - Never exceeds measured surplus

6. **Strategy Evaluation** – Ordered strategy stack returns "charge" or "wait" with human-readable reason:
   - **SolarPriorityStrategy** (Priority 1) – Prefer solar over grid
   - **PredictiveChargingStrategy** (Priority 2) – Delay for better prices (advisory)
   - **VeryLowPriceStrategy** (Priority 3) – Charge at excellent prices
   - **SOCBufferChargingStrategy** (Priority 4) – Buffer charging at acceptable prices when SOC low
   - **DynamicPriceStrategy** (Priority 5) – Dynamic price analysis with confidence scoring
   - **SOCBasedChargingStrategy** (Priority 6) – Safety net for low SOC situations

7. **Car Decision Logic** – Applies hysteresis for OFF→ON transitions (see [Car Charging Logic](#6-car-charging-logic))

8. **Feed-in & Safety Outputs** – Computes charger limit, grid setpoint, and feed-in solar decision

9. **Diagnostics** – Publishes every step and reason through `sensor.electricity_planner_decision_diagnostics`

### PriceThresholdGuard

Before strategies run, the **PriceThresholdGuard** enforces:
- **Price ceiling**: Blocks charging when price exceeds threshold (with SOC-based relaxation)
- **Emergency override**: Forces charging when SOC ≤ emergency threshold regardless of price
- **SOC price multiplier**: Relaxes threshold when battery is low (configurable 1.0–1.3×)

---

## 3. Pricing Models

### Static Threshold

Set `price_threshold` to your maximum acceptable price (e.g., €0.15/kWh). Batteries and EVs only consider grid charging beneath this ceiling.

### Dynamic Threshold (Optional)

Enable `use_dynamic_threshold` to use intelligent confidence-based pricing within your static ceiling:

- **Inputs:** Current/high/low price, next-hour price, volatility, SOC, solar surplus
- **Volatility adaptation:**
  - High volatility (>50% range): Charge only at bottom 40% of acceptable range
  - Medium volatility (30–50%): Charge at bottom 60%
  - Low volatility (<30%): Charge at bottom 80%
- **Confidence scoring:** Weighted combination of price quality, dynamic threshold position, and next-hour trend
- **SOC/Solar adjustments:** Low SOC relaxes requirements; significant solar tightens them

### Average Threshold (Optional)

Enable `use_average_threshold` to calculate a 24-hour rolling average price as your threshold:

- Uses future Nord Pool prices when available (preferred)
- Backfills with recent past prices when future data < 24 hours
- Requires minimum 24-hour window for stable threshold
- Hysteresis protection (3 consecutive valid calculations)
- Graceful fallback to static threshold when insufficient data

### Price Adjustments

Configure price adjustments to account for taxes, fees, or currency conversion:

- **`price_adjustment_multiplier`** (default: 1.0) – Multiplies raw Nord Pool prices
- **`price_adjustment_offset`** (default: 0.0) – Adds fixed amount after multiplication
- **`feedin_adjustment_multiplier`** (default: 1.0) – Multiplies feed-in prices
- **`feedin_adjustment_offset`** (default: 0.0) – Adds fixed amount to feed-in prices
- **`transport_cost_entity`** – Optional sensor for dynamic transport costs

**Formula:** `adjusted_price = (raw_price × multiplier) + offset + transport_cost`

---

## 4. Solar & Battery Behaviour

### Solar Priority

When solar surplus exceeds house consumption:
1. **Batteries first** – Solar charges batteries before considering grid
2. **EV bonus** – Once batteries approach target SOC, excess solar goes to EV
3. **Grid charging blocked** – No grid charging while significant solar available

### SOC-Based Price Relaxation

When battery SOC is low, the integration relaxes price requirements to prevent peak demand issues:

| SOC Level | Price Multiplier | Effect |
|-----------|------------------|--------|
| ≥ Buffer Target (default 50%) | 1.0× | No relaxation |
| At Emergency SOC (default 15%) | Up to 1.3× | Accept 30% higher prices |
| Between | Linear interpolation | Gradual relaxation |

Configure via:
- **`soc_price_multiplier_max`** (default: 1.3) – Maximum multiplier at emergency SOC
- **`soc_buffer_target`** (default: 50%) – SOC above which no relaxation applies

### Multi-Battery Support

The integration supports multiple batteries with:
- **Capacity-weighted average SOC** – Larger batteries have more influence
- **Per-battery capacity configuration** – Set kWh for each battery sensor
- **Phase assignments** (three-phase mode) – Assign batteries to L1/L2/L3

---

## 5. Three-Phase Support

Enable three-phase mode for installations with phase-specific batteries or inverters:

### Configuration

1. Select **"Three-phase"** in the Topology step
2. Configure per-phase entities:
   - Solar production per phase
   - House consumption per phase
   - Battery SOC sensors per phase
   - Battery power sensors per phase (optional)
3. Assign batteries to phases in the Battery Capacities step

### How It Works

1. **Aggregation** – Telemetry from all phases is aggregated into a single "virtual phase"
2. **Decision** – The decision engine evaluates the aggregated data
3. **Distribution** – Results are distributed back to phases based on capacity shares

### Phase Attributes

Binary sensors expose `phase_results` attribute containing:
- Per-phase grid setpoints
- Component breakdowns
- Reasons per phase
- Capacity shares

---

## 6. Car Charging Logic

### Hysteresis Protection

Car charging implements strict hysteresis to prevent short charging cycles:

**OFF → ON Transition:**
1. Current price must be below threshold
2. Minimum charging window validation:
   - Checks next N hours (configurable via `min_car_charging_duration`, default 2h)
   - Detects interval resolution automatically (15-min or 1-hour)
   - Builds timeline with explicit start/end times
   - Accumulates continuous low-price duration from NOW
   - Any gap >5 seconds breaks the window
   - Returns true only if accumulated duration ≥ configured minimum

**ON → OFF Transition:**
- Immediately when current price exceeds threshold
- No window check needed

### Threshold Floor Pattern

When car charging starts (OFF→ON):
1. **Lock** – Current price threshold is locked
2. **During charging** – Uses `max(locked_threshold, current_threshold)` as effective threshold
3. **Effect** – Prevents threshold decreases from stopping mid-session
4. **Allows increases** – Threshold increases take effect immediately
5. **Clear** – Lock clears when charging stops (ON→OFF)

This is critical for charging continuity when using dynamic or average thresholds.

### Permissive Mode

Enable the **Car Permissive Mode** switch to temporarily relax car charging requirements:

- Increases effective threshold by configurable multiplier (default: 1.2 = 20% higher)
- Useful when you need to charge regardless of optimal pricing
- Configure via `car_permissive_threshold_multiplier`

### Peak Import Limiting

When configured with a grid power sensor and monthly peak entity:
- Monitors real-time grid import
- Reduces car charging power to stay below peak threshold
- Exposes `car_peak_limited` and `car_peak_limit_threshold_w` in power analysis

---

## 7. Manual Overrides & Services

### Available Services

#### `electricity_planner.set_manual_override`

Force a specific charging decision for a duration:

```yaml
service: electricity_planner.set_manual_override
data:
  target: battery  # or "car"
  action: charge   # or "block"
  duration: 60     # minutes (optional, default: 60)
```

| Parameter | Values | Description |
|-----------|--------|-------------|
| `target` | `battery`, `car` | Which system to override |
| `action` | `charge`, `block` | Force charging on or block it |
| `duration` | 1–1440 | Override duration in minutes |

#### `electricity_planner.clear_manual_override`

Remove an active override:

```yaml
service: electricity_planner.clear_manual_override
data:
  target: battery  # or "car"
```

### Override Behaviour

- Overrides take precedence over all strategy decisions
- Automatically expire after configured duration
- Visible in `decision_diagnostics` sensor attributes
- Can be cleared manually at any time

---

## 8. Entities Reference

### Binary Sensors (Automation)

| Entity | Purpose | Key Attributes |
|--------|---------|----------------|
| `binary_sensor.electricity_planner_battery_grid_charging` | Should batteries charge from grid? | `reason`, `strategy`, `phase_results` |
| `binary_sensor.electricity_planner_car_grid_charging` | Should EV charge from grid? | `reason`, `strategy`, `phase_results` |
| `binary_sensor.electricity_planner_feedin_solar` | Should solar be exported to grid? | `feedin_threshold`, `current_price` |

### Binary Sensors (Diagnostic)

| Entity | Purpose | Key Attributes |
|--------|---------|----------------|
| `binary_sensor.electricity_planner_low_price` | Is current price below threshold? | `price_threshold`, `current_price` |
| `binary_sensor.electricity_planner_solar_production` | Is solar currently producing? | `solar_production_w` |
| `binary_sensor.electricity_planner_data_availability` | Is all required data available? | `missing_entities`, `last_update` |

### Sensors (Automation)

| Entity | Purpose | Unit |
|--------|---------|------|
| `sensor.electricity_planner_car_charger_limit` | Recommended EVSE power limit | W |
| `sensor.electricity_planner_grid_setpoint` | Suggested grid power setpoint | W |

### Sensors (Diagnostic)

| Entity | Purpose | Unit |
|--------|---------|------|
| `sensor.electricity_planner_decision_diagnostics` | Full decision context | - |
| `sensor.electricity_planner_current_electricity_price` | Current adjusted price | €/kWh |
| `sensor.electricity_planner_solar_surplus_power` | Available solar surplus | W |
| `sensor.electricity_planner_battery_soc_average` | Capacity-weighted average SOC | % |
| `sensor.electricity_planner_data_unavailable_duration` | Time since data became unavailable | s |
| `sensor.electricity_planner_entity_status` | Entity availability summary | - |
| `sensor.electricity_planner_nord_pool_prices` | All Nord Pool price data | €/kWh |
| `sensor.electricity_planner_price_threshold` | Current effective price threshold | €/kWh |
| `sensor.electricity_planner_feedin_threshold` | Current feed-in threshold | €/kWh |
| `sensor.electricity_planner_dynamic_threshold` | Dynamic threshold value | €/kWh |
| `sensor.electricity_planner_average_threshold` | 24h rolling average threshold | €/kWh |
| `sensor.electricity_planner_very_low_price_threshold` | Very low price cutoff | €/kWh |
| `sensor.electricity_planner_soc_price_multiplier` | Current SOC-based multiplier | × |
| `sensor.electricity_planner_effective_threshold` | Final threshold after all adjustments | €/kWh |
| `sensor.electricity_planner_car_charging_window` | Remaining low-price window | min |
| `sensor.electricity_planner_next_low_price_window` | Time until next charging window | min |

### Switch

| Entity | Purpose |
|--------|---------|
| `switch.electricity_planner_car_permissive_mode` | Toggle permissive car charging mode |

### Decision Diagnostics Attributes

The `decision_diagnostics` sensor exposes comprehensive data:

```yaml
# Price Analysis
current_price: 0.12
highest_price: 0.35
lowest_price: 0.08
price_position: 0.25
price_threshold: 0.15
dynamic_threshold: 0.11
is_low_price: true

# Battery Analysis
average_soc: 45.5
batteries_count: 2
min_soc_threshold: 15
max_soc_threshold: 95
remaining_capacity_percent: 49.5

# Power Analysis
solar_production: 3500
house_consumption: 1200
solar_surplus: 2300
has_solar_surplus: true
significant_solar_surplus: true

# Decision Results
battery_should_charge: true
battery_reason: "Very low price - charge recommended"
battery_strategy: "VeryLowPriceStrategy"
car_should_charge: true
car_reason: "Price below threshold with valid charging window"

# Three-Phase (when enabled)
phase_results:
  L1: { grid_setpoint: 2000, reason: "..." }
  L2: { grid_setpoint: 1500, reason: "..." }
  L3: { grid_setpoint: 1000, reason: "..." }
```

---

## 9. Configuration Options

### Entity Selection

| Option | Description | Required |
|--------|-------------|----------|
| `nordpool_current_price` | Current electricity price sensor | Yes |
| `nordpool_highest_price` | Today's highest price sensor | Yes |
| `nordpool_lowest_price` | Today's lowest price sensor | Yes |
| `nordpool_next_hour_price` | Next hour price sensor | Yes |
| `nordpool_config_entry` | Nord Pool integration entry (for forecast data) | No |
| `battery_soc_entities` | List of battery SOC sensors | Yes |
| `solar_production_entity` | Solar production power sensor | Yes |
| `house_consumption_entity` | House consumption power sensor | Yes |
| `car_charging_power_entity` | EV charging power sensor | No |
| `grid_power_entity` | Grid import/export power sensor | No |
| `monthly_peak_entity` | Monthly peak demand sensor | No |
| `transport_cost_entity` | Dynamic transport cost sensor | No |

### SOC Thresholds

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `min_soc_threshold` | 15% | 5–50% | Minimum battery SOC |
| `max_soc_threshold` | 95% | 50–100% | Maximum battery SOC |
| `emergency_soc_threshold` | 15% | 5–30% | Force charging below this |
| `predictive_charging_soc` | 30% | 10–50% | SOC for predictive logic |
| `soc_buffer_target` | 50% | 20–80% | SOC above which no price relaxation |
| `soc_price_multiplier_max` | 1.3 | 1.0–2.0 | Max price multiplier at emergency SOC |

### Price Thresholds

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `price_threshold` | 0.15 €/kWh | 0.01–1.0 | Maximum acceptable price |
| `very_low_price_percent` | 30% | 5–50% | Bottom % of range = "very low" |
| `feedin_threshold` | 0.10 €/kWh | 0.01–1.0 | Minimum price for solar export |
| `use_dynamic_threshold` | false | - | Enable dynamic threshold |
| `dynamic_threshold_confidence` | 70% | 50–95% | Confidence level for dynamic |
| `use_average_threshold` | false | - | Enable 24h average threshold |
| `price_adjustment_multiplier` | 1.0 | 0.5–2.0 | Price multiplier |
| `price_adjustment_offset` | 0.0 | -0.5–0.5 | Price offset (€/kWh) |
| `feedin_adjustment_multiplier` | 1.0 | 0.5–2.0 | Feed-in price multiplier |
| `feedin_adjustment_offset` | 0.0 | -0.5–0.5 | Feed-in price offset |

### Power Limits

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `max_battery_power` | 5000 W | 500–50000 | Max battery charging power |
| `max_car_power` | 11000 W | 1000–22000 | Max EV charging power |
| `max_grid_power` | 10000 W | 1000–50000 | Max grid import power |
| `base_grid_setpoint` | 5000 W | 1000–15000 | Base grid setpoint |
| `min_car_charging_power` | 1400 W | 500–3000 | Min power to detect EV charging |
| `min_car_charging_duration` | 2 h | 0.5–8 | Min charging window for hysteresis |
| `car_permissive_threshold_multiplier` | 1.2 | 1.0–2.0 | Permissive mode multiplier |

### Solar Parameters

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `significant_solar_surplus` | 500 W | 100–5000 | Threshold for "significant" surplus |

### Three-Phase Configuration

| Option | Description |
|--------|-------------|
| `phase_mode` | `single` or `three_phase` |
| `phases` | Per-phase entity configuration (L1/L2/L3) |
| `battery_phase_assignments` | Map of battery entity → phase |
| `battery_capacities` | Map of battery entity → kWh capacity |

---

## 10. Automation Examples

### Basic Battery Charging Automation

```yaml
automation:
  - alias: "Battery Grid Charging Control"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_battery_grid_charging
    action:
      - choose:
          - conditions:
              - condition: state
                entity_id: binary_sensor.electricity_planner_battery_grid_charging
                state: "on"
            sequence:
              - service: number.set_value
                target:
                  entity_id: number.inverter_grid_charging_power
                data:
                  value: "{{ states('sensor.electricity_planner_grid_setpoint') | int }}"
          - conditions:
              - condition: state
                entity_id: binary_sensor.electricity_planner_battery_grid_charging
                state: "off"
            sequence:
              - service: number.set_value
                target:
                  entity_id: number.inverter_grid_charging_power
                data:
                  value: 0
```

### EV Charger Control

```yaml
automation:
  - alias: "EV Charging Control"
    trigger:
      - platform: state
        entity_id: binary_sensor.electricity_planner_car_grid_charging
    action:
      - choose:
          - conditions:
              - condition: state
                entity_id: binary_sensor.electricity_planner_car_grid_charging
                state: "on"
            sequence:
              - service: switch.turn_on
                target:
                  entity_id: switch.ev_charger
              - service: number.set_value
                target:
                  entity_id: number.ev_charger_current_limit
                data:
                  value: "{{ (states('sensor.electricity_planner_car_charger_limit') | int / 230) | round(0) }}"
          - conditions:
              - condition: state
                entity_id: binary_sensor.electricity_planner_car_grid_charging
                state: "off"
            sequence:
              - service: switch.turn_off
                target:
                  entity_id: switch.ev_charger
```

### Manual Override Button

```yaml
script:
  force_battery_charge:
    alias: "Force Battery Charge 1 Hour"
    sequence:
      - service: electricity_planner.set_manual_override
        data:
          target: battery
          action: charge
          duration: 60

  cancel_override:
    alias: "Cancel Override"
    sequence:
      - service: electricity_planner.clear_manual_override
        data:
          target: battery
```

### Permissive Mode Toggle

```yaml
automation:
  - alias: "Enable Permissive Mode When Car Plugged In"
    trigger:
      - platform: state
        entity_id: binary_sensor.ev_cable_connected
        to: "on"
    condition:
      - condition: numeric_state
        entity_id: sensor.ev_battery_soc
        below: 30
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.electricity_planner_car_permissive_mode
```

---

## 11. Dashboard Setup

### Prerequisites

Install these HACS frontend cards:

| Card | Purpose | Installation |
|------|---------|--------------|
| [**Gauge Card Pro**](https://github.com/benjamin-dcs/gauge-card-pro) | Dynamic price threshold gauges | HACS → Frontend → Search "Gauge Card Pro" |
| [**ApexCharts Card**](https://github.com/RomRider/apexcharts-card) | Historical price charts | HACS → Frontend → Search "ApexCharts Card" |
| [**Button Card**](https://github.com/custom-cards/button-card) | Manual override controls | HACS → Frontend → Search "Button Card" |

### Dashboard Template

1. Download `dashboard_template.yaml` from the [latest release](https://github.com/emavap/electricity_planner/releases/latest)
2. Settings → Dashboards → Add Dashboard
3. Type: "Panel (1 card)"
4. Paste the template YAML
5. Replace placeholder entity IDs with your actual sensor names

See [DASHBOARD.md](DASHBOARD.md) for detailed dashboard configuration.

---

## 12. Troubleshooting

### Common Issues

#### "Data unavailable" warnings

**Symptoms:** `binary_sensor.electricity_planner_data_availability` is `off`

**Solutions:**
1. Check `sensor.electricity_planner_entity_status` for missing entities
2. Verify Nord Pool integration is working
3. Ensure all configured sensors are available
4. Check Home Assistant logs for entity errors

#### Charging not starting despite low prices

**Check:**
1. Is `binary_sensor.electricity_planner_battery_grid_charging` actually `on`?
2. Check the `reason` attribute for explanation
3. Verify SOC is below `max_soc_threshold`
4. Check if solar surplus is blocking grid charging
5. Review `sensor.electricity_planner_decision_diagnostics` attributes

#### Car charging stops unexpectedly

**Possible causes:**
1. Price exceeded threshold (check `reason` attribute)
2. Minimum charging window not met (hysteresis protection)
3. Peak import limiting activated (check `car_peak_limited` attribute)

**Solutions:**
1. Enable permissive mode for less strict thresholds
2. Increase `min_car_charging_duration` for longer windows
3. Adjust `price_threshold` or enable dynamic threshold

#### Three-phase imbalance

**Symptoms:** Uneven power distribution across phases

**Solutions:**
1. Verify battery phase assignments are correct
2. Check per-phase entity configuration
3. Review `phase_results` attribute for per-phase decisions

### Debug Logging

Enable debug logging in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.electricity_planner: debug
```

---

## 13. Testing & Development

### Running Tests

Tests run via Docker (no local Python dependencies required):

```bash
# Build test image
docker build -f Dockerfile.tests -t electricity-planner-tests .

# Run all tests
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest

# Run specific test file
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest tests/test_decision_engine.py

# Run with coverage
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest --cov=custom_components/electricity_planner
```

### Development Workflow

1. Make code changes
2. Run tests to verify
3. Copy `custom_components/electricity_planner/` to Home Assistant config
4. Restart Home Assistant
5. Check logs for errors

### Code Structure

```
custom_components/electricity_planner/
├── __init__.py           # Integration setup, service registration
├── const.py              # Constants, configuration keys (65+ CONF_ constants)
├── coordinator.py        # Data coordination, state management
├── decision_engine.py    # Core charging algorithms (4500+ lines)
├── strategies.py         # 6 strategy classes + StrategyManager
├── dynamic_threshold.py  # DynamicThresholdAnalyzer class
├── defaults.py           # Default values, SOC price multiplier calculation
├── config_flow.py        # Multi-step configuration wizard
├── sensor.py             # 19 sensor entities
├── binary_sensor.py      # 6 binary sensor entities
├── switch.py             # Car permissive mode switch
├── migrations.py         # Config version migrations (v1→v11)
├── manifest.json         # Integration metadata
└── strings.json          # UI translations
```

---

## 14. FAQ

### Q: Does this integration control my inverter/charger directly?

**A:** No. Electricity Planner only provides boolean decisions and recommended power values. You must create automations to act on these signals.

### Q: Can I use this without solar panels?

**A:** Yes. Configure `solar_production_entity` to a helper that always returns 0, or leave solar-related decisions to default behaviour.

### Q: How does the integration handle negative prices?

**A:** Negative prices are treated as "very low" and will trigger charging recommendations. The price position calculation handles negative values correctly.

### Q: What happens if Nord Pool data is unavailable?

**A:** The integration falls back to safe defaults (no charging) and sets `binary_sensor.electricity_planner_data_availability` to `off`. Check `sensor.electricity_planner_data_unavailable_duration` for how long data has been missing.

### Q: Can I use multiple instances for different systems?

**A:** Currently, only one instance per Home Assistant installation is supported. Multi-instance support may be added in future versions.

### Q: How do I migrate from an older version?

**A:** Migrations are automatic. The integration detects your config version and upgrades through each version (v1→v2→...→v11). Check logs for migration messages.

### Q: Why does car charging have hysteresis but battery charging doesn't?

**A:** EV chargers often have minimum session requirements and frequent on/off cycling can damage equipment or confuse the vehicle. Batteries typically handle rapid state changes better.

---

## 15. Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Write tests for new functionality
4. Ensure all tests pass
5. Submit a pull request

### Reporting Issues

When reporting issues, please include:
- Home Assistant version
- Integration version (check `manifest.json`)
- Relevant log entries (with debug logging enabled)
- Configuration (anonymized)
- Steps to reproduce

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Acknowledgements

- [Nord Pool](https://www.nordpoolgroup.com/) for electricity market data
- [Home Assistant](https://www.home-assistant.io/) community
- All contributors and testers