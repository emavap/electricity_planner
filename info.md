# Electricity Planner – Project Summary

This is a Home Assistant custom integration that analyses live Nord Pool prices, battery SOC, solar production and forecast data to recommend when you should charge from the grid. It never controls hardware directly; instead it exposes boolean decisions, grid power limits and human-readable reasons that you can feed into your own automations.

## Quick Facts

- Supports **multiple batteries** at once, with emergency overrides and SOC-aware safety limits.
- Produces **separate decisions** for home batteries and electric vehicles, keeping batteries first in line while allowing solar surplus to top up cars only when storage is already near full.
- Optional **dynamic price threshold** mode helps you target the most economical hours inside your maximum price ceiling.
- Publishes a **Decision Diagnostics sensor** with the full analysis, so you can see exactly why a recommendation changed.

## Documentation Map

- [README](README.md) – full installation, configuration and decision logic.
- [DYNAMIC_THRESHOLD](DYNAMIC_THRESHOLD.md) – deep dive into the adaptive price model.
- [DASHBOARD](DASHBOARD.md) – Lovelace visualisation ideas.
- [TROUBLESHOOTING](TROUBLESHOOTING.md) – checklist for common issues.

To install, use HACS or copy the `custom_components/electricity_planner` directory into Home Assistant, then configure the integration via the UI. Once configured, observe the binary sensors and diagnostics attributes to drive your automations. For contribution guidelines and test instructions, see the README.
