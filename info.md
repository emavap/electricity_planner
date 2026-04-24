# Electricity Planner – Project Summary

**Version 6.0.2** | **Config Schema Version 21** | **Home Assistant 2024.4+**

A Home Assistant custom integration that analyses live Nord Pool prices, battery SOC, and solar production to recommend when you should charge from the grid. It never controls hardware directly—instead it exposes boolean decisions, grid power limits, and human-readable reasons that you wire into your own automations.

> Release note for v6.0.2: logic-review follow-up on top of v6.0.1 — five small, low-risk fixes with no user-facing behavior drift. `battery_charging.py` surplus-block gate now uses `max_soc_threshold_solar` (was hardcoded 50). `car_charging.py` arbitrage path explicitly clears `car_solar_only`. `grid_setpoint.py` safety-net logs demoted from `warning` to `info` so recoverable edges no longer surface as persistent HA notifications. `strategies.py` docstring clarifies the predictive strategy's advisory reason only surfaces in non-dynamic mode. CLAUDE.md strategy count corrected 8 → 6. No code-behavior, schema, or API changes. 456/456 tests still passing.

## Key Features

- **Multi-battery support** with capacity-weighted SOC averaging and per-battery configuration
- **Separate decisions** for home batteries and electric vehicles with distinct logic
- **Six charging strategies** in priority order: Solar Priority, Predictive, Very Low Price, SOC Buffer, Dynamic Price, SOC-Based
- **Three-phase support** for installations with phase-specific batteries or inverters
- **Dynamic threshold mode** with volatility-aware confidence scoring
- **24-hour average threshold** using rolling Nord Pool price data
- **SOC-based price relaxation** that accepts higher prices when battery is critically low
- **Car charging hysteresis** with minimum window validation and threshold floor pattern
- **Sunny-day dual SOC limits** with forecast-based kWh trigger and today/tomorrow forecast handling
- **Permissive mode switch** for temporarily relaxed car charging thresholds
- **Manual override services** to force or block charging for configurable durations
- **Contract-specific pricing** with multiplier/offset adjustments for consumption and feed-in
- **Arbitrage mode** with threshold-based export activation and dashboard threshold visibility
- **Comprehensive diagnostics** with 20 sensors and 7 binary sensors

## Entities Overview

| Category | Count | Examples |
|----------|-------|----------|
| Binary Sensors (Automation) | 4 | Battery/Car Grid Charging, Feed-in Solar, Derating Alarm |
| Binary Sensors (Diagnostic) | 3 | Low Price, Solar Production, Data Availability |
| Sensors (Automation) | 3 | Car Charger Limit, Grid Setpoint, Inverter Derating Target |
| Sensors (Diagnostic) | 17 | Decision Diagnostics, Price/Threshold sensors, SOC Average |
| Switch | 3 | Car Permissive Mode, Arbitrage Mode, Disable Battery Charging |
| Services | 2 | Set/Clear Manual Override |

## Documentation

- [README](README.md) – Complete documentation with installation, configuration, decision pipeline, all entities, automation examples, and troubleshooting
- [DASHBOARD](DASHBOARD.md) – Lovelace visualisation examples (ApexCharts, Gauge Card Pro, Button Card)
- [CLAUDE](CLAUDE.md) – Developer guide for AI coding assistants
- [Releases](https://github.com/emavap/electricity_planner/releases) – Published versions and upgrade notes

## Quick Installation

1. **HACS**: Add custom repository `https://github.com/emavap/electricity_planner` (Integration) → Install → Restart
2. **Manual**: Copy `custom_components/electricity_planner/` to `<config>/custom_components/` → Restart
3. **Configure**: Settings → Devices & Services → Add Integration → Electricity Planner

## Testing

```bash
# Docker-based pytest (no local Python deps required)
docker build -f Dockerfile.tests -t electricity-planner-tests .
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest
```

## Debug Logging

```yaml
logger:
  logs:
    custom_components.electricity_planner: debug
```
