# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration called "Electricity Planner" - a smart electricity market optimization system that provides intelligent charging decisions for batteries and electric vehicles. The integration analyzes Nord Pool pricing, battery status, and solar production to make **boolean recommendations** for when to charge from the grid.

## Architecture

### Core Components

- **Decision Engine** (`decision_engine.py`): Multi-factor algorithm that evaluates price positioning, battery status, and live solar production
  - 29 methods for comprehensive decision logic
  - 17 validation checks for data integrity
  - Uses strategy pattern for extensible decision making
- **Strategies** (`strategies.py`): 8 strategy classes implementing different charging scenarios
  - EmergencyChargingStrategy, SolarPriorityStrategy, VeryLowPriceStrategy, etc.
  - Clean separation of concerns for each decision type
- **Coordinator** (`coordinator.py`): Real-time data coordination with 30-second updates and state change triggers
  - Provides normalized access to all configuration and sensor data
  - Manages update cycles and entity state tracking
- **Config Flow** (`config_flow.py`): UI-based configuration for entity selection and thresholds
  - 5-step setup wizard (entities, thresholds, limits, solar, finalize)
  - Version 5 config schema with migration support
- **Migrations** (`migrations.py`): Handles configuration upgrades from v1→v5
  - Automatic migration on integration load
  - Safe removal of deprecated options
- **Sensors** (`sensor.py`): Comprehensive analysis sensors
  - Battery analysis, price analysis, power analysis
  - Decision diagnostics with full validation data
  - Threshold visibility sensors for monitoring
- **Binary Sensors** (`binary_sensor.py`): Main boolean outputs for charging decisions
  - battery_grid_charging and car_grid_charging with detailed reasons

### Key Decision Logic

The integration uses **price positioning** within daily range (0-100%) rather than simple thresholds:
- Very low prices: Bottom 30% of daily range → Charge recommended
- Solar surplus: Always preferred over grid charging for batteries
- Battery SOC: Different logic for batteries above/below 30% SOC
- Price trends: Considers next hour price improvements for timing

### Entity Dependencies

**Required Nord Pool entities** (4 price entities):
- Current price, highest price today, lowest price today, next hour price

**Battery entities**: 
- Battery SOC sensors (%) - supports multiple batteries
- Battery capacity sensors (kWh) - optional but recommended

**Power flow entities**:
- House consumption (W)
- Solar surplus = production - consumption (W)

## Development Commands

This is a Home Assistant custom integration - no build system or tests are present. Development workflow:

1. **Install in Home Assistant**: Copy `custom_components/electricity_planner/` to HA config
2. **Restart Home Assistant**: Required after code changes
3. **Reload integration**: Through HA UI for configuration changes
4. **Debug logging**: Add to `configuration.yaml`:
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
├── decision_engine.py   # Core charging decision algorithms (4538 lines)
├── strategies.py        # Decision strategies (8 strategy classes)
├── config_flow.py       # UI configuration flow (5-step wizard, version 5)
├── sensor.py           # Analysis sensors (price, battery, power, diagnostics)
├── binary_sensor.py    # Main boolean outputs for charging decisions
├── migrations.py       # Configuration migration system (v1→v5)
├── manifest.json       # Integration metadata and dependencies (v2.3.0)
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
- **Integration Version**: 2.3.0
- **Config Schema Version**: 5
- **Migration Path**: Automatic v1→v2→v3→v4→v5 migration

### Configuration Categories
1. **Entity Selection**: Nord Pool, battery, solar, car, power flow entities
2. **SOC Thresholds**: Min/max SOC, emergency overrides, predictive logic thresholds
3. **Price Thresholds**: Price threshold, very low price %, feed-in threshold
4. **Power Limits**: Max battery/car/grid power, charging thresholds
5. **Solar Parameters**: Significant solar surplus threshold configuration

### Recent Changes (v2.3.0)
- **Removed**: `grid_battery_charging_limit_soc` (unused config option)
- **Added**: Threshold visibility sensors for monitoring
- **Fixed**: Grid setpoint calculation to account for battery power
- **Fixed**: Solar surplus calculation to prevent negative values
- **Added**: Car charging restriction logic (1.4kW limit when not allowed)
