# Architecture

The integration follows the standard Home Assistant **coordinator + entity platforms** pattern, with a dedicated decision engine producing structured output for each tick.

## Top-level flow

```
HA sensors (prices, PV, SOC, load, forecast)
        │
        ▼
   Coordinator (coordinator.py)
        │  gathers inputs, caches forecasts, validates state
        ▼
  Decision Engine (decision_engine.py)
        │  runs strategy chain, applies overrides, computes
        │  charger limit / grid setpoint / feed-in mode
        ▼
   Entity Platforms (sensor / binary_sensor / number / switch)
        │
        ▼
       HA states / dashboards / automations
```

## File map (selected)

| Area              | File                                 | Responsibility                            |
| ----------------- | ------------------------------------ | ----------------------------------------- |
| Coordinator       | `coordinator.py`                     | Polling, input gathering, state cache     |
| Decision engine   | `decision_engine.py`                 | Orchestrates strategies, emits decisions  |
| Strategies        | `arbitrage_mode.py`, `negative_buy.py`, `battery_charging.py`, … | Individual decision policies |
| Pricing           | `nordpool_service.py`, `dynamic_threshold.py` | Price ingestion + threshold logic |
| Forecasting       | `forecast_summary.py`                | Solar forecast caching + bounds checks    |
| Limits            | `charger_limit.py`, `inverter_derating.py`, `grid_setpoint.py` | Power allocation math |
| Overrides         | `manual_overrides.py`, `entity_status.py` | User-driven overrides + status surfacing |
| Platforms         | `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py` | HA entity definitions |
| Diagnostics       | `diagnostics.py`                     | Full-state dump for support               |
| Config flow       | `config_flow.py`, `migrations.py`    | UI setup + version migration              |

## Data lifecycle

1. **Tick** — Coordinator polls HA states at the configured interval.
2. **Gather** — Validated inputs flow into a typed snapshot.
3. **Decide** — Decision engine runs the strategy chain over the snapshot.
4. **Emit** — Entity platforms read the resulting decision blob and update states.
5. **Trace** — Diagnostics retain the latest decision rationale for support.
