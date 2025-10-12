# Electricity Planner

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/emavap/electricity_planner.svg)](https://github.com/emavap/electricity_planner/releases/)
[![License](https://img.shields.io/github/license/emavap/electricity_planner.svg)](LICENSE)

Smart charging decisions for batteries and electric vehicles inside Home Assistant. Electricity Planner ingests live Nord Pool pricing, solar production, battery state-of-charge and forecast data, then issues boolean recommendations, power limits and grid setpoints you can automate on. Every decision is accompanied by plain‑language reasoning so you can trust—and troubleshoot—the outcome.

---

## 1. Highlights

- **Battery-first philosophy** – multiple batteries are monitored simultaneously, prioritising SOC recovery and respecting per-device power limits.
- **EV-ready** – plans car charging from the grid only when prices are attractive, but still allows solar surplus to top up the car once all batteries are nearly full.
- **Dynamic pricing intelligence** – optional adaptive thresholding (see [Dynamic Threshold](DYNAMIC_THRESHOLD.md)) finds the best price windows instead of charging at the first “cheap” slot.
- **Contract-aware pricing** – configure multiplier/offset adjustments to mirror local tariffs (e.g. Belgian Belpex formulas for both consumption and feed-in).
- **Rich diagnostics** – sensors expose raw analyses, chosen strategies, safety caps, and human-readable reasons for each decision.
- **Home Assistant native** – configuration, options and diagnostics live entirely in the UI; no YAML required.

---

## 2. How the System Thinks

### Data Inputs

| Category | What We Read | Why It Matters |
|----------|--------------|----------------|
| Prices | Current, highest, lowest and next-hour Nord Pool entities | Drives low/very-low price logic and dynamic thresholds |
| Batteries | SOC (%) for one or more packs, optional capacity | Determines energy deficit, emergency overrides and solar eligibility |
| Power Flow | Solar production, house consumption, car charging power | Measures surplus vs deficit and tracks existing EV usage |
| Forecasts | Hourly, daily or remaining-day solar forecasts (optional) | Adjusts confidence in buying power now vs waiting for sun |
| Grid Limits | Monthly peak (optional), base grid setpoint, safe maximums | Keeps charger limits and grid draw inside contractual caps |

### Decision Pipeline

1. **Validation** – sanitize inputs, detect missing data, and infer safe defaults.
2. **Price analysis** – compute price position, trend, volatility and dynamic confidence.
3. **Battery & solar analysis** – map SOC ranges, deficit, forecast quality and production surplus.
4. **Solar allocation** – hierarchically assign surplus to current car demand, battery charging, and finally additional car charging (only when every battery is near full).
5. **Strategy evaluation** – ordered strategies decide whether grid charging is justified for batteries and car.
6. **Safety outputs** – derive charger limits, grid setpoints and feed-in toggles consistent with the decisions.
7. **Diagnostics** – publish boolean decisions, supporting numbers and full reasoning strings.

### Entities Produced

- `binary_sensor.electricity_planner_battery_grid_charging`
- `binary_sensor.electricity_planner_car_grid_charging`
- `sensor.electricity_planner_decision_diagnostics` (attributes mirror every analysis layer)
- `number.electricity_planner_charger_limit` *(if configured)*
- `number.electricity_planner_grid_setpoint` *(if configured)*

Use these entities directly in automations or dashboards—see [DASHBOARD.md](DASHBOARD.md) for ready-to-use Lovelace ideas.

---

## 3. Installation

### Option A – HACS (recommended)
1. Open Home Assistant → HACS → Integrations.
2. Add custom repository `https://github.com/emavap/electricity_planner` (category: Integration).
3. Install *Electricity Planner* and restart Home Assistant.
4. Settings → Devices & Services → Add Integration → search for “Electricity Planner”.

### Option B – Manual
1. Download the latest release archive.
2. Copy `custom_components/electricity_planner` to your Home Assistant `custom_components` directory.
3. Restart Home Assistant and add the integration from the UI.

---

## 4. Configuration Guide

### Required Entities

- Nord Pool current, highest, lowest and next-hour price sensors.
- At least one battery SOC sensor (add capacities for weighted averages if available).
- Solar production and total house consumption sensors.

### Optional but Recommended

- Car charging power sensor.
- Solar forecast entities (current hour, next hour, remaining today, tomorrow).
- Monthly grid peak sensor for demand-based contracts.

### Key Options (default values shown)

| Setting | Default | Purpose |
|---------|---------|---------|
| `min_soc_threshold` | 20 % | Lower bound for routine battery charging decisions |
| `max_soc_threshold` | 90 % | Target SOC ceiling used before diverting solar elsewhere |
| `price_threshold` | 0.15 €/kWh | Never charge from grid above this price |
| `price_adjustment_multiplier` | 1.0 | Multiplier applied before decisions (set 1.12 for the Belgian formula) |
| `price_adjustment_offset` | 0.000 €/kWh | Fixed offset added after the multiplier (0.008 for 0.8 c€/kWh) |
| `very_low_price_threshold` | 30 % | “Bottom X% of day” that always triggers charging |
| `max_battery_power` | 3000 W | Caps grid allocation for batteries |
| `max_car_power` | 11000 W | Caps combined solar + grid allocation for the car |
| `max_grid_power` | 15000 W | Ultimate safety limit for grid draw |
| `min_car_charging_threshold` | 100 W | Filters out noise when detecting active EV charging |
| `emergency_soc_threshold` | 15 % | Force-charge batteries even at high prices below this level |
| `solar_peak_emergency_soc` | 25 % | Allow charging during solar peak only if SOC is below this |
| `use_dynamic_threshold` | false | Enables the adaptive price logic |
| `dynamic_threshold_confidence` | 60 % | Baseline “confidence” required before charging (see below) |
| `feedin_price_threshold` | 0.05 €/kWh | Legacy fallback when no feed-in adjustment is set |
| `feedin_adjustment_multiplier` | 1.0 | Multiplier applied to raw price for feed-in (0.70 for the Belgian formula) |
| `feedin_adjustment_offset` | 0.000 €/kWh | Offset added to feed-in price (-0.010 for the Belgian formula) |

Re-run the integration’s **Configure** flow anytime to adjust these values without restarting Home Assistant.

---

## 5. Price Intelligence

Electricity Planner supports two price modes:

1. **Simple threshold** – charge whenever the current price is ≤ `price_threshold`.
2. **Dynamic threshold** *(optional)* – treat `price_threshold` as a ceiling, then:
   - score how attractive the current price is within today’s range,
   - account for next-hour improvements,
   - tighten or relax confidence based on SOC and solar forecast.

The dynamic mode is described in depth in [DYNAMIC_THRESHOLD.md](DYNAMIC_THRESHOLD.md). It is disabled by default but highly recommended once you are comfortable with the integration.

Both modes work on the *adjusted* price if you provide a multiplier/offset. Leave the defaults (`1.0` / `0.0`) to use the raw feed from Nord Pool, or enter your contract’s coefficients to model grid fees transparently.

### Contract Example (Belgium)

The Flemish supplier formula provided in Dutch translates to:

- Consumption: `(1.12 × Belpex + 0.8) c€/kWh` → set `price_adjustment_multiplier = 1.12` and `price_adjustment_offset = 0.008` (because 0.8 c€ = €0.008).
- Feed-in: `(0.70 × Belpex – 1) c€/kWh` → set `feedin_adjustment_multiplier = 0.70` and `feedin_adjustment_offset = -0.010`.

With those values the integration works directly with the net €/kWh rates. The feed-in threshold becomes redundant: the planner will export solar only when the adjusted price is positive.

---

## 6. Solar Allocation Rules

Solar surplus is conserved using a strict priority list:

1. **Maintain current EV draw** – if the car is already charging above the minimum threshold, preserve that power from solar first.
2. **Recharge batteries** – while any battery is below `max_soc_threshold - soc_safety_margin`, solar is reserved for them.
3. **Bonus energy for the car** – only when *every* battery reports a SOC above the near-full buffer (and none are lagging) does the car receive extra solar.
4. **Remaining surplus** – exported or curtailed, and optionally used to enable feed-in.

This approach keeps batteries as the primary reserve while still allowing the car to benefit from excess generation once storage is effectively full.

---

## 7. Decision Logic Deep Dive

### Battery Grid Charging

Strategies are evaluated in priority order until one returns a decision:

1. **Emergency** – SOC below `emergency_soc_threshold` forces charging regardless of price.
2. **Solar priority** – if solar allocation already covers needs, grid charging is suspended.
3. **Very low price** – bottom `very_low_price_threshold`% of the day triggers charging.
4. **Dynamic price** – respects price confidence, future trends and SOC/forecast adjustments.
5. **Predictive charging** – can delay if a significantly better price is imminent.
6. **SOC-based safety nets** – ensures batteries recover when low or solar outlook is poor.

If none of the strategies authorise charging and prices exceed the configured ceiling, the integration clearly states that grid charging is blocked for cost reasons.

### Car Grid Charging

1. **Check price data availability** – missing prices disable grid charging for safety.
2. **Very low prices** – always allow charging, even without solar.
3. **Low prices** – allow grid + solar; the reason string highlights the current price and threshold.
4. **High prices** – deny grid charging; if solar is available and batteries are already full, the car can continue on solar-only mode (`car_solar_only = True`).
5. **Solar bonus** – extra solar is only announced when all batteries are nearly full.

### Charger Limit & Grid Setpoint

- **Charger limit** – caps EVSE power based on the decision outcome, battery SOC, available solar and configured limits. When car grid charging is denied, the limit defaults to 1.4 kW to avoid high-power draw.
- **Grid setpoint** – allocates grid power between batteries and car within the allowed maximums and any detected monthly peak.

### Solar Feed-in

Feed-in is enabled when there is remaining solar surplus and the current price meets or exceeds the configured feed-in threshold.

---

## 8. Diagnostics & Observability

`sensor.electricity_planner_decision_diagnostics` is your inspection hub. Key attribute groups include:

- `decisions` – boolean outcomes with human-readable explanations.
- `price_analysis` – current, next-hour, highest/lowest prices, price position and dynamic confidence.
- `battery_analysis` – average/min SOC, thresholds, remaining capacity.
- `power_allocation` – solar distribution across batteries, car and export.
- `solar_forecast` – chosen forecast path, production factor and textual reason.
- `time_context` – flags for night, solar peak and evening periods.

For troubleshooting tips see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

---

## 9. Example Scenarios

| Situation | Outcome | Rationale |
|-----------|---------|-----------|
| **Price spike** – 0.22 €/kWh with SOC at 40% | ❌ Battery, ❌ Car | “Price 0.220 €/kWh exceeds threshold 0.150 €/kWh” |
| **Dynamic wait** – 0.12 €/kWh now, 0.08 €/kWh next hour, SOC 65% | ❌ Battery | “Price improving next hour (0.080 €/kWh) – waiting for better price” |
| **Emergency low SOC** – SOC 12%, price 0.25 €/kWh | ✅ Battery | “Emergency charge – SOC 12% < 15% threshold” |
| **Solar bonus for car** – Batteries at 92%, 4 kW surplus | ✅ Car (solar only) | “Batteries near full – allocating surplus 4000 W to car, no grid draw” |
| **Night bargain** – Price 0.07 €/kWh, SOC 55% | ✅ Battery, ✅ Car | “Very low price – within bottom 30% of day” |

---

## 10. Dashboards & Automation Ideas

- Use the binary sensors to trigger EVSE or battery inverter services.
- Display diagnostics using the examples in [DASHBOARD.md](DASHBOARD.md).
- Combine with adaptive charging services to modulate car power according to `number.electricity_planner_charger_limit`.

---

## 11. Development & Testing

Run the full test suite—including the dockerised environment we ship—in the project root:

```bash
docker build -f Dockerfile.tests -t electricity-planner-tests .
docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest
```

Tests cover the decision engine, solar allocation rules and strategy manager behaviour. Feel free to extend them alongside contributions.

---

## 12. Support & License

- Troubleshooting checklist: [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
- Dynamic threshold deep dive: [DYNAMIC_THRESHOLD.md](DYNAMIC_THRESHOLD.md)
- Dashboard examples: [DASHBOARD.md](DASHBOARD.md)
- Changelog: [CHANGELOG.md](CHANGELOG.md)

Released under the [MIT License](LICENSE). Contributions are welcome—please open a draft PR or discussion before large changes to align on approach.
