# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Home Assistant custom integration called "Electricity Planner" - a Belgian electricity market optimization system that provides intelligent charging decisions for batteries and electric vehicles. The integration analyzes Nord Pool pricing, battery status, and solar production to make **boolean recommendations** for when to charge from the grid.

## Architecture

### Core Components

- **Decision Engine** (`decision_engine.py`): Multi-factor algorithm that evaluates price positioning, battery status, and solar forecasts
- **Coordinator** (`coordinator.py`): Real-time data coordination with 30-second updates and state change triggers  
- **Config Flow** (`config_flow.py`): UI-based configuration for entity selection and thresholds
- **Sensors** (`sensor.py`): Comprehensive analysis sensors (price, battery, power flow)
- **Binary Sensors** (`binary_sensor.py`): Main boolean outputs for charging decisions

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
├── const.py             # Constants and configuration keys
├── coordinator.py       # Data coordination and state management
├── decision_engine.py   # Core charging decision algorithms
├── config_flow.py       # UI configuration flow
├── sensor.py           # Analysis sensors (price, battery, power)
├── binary_sensor.py    # Main boolean outputs for charging decisions
├── manifest.json       # Integration metadata and dependencies
└── strings.json        # UI text and translations
```

## Key Integration Points

- **HACS**: Configured in `hacs.json` for custom repository installation
- **Home Assistant Core**: Minimum version 2024.4.0 (specified in `hacs.json`)
- **Dependencies**: Only `aiohttp>=3.8.0` (specified in `manifest.json`)
- **Platforms**: Registers both `sensor` and `binary_sensor` platforms

## Belgian Market Focus

The integration is specifically designed for Belgian electricity markets:
- Dynamic Nord Pool pricing integration
- Solar feed-in tariff considerations  
- Regulatory compliance for residential battery storage
- Peak shaving during high-price periods

## Important Code Patterns

- All price values are handled in €/kWh
- Power values are in Watts (W)
- Battery SOC values are percentages (0-100)
- Updates trigger on entity state changes + 30-second intervals
- Error handling returns safe "False" charging decisions when data unavailable
- Comprehensive logging with decision reasoning for troubleshooting