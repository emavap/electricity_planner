# Electricity Planner – Project Summary

**Version 4.7.1** | **Config Schema Version 11** | **Home Assistant 2024.4+**

A Home Assistant custom integration that analyses live Nord Pool prices, battery SOC, and solar production to recommend when you should charge from the grid. It never controls hardware directly—instead it exposes boolean decisions, grid power limits, and human-readable reasons that you wire into your own automations.

## Key Features

- **Multi-battery support** with capacity-weighted SOC averaging and per-battery configuration
- **Separate decisions** for home batteries and electric vehicles with distinct logic
- **Six charging strategies** in priority order: Solar Priority, Predictive, Very Low Price, SOC Buffer, Dynamic Price, SOC-Based
- **Three-phase support** for installations with phase-specific batteries or inverters
- **Dynamic threshold mode** with volatility-aware confidence scoring
- **24-hour average threshold** using rolling Nord Pool price data
- **SOC-based price relaxation** that accepts higher prices when battery is critically low
- **Car charging hysteresis** with minimum window validation and threshold floor pattern
- **Permissive mode switch** for temporarily relaxed car charging thresholds
- **Manual override services** to force or block charging for configurable durations
- **Contract-specific pricing** with multiplier/offset adjustments for consumption and feed-in
- **Comprehensive diagnostics** with 19 sensors and 6 binary sensors

## Entities Overview

| Category | Count | Examples |
|----------|-------|----------|
| Binary Sensors (Automation) | 3 | Battery/Car Grid Charging, Feed-in Solar |
| Binary Sensors (Diagnostic) | 3 | Low Price, Solar Production, Data Availability |
| Sensors (Automation) | 2 | Car Charger Limit, Grid Setpoint |
| Sensors (Diagnostic) | 17 | Decision Diagnostics, Price/Threshold sensors, SOC Average |
| Switch | 1 | Car Permissive Mode |
| Services | 2 | Set/Clear Manual Override |

## Documentation

- [README](README.md) – Complete documentation with installation, configuration, decision pipeline, all entities, automation examples, and troubleshooting
- [DASHBOARD](DASHBOARD.md) – Lovelace visualisation examples (ApexCharts, Gauge Card Pro, Button Card)
- [CHANGELOG](CHANGELOG.md) – Release notes and migration history
- [CLAUDE](CLAUDE.md) – Developer guide for AI coding assistants

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
