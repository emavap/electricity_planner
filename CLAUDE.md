# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration called "Electricity Planner" - a smart electricity market optimization system that provides intelligent charging decisions for batteries and electric vehicles. The integration analyzes Nord Pool pricing, battery status, and solar production to make **boolean recommendations** for when to charge from the grid.

## Architecture

### Core Components

- **Decision Engine** (`decision_engine.py`): Multi-factor algorithm that evaluates price positioning, battery status, and live solar production
  - 29+ methods for comprehensive decision logic
  - Extensive validation checks for data integrity and safety
  - Uses strategy pattern for extensible decision making
- **Strategies** (`strategies.py`): 8 strategy classes implementing different charging scenarios
  - EmergencyChargingStrategy, SolarPriorityStrategy, VeryLowPriceStrategy, etc.
  - Clean separation of concerns for each decision type
- **Coordinator** (`coordinator.py`): Real-time data coordination with 30-second updates and state change triggers
  - Provides normalized access to all configuration and sensor data
  - Manages update cycles and entity state tracking
- **Config Flow** (`config_flow.py`): UI-based configuration for entity selection and thresholds
  - Multi-step setup wizard (entities → capacities → thresholds → safety limits)
  - Options flow defaults merge `entry.data` + `entry.options` so users always see their latest selections
  - Version 13 config schema with migration support
- **Migrations** (`migrations.py`): Handles configuration upgrades from v1→v13
  - Automatic migration on integration load
  - Safe removal of deprecated options
- **Sensors** (`sensor.py`): Comprehensive analysis sensors
  - Battery analysis, price analysis, power analysis
  - Decision diagnostics with full validation data
  - Threshold visibility sensors for monitoring
- **Binary Sensors** (`binary_sensor.py`): Main boolean outputs for charging decisions
  - `battery_grid_charging` and `car_grid_charging` with detailed reasons
  - Feed-in sensor attributes sourced from the coordinator’s merged configuration, keeping thresholds aligned with live options

### Key Decision Logic

The integration uses **price positioning** within daily range (0-100%) rather than simple thresholds:
- Very low prices: Bottom 30% of daily range → Charge recommended
- Solar surplus: Always preferred over grid charging for batteries
- Battery SOC: Different logic for batteries above/below 30% SOC
- Price trends: Considers next hour price improvements for timing

### Average Threshold Calculation

When dynamic threshold mode is enabled, the integration calculates a 24-hour rolling average:
- **Minimum window**: Forces at least 24 hours of data for stable threshold
- **Future preference**: Uses future Nord Pool prices when available
- **Past backfill**: When future < 24h, backfills with recent past prices
- **Interval detection**: Automatically detects 15-min or 1-hour intervals from data
- **Graceful degradation**: Falls back to future-only when insufficient past data
- **Implementation**: `coordinator.py:377-643` (`_calculate_average_threshold`)

This prevents late-evening threshold spikes from sparse data and provides more stable charging decisions, especially beneficial for batteries.

### Car Charging Hysteresis

The car charging logic implements strict hysteresis to prevent short charging cycles:

**OFF → ON Transition:**
- Current price must be below threshold
- `_check_minimum_charging_window()` validates next N hours (configurable via `CONF_MIN_CAR_CHARGING_DURATION`, default 2h)
- Method performs:
  1. Automatic interval resolution detection (finds smallest interval spacing, typically 15min or 1h)
  2. Timeline building with explicit start/end times for all intervals
  3. Current interval identification (interval containing NOW)
  4. Continuous low-price accumulation from NOW forward
  5. Gap detection (5-second tolerance) - any gap breaks the window
  6. Returns True only if accumulated duration ≥ configured minimum

**ON → OFF Transition:**
- Immediately when current price exceeds threshold
- No window check needed

**Threshold Floor Pattern:**
- Locks price threshold when charging starts (OFF→ON)
- During active charging: uses `max(locked_threshold, current_threshold)` as effective threshold
- Prevents threshold decreases from stopping mid-session (e.g., when 24h rolling average drops)
- Allows threshold increases to take effect immediately
- Clears lock when charging stops (ON→OFF)
- Critical for charging continuity when using dynamic thresholds

**Implementation:**
- Window validation: `coordinator.py:528-817` (`_check_minimum_charging_window`)
- Threshold floor: `decision_engine.py:808-819` (threshold floor logic), `coordinator.py:250` (state storage)

### Sunny Day Feature (v4.10.0+)

Reduces the grid-charging Max SOC threshold when high solar production is forecast, preserving battery capacity for free solar energy.

**How it works:**
- `_resolve_solar_forecast()` in `coordinator.py` reads `energy_production_tomorrow` after the configurable start hour (default 20:00) and caches the value hourly
- `_apply_sunny_day_grid_limit()` in `decision_engine.py` compares forecast against `total_battery_capacity / 2`
- If sunny: `max_soc_threshold` is overridden by `max_soc_threshold_sunny` for grid charging only

**Midnight flip handling:**
- After midnight, the "tomorrow" entity flips to the *next* day (wrong day for overnight decisions)
- If an optional "today" entity (`energy_production_today`) is configured, it's used live between midnight and the start hour
- Otherwise, the cached value from the previous evening is used
- If no cache and no "today" entity: returns `None` (feature safely disabled until start hour)

**Configuration:**
- `solar_forecast_entity`: Tomorrow's forecast sensor (e.g., `sensor.energy_production_tomorrow`)
- `solar_forecast_today_entity`: Today's forecast sensor (optional, recommended)
- `solar_forecast_start_hour`: Hour (12-23) to start reading forecast, default 20
- `max_soc_threshold_sunny`: Grid charging max SOC on sunny days, default 50%

**Formula:** `forecast_kwh >= (total_battery_capacity_kwh / 2)` → sunny day active

### Entity Dependencies

**Required Nord Pool entities** (4 price entities):
- Current price, highest price today, lowest price today, next hour price

**Battery entities**:
- Battery SOC sensors (%) - supports multiple batteries
- Battery capacity sensors (kWh) - optional but recommended

**Power flow entities**:
- House consumption (W)
- Solar surplus = production - consumption (W)

**Solar forecast entities** (optional):
- Energy production tomorrow (kWh) - primary forecast source
- Energy production today (kWh) - overnight validation/fallback

## Development Commands

This is a Home Assistant custom integration. Recommended development workflow:

1. **Run automated tests (preferred)** – via Docker (no local Python deps required):
   ```bash
   docker build -f Dockerfile.tests -t electricity-planner-tests .
   docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest
   ```
2. **Install in Home Assistant**: Copy `custom_components/electricity_planner/` to HA config
3. **Restart Home Assistant**: Required after code changes
4. **Reload integration**: Through HA UI for configuration changes
5. **Debug logging**: Add to `configuration.yaml`:
   ```yaml
   logger:
     logs:
       custom_components.electricity_planner: debug
   ```

## File Structure

```
custom_components/electricity_planner/
├── __init__.py           # Integration setup and platform registration
├── const.py             # Constants and configuration keys (35 CONF_ constants)
├── coordinator.py       # Data coordination and state management
├── decision_engine.py   # Core charging decision algorithms
├── strategies.py        # Decision strategies (8 strategy classes)
├── config_flow.py       # UI configuration + options flow (multi-step wizard, schema v13)
├── sensor.py           # Analysis sensors (price, battery, power, diagnostics)
├── binary_sensor.py    # Main boolean outputs for charging decisions
├── migrations.py       # Configuration migration system (v1→v13)
├── manifest.json       # Integration metadata and dependencies
└── strings.json        # UI text and translations
```

## Key Integration Points

- **HACS**: Configured in `hacs.json` for custom repository installation
- **Home Assistant Core**: Minimum version 2024.4.0 (specified in `hacs.json`)
- **Dependencies**: Only `aiohttp>=3.8.0` (specified in `manifest.json`)
- **Platforms**: Registers both `sensor` and `binary_sensor` platforms

## Dynamic Market Focus

The integration is designed for dynamic electricity markets:
- Nord Pool pricing integration (and similar dynamic pricing markets)
- Solar feed-in tariff considerations and export optimization
- Configurable safety limits for residential battery storage
- Peak shaving during high-price periods

## Important Code Patterns

- All price values are handled in €/kWh
- Power values are in Watts (W)
- Battery SOC values are percentages (0-100)
- Updates trigger on entity state changes + 30-second intervals
- Error handling returns safe "False" charging decisions when data unavailable
- Comprehensive logging with decision reasoning for troubleshooting
- Binary sensors expose decision reasons in their attributes
- Diagnostic sensors provide complete validation data

## Configuration System

### Current Version
- **Integration Version**: 4.10.3
- **Config Schema Version**: 13
- **Migration Path**: Automatic v1→v13 migration

### Configuration Categories
1. **Entity Selection**: Nord Pool, battery, solar, car, power flow, solar forecast entities
2. **SOC Thresholds**: Min/max SOC, sunny day max SOC, emergency overrides, predictive logic thresholds
3. **Price Thresholds**: Price threshold, very low price %, feed-in threshold
4. **Power Limits**: Max battery/car/grid power, charging thresholds
5. **Solar Parameters**: Significant solar surplus threshold, forecast start hour

### Recent Changes (v4.10.x)
- **Added**: Sunny day feature — reduces grid charging max SOC when solar forecast is high
- **Added**: Solar forecast caching with configurable start hour (12-23, default 20)
- **Added**: Optional "today" forecast entity for overnight validation
- **Added**: 187 pytest tests with comprehensive coverage for forecast and sunny day logic
- **Fixed**: Silent wrong-day fallback bug in forecast resolution after midnight
- **Fixed**: ~30 missing UI translation keys across config and options flows
