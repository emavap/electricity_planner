# Electricity Planner

Electricity Planner is a Home Assistant custom integration that turns Nord Pool market data and your home telemetry into actionable, plain‑language automation signals. It never drives hardware directly. Instead, it delivers boolean decisions, grid power limits and diagnostics you can wire into your own battery, inverter and EV workflows.

---

## 1. Quick Start

### Requirements

- Home Assistant 2024.4+
- Nord Pool integration providing:
  - `current_price`
  - `highest_price_today`
  - `lowest_price_today`
  - `next_hour_price`
- At least one battery SOC sensor (percentage, optional capacity)
- Solar production (W) and house consumption (W) sensors
- Optional but recommended:
  - EV charging power sensor (W)
  - Monthly grid peak entity (W)

### Installation

| Method | Steps |
|--------|-------|
| **HACS** | 1. HACS → Integrations → `+` → Custom repository `https://github.com/emavap/electricity_planner` (Integration)<br>2. Install the integration<br>3. Restart Home Assistant |
| **Manual** | 1. Download the latest release archive<br>2. Copy `custom_components/electricity_planner/` into `<config>/custom_components/`<br>3. Restart Home Assistant |

### First-Time Configuration

1. Settings → Devices & Services → `+ Add Integration` → search for **Electricity Planner**.
2. Wizard steps:
   - **Entities** – select Nord Pool, battery, solar, consumption, EV sensors.
   - **SOC Thresholds** – min/max SOC, emergency threshold, predictive threshold.
   - **Price Thresholds** – static ceiling, very-low-price %, feed‑in limits.
   - **Power Limits** – max battery/car/grid power, minimum EV detection threshold.
   - **Solar Parameters** – significant surplus threshold, solar-peak SOC override.
3. Save and allow the coordinator to populate sensors (10–30 seconds).

### Entities Exposed

| Entity | Purpose |
|--------|---------|
| `binary_sensor.electricity_planner_battery_grid_charging` | “Can batteries charge from grid now?” (reason attribute) |
| `binary_sensor.electricity_planner_car_grid_charging`     | “Can the EV draw grid energy now?” (reason attribute) |
| `sensor.electricity_planner_decision_diagnostics`         | Full analysis context in attributes |
| `sensor.electricity_planner_battery_soc_average`          | Optional automation helper |
| `sensor.electricity_planner_grid_setpoint` / `number.electricity_planner_grid_setpoint` | Suggested grid power limit (if enabled) |
| `sensor.electricity_planner_car_charger_limit` / `number.electricity_planner_car_charger_limit` | Recommended EVSE limit |
| Diagnostic sensors (current price, feed-in price, thresholds, solar surplus, etc.) | Visualisation and troubleshooting |

---

## 2. How It Thinks – Decision Pipeline

1. **Data validation** – ensure every configured entity is available and convertible to numbers; fall back to safe defaults where possible.
2. **Price analysis** – compute position inside the daily range, volatility, next-hour trend, “very low” percentage band, and optionally the dynamic confidence score.
3. **Battery analysis** – capacity-weighted SOC stats, remaining headroom, minimum/maximum thresholds, emergency detection.
4. **Power analysis** – real-time solar production, house consumption, solar surplus and EV draw; flag “significant” surplus once it exceeds the configured watt threshold.
5. **Solar allocation** – reserve surplus for the EV’s current draw, then batteries, then bonus EV power once every battery is near its target SOC. Allocation never exceeds the measured surplus.
6. **Strategy evaluation** – ordered strategy set (Emergency, Solar priority, Very low price, Dynamic pricing, Predictive wait, Solar-aware, SOC safety nets) returns “charge” or “wait” with a human-readable reason.
7. **Car decision logic** – applies hysteresis for OFF→ON transitions only:
   - **OFF → ON**: Requires current price below threshold AND next N hours (configurable, default 2h) all below threshold
   - **ON → OFF**: Immediately when current price exceeds threshold
   - Uses exact interval resolution detection (15-min or hourly) with gap detection
   - Very-low prices still require the minimum window before starting
8. **Feed-in & safety outputs** – compute charger limit, grid setpoint, and whether to export surplus via the configured feed-in pricing model.
9. **Diagnostics** – publish every step and reason through `sensor.electricity_planner_decision_diagnostics`.

---

## 3. Pricing Models

### Static Threshold

Set `price_threshold` to your “never exceed” value (e.g. €0.15). Batteries and cars only consider grid charging beneath that ceiling.

### Dynamic Threshold (optional)

Enable `use_dynamic_threshold` to turn the static ceiling into a hard cap and let the dynamic confidence engine pick the best times inside it.

- **Inputs:** current/high/low price, next-hour price, volatility, configured confidence baseline, SOC, solar surplus.
- **Adjustments:** Low SOC relaxes confidence; high SOC tightens it. Significant solar surplus tightens confidence; no surplus relaxes it.
- **Decision:** Charge when calculated confidence ≥ adjusted requirement. Reasons include the computed confidence and SOC/solar context for transparency.

### Very-Low Price Band

`very_low_price_threshold` (default 30 %) marks the bottom slice of each day’s range. Whenever price slips into that band:

- **Batteries** always prioritize free solar over grid. If SOC ≥ 50 % and significant solar surplus exists, grid charging is blocked (even at very low prices) to preserve space for free solar.
- **EVs** charge immediately (subject to hysteresis window) regardless of surplus. Very low prices override solar preference for cars since they need more energy.

---

## 4. Solar & Battery Behaviour

| Situation | Battery decision | Car decision |
|-----------|------------------|--------------|
| **Significant surplus, SOC ≥ 50 %** | Grid charging always blocked to preserve space for free solar (even at very low prices). | Very-low prices always permitted for cars regardless of solar. |
| **Significant surplus, SOC low** | Grid used only if strategies approve (e.g. emergency) | Same as above |
| **No surplus** | Normal price/SOC strategies apply | Hysteresis + price logic |
| **Emergency SOC (< `emergency_soc_threshold`)** | Always charge, price ignored | N/A – car still obeys its own logic |
| **Solar peak (10–16 by default)** | If SOC > `solar_peak_emergency_soc`, grid charging pauses in favour of live solar | Car follows global logic; can remain in solar-only mode when flagged |

All surplus allocation comes straight from the coordinator’s measured data—no forecasts are required or considered.

---

## 5. Outputs & Monitoring

### Decision Diagnostics (`sensor.electricity_planner_decision_diagnostics`)

Key attribute groups:

- `decisions` – boolean outcomes (`battery_grid_charging`, `car_grid_charging`, `feedin_solar`) with reasons.
- `price_analysis` – current price, highest/lowest, position, volatility, dynamic confidence, hysteresis flags.
- `battery_analysis` – average/min/max SOC, thresholds, remaining capacity, availability status.
- `power_analysis` – solar production, house consumption, surplus, EV draw, significant-surplus flag.
- `power_allocation` – where surplus is assigned (battery, EV, export).
- `time_context` – night/peak/evening flags, winter detection.
- `configured_limits` – the effective power and SOC limits currently applied.

### Example Reason Strings

- `battery_grid_charging_reason`: "Significant solar surplus (2800W) available – SOC 62% ≥ 50% so waiting for free solar (even at very low prices)"
- `car_grid_charging_reason`: “Very low price (0.062€/kWh) – bottom 30% of daily range (2h+ window available)”
- `feedin_solar_reason`: “Net feed-in price 0.125€/kWh ≥ 0.050€/kWh – enable solar export (surplus: 1800W)”

### Quick Troubleshooting Checklist

| Symptom | What to inspect |
|---------|-----------------|
| Planner never charges | `price_analysis` attributes – is `data_available` false, or is `current_price > price_threshold`? |
| Batteries refuse grid charging | `battery_analysis.average_soc` and `power_analysis.significant_solar_surplus` – you may be within solar-only territory. |
| EV stops unexpectedly | `car_grid_charging_reason` – look for “less than X hours of low prices ahead” or high price threshold messages. |
| No updates | Ensure source sensors publish numeric values; check HA logs for warnings emitted by the coordinator. |

---

## 6. Automation & Dashboard Ideas

### Automation Hooks

- Use the `battery_grid_charging` and `car_grid_charging` binary sensors to trigger inverter or EVSE service calls.
- Drive power limits from `number.electricity_planner_charger_limit` / `number.electricity_planner_grid_setpoint`.
- Use `sensor.electricity_planner_decision_diagnostics` attributes in templates for advanced logic (e.g., avoid washing-machine start above a price threshold).

### Dashboard Snippets

```yaml
# Example conditional card snippet
- type: conditional
  conditions:
    - entity: binary_sensor.electricity_planner_battery_grid_charging
      state: "on"
  card:
    type: markdown
    content: >
      ### 🟢 Battery Charging Allowed
      {{ state_attr('binary_sensor.electricity_planner_battery_grid_charging', 'reason') }}
```

For a ready-made Lovelace layout, mount the Nord Pool prices and diagnostic sensors on ApexCharts stacked columns (energy price + transport cost) and highlight the decision reasons alongside.

---

## 7. Troubleshooting Reference

- **Price unavailable** – ensure Nord Pool entities update at least hourly; during brief refresh periods, dynamic pricing tolerates `None` values.
- **Transport cost jumps** – the coordinator builds a 7-day history. Until enough minutes are captured, the fallback is the live transport cost sensor.
- **Car ignores very-low prices** – check `has_min_charging_window` in diagnostics. The car will only start (OFF→ON) if the next N hours (default 2h, configurable via `min_car_charging_duration`) are all below threshold. Once charging (ON state), it continues until price exceeds threshold. Adjust `min_car_charging_duration` to require shorter/longer guaranteed windows.
- **Feed-in never triggers** – verify `remaining_solar` in `power_allocation`, and ensure your feed-in multiplier/offset produce a net value above the threshold.
- **Batteries never reach 100 %** – adjust `max_soc_threshold` upward, or reduce `significant_solar_threshold` if surplus is frequently just below the default 1 kW.

---

## 8. Testing & Development

### Running the Suite (Docker)

```bash
docker build -f Dockerfile.tests -t electricity-planner-tests .
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest
```

### Running Locally

```bash
pip install -r requirements-dev.txt
export PYTHONPATH=.
pytest
```

All tests are written to run without spinning up Home Assistant itself—the project stubs the coordinator and entity layers where necessary.

### Logging

Add the following to `configuration.yaml` for verbose logs:

```yaml
logger:
  logs:
    custom_components.electricity_planner: debug
```

---

## 9. FAQ

**Do I need solar forecasts?**  
No. The planner uses only live production/consumption values. Forecast logic has been removed to reflect real-time conditions.

**How do I change energy contract adjustments?**  
In the options flow, set `price_adjustment_multiplier` / `price_adjustment_offset` (for consumption) and `feedin_adjustment_multiplier` / `feedin_adjustment_offset` (for export). The defaults model a “no adjustment” contract.

**Why does the car ignore a solar surplus yet the batteries pause?**  
EV logic is intentionally independent—very-low market prices are an opportunity to buy energy even if solar is available. Batteries, however, avoid grid consumption when surplus and SOC ≥ 50 % to keep capacity free for solar.

**Can I disable dynamic pricing entirely?**  
Yes. Leave `use_dynamic_threshold` set to false. The integration will rely on the simple static threshold plus strategy safeguards.

---

## 10. Contributing

Pull requests and discussions are welcome. Please:

1. Fork and branch from `main`.
2. Run `pytest`.
3. Include a short description of behaviour changes or new logic in your PR summary.
4. For significant UI or API changes, update this README accordingly—this file is the single source of documentation.

---

Happy planning! Put the planner’s boolean outputs and limits into your automations, let the diagnostics beat the guesswork, and enjoy cheaper, better-aligned charging. For questions or ideas, open an issue in the repository.***
