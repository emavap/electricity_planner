# Electricity Planner

A Home Assistant custom integration that schedules **battery charging**, **car charging**, and **grid feed-in** decisions based on:

- Nord Pool day-ahead electricity prices
- Solar production forecasts
- Battery SOC and capacity
- Real-time PV/load/grid sensors
- User-configurable strategies and overrides

## Features

- **Decision engine** that emits a coherent set of binary, numeric and sensor entities every coordinator tick.
- **Strategy chain** for arbitrage, solar self-consumption, peak shaving, sunny-day adjustments, and manual overrides.
- **3-phase aware** charger limit calculation and dashboard.
- **Diagnostics** with full state dump and decision trace.
- **400+ unit tests** covering coordinator, decision engine, strategies, and dashboards.

## Quick links

- [Architecture](architecture.md) — top-level data flow and file map
- [Decision Engine](decision-engine.md) — how a single tick produces decisions
- [Strategies](strategies.md) — pluggable scoring policies
- [Development](development.md) — devcontainer, make targets, testing
