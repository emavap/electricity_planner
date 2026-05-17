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
- **Strategies** (`strategies.py`): 6 strategy classes implementing different charging scenarios
  - SolarPriorityStrategy, PredictiveChargingStrategy, VeryLowPriceStrategy, SOCBufferChargingStrategy, DynamicPriceStrategy, SOCBasedChargingStrategy
  - Emergency-SOC override is handled inline by the `PriceThresholdGuard` inside `StrategyManager.evaluate()` (no dedicated class)
  - Clean separation of concerns for each decision type
- **Coordinator** (`coordinator.py`): Real-time data coordination with 30-second updates and state change triggers
  - Provides normalized access to all configuration and sensor data
  - Manages update cycles and entity state tracking
- **Config Flow** (`config_flow.py`): UI-based configuration for entity selection and thresholds
  - Multi-step setup wizard (entities → capacities → thresholds → safety limits)
  - Options flow defaults merge `entry.data` + `entry.options` so users always see their latest selections
  - Version 20 config schema with migration support
- **Migrations** (`migrations.py`): Handles configuration upgrades from v1→v20
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

### Solar Allocation Policy (v5.0.12+)

`_allocate_solar_power()` in `decision_engine.py` splits post-house solar surplus between batteries, the EV, and exported leftover using a **car-state-aware** policy:

**Car actively charging (`car_charging_power > min_car_charging_threshold`, default 100W):**

- Batteries get a fixed reserve slice capped at `significant_solar_threshold` (default 1000W)
- Remaining surplus is offered to the EV (`car_current_solar_usage` + `solar_for_car` bonus)

**Car idle:**

- Batteries absorb the full surplus up to their demand, **uncapped** by `significant_solar_threshold`
- Any leftover triggers the solar-only **bootstrap path** (`_bootstrap_car_solar_allocation`):
  - If `batteries_full=True` or every battery's SOC is `≥ max_soc_threshold − soc_buffer` (10%), the leftover is published as `solar_for_car` so the car decision engine can enter `car_solar_only` mode
  - Otherwise the leftover becomes `remaining_solar` (available for feed-in / export)

The `allocated_car_solar = solar_for_car + car_current_solar_usage` value is exposed via `CycleContext` and consumed by `_calculate_charger_limit` as the **non-grid floor** passed to `_apply_peak_import_limit`, so solar (and battery arbitrage) portions are preserved when peak-import protection halves the grid share.

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
- `max_soc_threshold_sunny`: Grid charging max SOC on sunny days, default 35%

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

```text
custom_components/electricity_planner/
├── __init__.py           # Integration setup and platform registration
├── const.py             # Constants and configuration keys (35 CONF_ constants)
├── coordinator.py       # Data coordination and state management
├── decision_engine.py   # Core charging decision algorithms
├── strategies.py        # Decision strategies (6 strategy classes + inline PriceThresholdGuard)
├── config_flow.py       # UI configuration + options flow (multi-step wizard, schema v23)
├── sensor.py           # Analysis sensors (price, battery, power, diagnostics)
├── binary_sensor.py    # Main boolean outputs for charging decisions
├── number.py           # Live-adjust SOC/threshold/deadline number entities
├── switch.py           # Runtime mode switches (permissive, arbitrage, negative buy, disable charging)
├── arbitrage_mode.py   # Arbitrage sell planner (formerly battery_dump.py)
├── negative_buy.py     # Negative Arbitrage Buy planner (force grid-charge during negative prices)
├── migrations.py       # Configuration migration system (v1→v23)
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

- **Integration Version**: 6.6.0
- **Config Schema Version**: 23
- **Migration Path**: Automatic v1→v23 migration

### Configuration Categories

1. **Entity Selection**: Nord Pool, battery, solar, car, power flow, solar forecast entities
2. **SOC Thresholds**: Min/max SOC (grid), sunny-day max SOC (grid), **solar-specific max SOC**, emergency overrides, predictive logic thresholds, **arbitrage reserve SOC**
3. **Price Thresholds**: Price threshold, very low price %, feed-in threshold, **negative-buy threshold**
4. **Power Limits**: Max battery/car/grid power, charging thresholds, **arbitrage export cap**
5. **Solar Parameters**: Significant solar surplus threshold, forecast start hour
6. **Arbitrage Mode**: Reserve SOC, deadline hour (shared by sell + negative-buy planning), max export power

### Recent Changes

**v6.6.0** (Battery binary-sensor consistency + anti-flap hold + battery-threshold decoupling — behaviour change with persisted state)

- **Added** (`battery_charging.py`): Top-of-decision `ArbitrageExportGuard` early return. When `arbitrage_mode_export_active` is true, `BatteryChargingDecisionCalculator.decide()` now returns `battery_grid_charging=False` immediately (with a dedicated trace entry), before the Negative Arbitrage Buy override and before any other validation. The grid-setpoint logic already gave export priority over charging, but the binary sensor could still report `True` during export windows; the new guard closes that inconsistency. The previous "comment-only" suppression block has been removed.
- **Added** (`battery_charging.py:_apply_on_hold`): Five-minute anti-flap hold that mirrors the car-charging hysteresis. After an automatic OFF decision, if the previous state was ON, the state has been on for less than `_MIN_BATTERY_ON_HOLD_SECONDS` (300s), the current price is `≤` the **threshold locked at hold entry** (not the live SOC-relaxed threshold — see below), and `SolarPriorityStrategy` (priority 1 with a non-empty reason) did *not* fire, the decision is held ON for the rest of the 300s window. A `BatteryOnHold` trace entry surfaces the remaining seconds and the locked threshold value. Solar-priority detection uses `priority == 1 and reason` rather than a class-name string match so it survives renames.
- **Added** (`coordinator.py`): Three new in-memory + persisted attributes — `_previous_battery_grid_charging`, `_battery_grid_charging_changed_at`, `_battery_grid_charging_locked_threshold` — owned by a new dedicated `Store` (`<DOMAIN>.<entry_id>.battery_charging_state`, version 1). The state is loaded in `async_initialize_persistent_state` and re-persisted whenever any of the three values change (typically OFF↔ON transitions). This keeps the anti-flap hold working across HA restarts and integration reloads, matching the persistence already in place for arbitrage/runtime-mode toggles.
- **Added** (`coordinator._update_battery_charging_state_tracking`): Captures the cycle's `effective_battery_price_threshold` at the OFF→ON transition and stores it on `_battery_grid_charging_locked_threshold`. Cleared at ON→OFF and on any manual `battery_grid_charging` override (the override resets all three tracking fields). The locked threshold replaces the live `effective_battery_price_threshold` in the hold check, so SOC drift mid-session (which would shrink the SOC multiplier and the live threshold) cannot prematurely terminate the hold.
- **Added** (`decision_engine.py`): The battery decision result now exposes `battery_effective_threshold` (= `cycle_ctx.effective_battery_price_threshold`) so the coordinator has the value to lock at the OFF→ON transition.
- **Renamed** (`decision_engine.py`, `battery_charging.py`): `CycleContext.previous_battery_charging` → `previous_battery_grid_charging`, `battery_charging_state_age_seconds` → `battery_grid_charging_state_age_seconds`. Internal-key constants renamed accordingly (`_PREVIOUS_BATTERY_GRID_CHARGING_KEY`, `_BATTERY_GRID_CHARGING_STATE_AGE_SECONDS_KEY`, new `_BATTERY_GRID_CHARGING_LOCKED_THRESHOLD_KEY`). The on-the-wire data dict already used the `*_grid_*` form; this aligns the in-process plumbing.
- **Decoupled** (`strategies.py`): `PredictiveChargingStrategy`, `SOCBasedChargingStrategy`, and `SOCBufferChargingStrategy` now branch on a new `battery_is_low_price` context flag (`current_price <= base_threshold`, where `base_threshold` is the stable snapshot when present) instead of mutating `price_analysis["is_low_price"]`. The public `is_low_price` flag — read by car decisions and diagnostic sensors — keeps reflecting the configured threshold, while battery decisions get the stable-threshold view they actually need. `SOCBufferChargingStrategy.base_threshold` and `DynamicPriceStrategy.base_threshold` already used `battery_stable_threshold`; this brings the other two battery strategies into line.
- **Fixed** (`override_recalculator.py`): Manual `grid_setpoint` overrides that go negative (export) now also force `battery_grid_charging=False` on both `decision` and `combined_data`, eliminating the contradictory "charging while exporting" binary-sensor state. The check uses a true float comparison (`grid_setpoint_value < 0`) rather than `int(...) < 0`, so values in `(-1, 0)` still trigger the suppression.
- **Documentation** (`README.md`, `dashboard_template.yaml`, both bundled dashboards): Reworded the *Arbitrage Mode* description ("Grid charging is blocked while the mode is enabled unless the current price is negative" → "Grid charging remains available while the mode is armed, but is suppressed during active arbitrage export slots") and dropped the "and solar export curtailment" qualifier from the Negative Arbitrage Buy description (the v6.5.0 behavioural revert means we no longer curtail solar during paid-to-consume slots). Updated the FAQ entry on car vs battery hysteresis to describe the new short anti-flap hold and to enumerate the hard stops (missing data, full battery, active arbitrage export, solar-priority stops, manual overrides) that still take effect immediately.
- **Bumped**: `MANAGED_VERSION` `33` → `34` so existing managed dashboards re-save the corrected wording automatically on next reload.
- **Tests**: New coverage in `tests/test_decision_engine_trees.py` (anti-flap hold applies / does not apply on high price / does not apply on solar-priority stop), `tests/test_decision_engine_power.py` (active export blocks battery binary, export takes precedence over both Negative Buy and the hold, negative grid-setpoint override forces battery off, stable threshold does not mutate `is_low_price`), `tests/test_strategies_smoke.py` (low-price strategies use stable-threshold snapshot without mutating `price_analysis`), and `tests/test_coordinator.py` (state tracking captures automatic decisions, manual override resets all three tracking fields).
- **Compatibility**: No config-schema change (still v23), no migration required for `entry.data` / `entry.options`. The new `battery_charging_state` store is created on first load and self-populates from runtime state — no user action needed. Bundled-YAML users need to re-copy the dashboards to pick up the wording fix; managed-dashboard users get it automatically on next integration reload via the `MANAGED_VERSION` bump. Installations that were relying on `binary_sensor.electricity_planner_battery_grid_charging` flipping `True` during arbitrage export windows (incorrect prior behaviour) will now see it correctly stay `False` — automations should look at the *grid setpoint* sign or `switch.electricity_planner_arbitrage_mode` attributes for export status instead.

**v6.5.0** (Negative Arbitrage Buy no longer derates the inverter — behaviour change)

- **Removed** (`inverter_derating.py`): The `negative_buy_curtail_solar` early-return branch (introduced in v6.1.0) that pinned `inverter_derating_target = 0` whenever the planner flagged the current slot as paid-to-consume. Forcing the inverter to 0W during arbitrage-buy slots cut off PV that was already covering house and EV load, which is wasteful — the import we are credited for is the *net* draw, not gross consumption, so curtailing solar does not increase the credit.
- **New behaviour**: Inverter derating is now strictly reserved for the **exporting** case — the existing export-deadband control loop around `inverter_export_limit` is the only path that lowers the cap. While the site is importing (`grid_power > 0`) — including paid-to-consume Negative Arbitrage Buy slots — the inverter stays unrestricted, exactly as it did before v6.1.0. The rest of the Negative Arbitrage Buy planner is unchanged: `battery_charging.py` still force-grid-charges batteries past the SOC ceiling, and `grid_setpoint.py` still reserves the peak-limited grid import budget.
- **Tests**: 495/495 passing. The legacy assertion `test_inverter_derating_curtails_solar_when_negative_buy_active` was inverted into `test_inverter_derating_does_not_curtail_solar_when_negative_buy_active` (asserts the inverter stays at `max_inverter_power` under the same inputs that previously yielded 0W). New `test_inverter_derating_holds_during_negative_buy_when_importing` covers the importing-during-negative-buy case (`grid_power > 0`, no export → no derating engaged).
- **Compatibility**: No config-schema change (still v23), no migration required, no entity-ID renames, no service-surface or public API changes. The flag `negative_buy_curtail_solar` is still produced by the planner and exposed on the Negative Arbitrage Buy switch's attributes; only the derating side-effect was removed. Installations that depended on the inverter dropping to 0W during negative-buy slots will see the inverter stay online — disable the Negative Arbitrage Buy switch to retain v6.4.0 behaviour, or wire your own automation against the `negative_buy_mode_active` attribute if you want a hard inverter cut.

**v6.4.0** (Dashboard view-split + per-cycle hot-path optimisations — no behaviour change)

- **Dashboard restructure** (`dashboard_template.yaml`, both bundled dashboards): The single "Electricity Planner" view has been broken up into four panel views — **Overview** (status gauges, decision summary, per-decision diagnostics row), **Controls** (Battery Controls entities row, Battery Controls quick-reference, Manual override status, manual override buttons), **Prices** (ApexCharts buy/sell graphs, current price + components markdown, threshold markdown, peak/feed-in details), and **Diagnostics** (Entity Status, Algorithm thresholds). Heavy ApexCharts and large markdown blocks no longer render on the first load — Lovelace only paints the active view, so the Overview opens noticeably faster on slow / mobile clients while keeping every existing card available behind a tap. View paths (`overview` / `controls` / `prices` / `diagnostics`) are stable so cross-view links and bookmarks resolve consistently across the bundled and managed dashboards.
- **Three-phase appendix is now its own view** (`dashboard.py:_append_cards_to_primary_stack`, both bundled dashboards): When 3-phase mode is enabled the managed dashboard adds a fifth view titled **Three Phase** (`path: three-phase`, icon `mdi:transmission-tower`) instead of appending the per-phase grid to the Overview's primary stack. The bundled `electricity_planner_3phase_dashboard.yaml` mirrors the same five-view layout. The appendix's own intro markdown was rewritten — it previously read "the cards above show the shared planner state", which became false once it was promoted to its own view; it now points users to the Overview/Controls/Prices/Diagnostics views explicitly.
- **`MANAGED_VERSION` bumped 32 → 33** (`dashboard.py`) so existing managed dashboards re-save into the new view layout automatically on next reload.
- **Per-update timeline cache** (`coordinator.py`): `_build_purchase_price_timeline` and `_build_feedin_price_timeline` now memoise their output for the lifetime of one `_async_update_data` call. The cache is keyed by an `_active_timeline_cache_token` (a fresh `object()` per update cycle) plus the **identities** (`id(...)`) of the `prices_today` / `prices_tomorrow` / `transport_lookup` containers and the cycle's `now`. `DataUpdateCoordinator` swaps `coordinator.data` wholesale per cycle, so identity is a sound substitute for hashing the contents on this hot path. The cache token is set at the top of `_async_update_data` and cleared in its `finally`, so cross-cycle reads always rebuild.
- **Identity-based attribute cache** (`sensor.py:NordPoolPricesSensor`): `extra_state_attributes` now skips re-rendering its (recorder-bound) payload when the dict identities on `coordinator.data` are unchanged, which avoids re-walking the full price list and re-formatting transport-cost-stamped intervals on every state read between coordinator refreshes. `last_update` is part of the cached payload, so the dashboard only sees a fresh timestamp when the underlying data actually moves.
- **Memoised `dt_util.parse_datetime`** (`helpers.py:parse_datetime_cached` + 4 call sites): New `@lru_cache(maxsize=1024)`-backed wrapper, wired into `price_timeline.PriceTimelineBuilder`, `coordinator._build_price_analysis_overrides`, all three `threshold_calculator` passes, and both Nord Pool sensor parse sites. The same ~192 Nord Pool interval `start` / `end` strings are now parsed once per cycle instead of being re-parsed across five collaborators. Non-string inputs and `None` defer to the underlying helper to preserve original validation semantics.
- **Pre-parsed transport-lookup timestamps** (`transport_cost.py:TransportCostResolver.get_lookup`, `helpers.py:_entry_local_datetime` / `resolve_transport_cost_from_lookup`): Each lookup entry now carries a precomputed `_local: datetime` field built once when the recorder history is read. `resolve_transport_cost_from_lookup` consults `_entry_local_datetime`, which prefers the cached value and falls back to parsing only for legacy/test callers. Eliminates ~134 k `dt_util.parse_datetime` + `as_utc` + `as_local` calls per update cycle on a full 7-day, 96-entry-per-day lookup paired with a 192-interval timeline rebuild.
- **Tests**: 494/494 passing. New regression coverage in `tests/test_nordpool_sensor.py` for the attribute cache (`test_extra_state_attributes_reuses_cached_payload_until_inputs_change`, `test_extra_state_attributes_invalidates_when_data_dict_replaced`), the pre-parsed transport lookup hot path (`test_resolve_transport_cost_uses_pre_parsed_local_when_present`), and the parse-datetime memo (`test_parse_datetime_cached_skips_redundant_parses`). Dashboard contract added in `tests/test_dashboard.py` for the four-view split (`test_managed_dashboard_template_splits_heavy_cards_into_secondary_views`) and the dedicated three-phase view (`test_three_phase_appendix_merges_as_dedicated_view`).
- **Compatibility**: No config-schema change (still v23), no migration required, no entity-ID renames, no public API changes. Pure performance + UX work. Existing automations and dashboards keep working unchanged. Bundled-YAML users need to re-copy the dashboards to pick up the view split; managed-dashboard users get it automatically on next integration reload via the `MANAGED_VERSION` bump.

**v6.3.0** (Runtime modes split out of manual overrides — behavior change with auto-migration)

- **Architecture**: Persistent toggles (Arbitrage Mode, Negative Arbitrage Buy Mode) are now first-class **Runtime Modes** owned by their dedicated dashboard switches, no longer entries in `_manual_overrides`. The "Clear Manual Override" service / dashboard button (`target: all`) now wipes only the per-decision overrides — battery charging, car charging, charger limit, and grid setpoint — and leaves the runtime-mode switches untouched. Car Permissive Mode was already a separate switch with its own store and continues to behave the same way.
- **New storage** (`coordinator.py`): `_runtime_mode_store` (filename `<DOMAIN>.<entry_id>.runtime_modes`, version 1) holds the persistent toggle state in `{"modes": {"arbitrage_mode": {...}, "negative_buy_mode": {...}}}` shape. Loaded before `_manual_override_store`.
- **Backward-compat migration** (`runtime_modes.py:load_runtime_modes`): On first load after upgrade, if the new store is empty the manager reads the legacy `arbitrage_mode` / `negative_buy_mode` (and the historical `battery_dump` / `battery_dump_to_grid` rename targets) from the manual-override store, seeds the runtime store via `persist_runtime_modes()`, then `_async_load_manual_overrides` rewrites the legacy store without those keys via its existing "unknown key → mark changed → re-persist" path. No user intervention required.
- **Service surface** (`__init__.py`, `services.yaml`, `strings.json`, `translations/en.json`): Removed `arbitrage_mode` and `negative_buy` from the `target` selector of both `electricity_planner.set_manual_override` and `electricity_planner.clear_manual_override`. The dedicated switches (`switch.electricity_planner_arbitrage_mode`, `switch.electricity_planner_negative_arbitrage_buy_mode`) are now the only way to toggle. Removed the corresponding `MANUAL_OVERRIDE_TARGET_ARBITRAGE_MODE` / `MANUAL_OVERRIDE_TARGET_NEGATIVE_BUY` constants from `const.py`.
- **Switch attributes** (`switch.py`): `ArbitrageModeSwitch` and `NegativeArbitrageBuyModeSwitch` now expose `mode_enabled` (was `override_active`) since the toggle is no longer modelled as a manual override. `is_on` reads `coordinator.is_arbitrage_mode_enabled()` / `is_negative_buy_mode_enabled()` which delegate to the runtime mode manager. Other attributes (`reason`, `set_at`, plan-derived fields) unchanged.
- **Numeric override fix** (`manual_overrides.py`): `set_override()` now applies `charger_limit` and `grid_setpoint` whenever the caller passes a value, regardless of which boolean `target` was named. Previously, calling `set_manual_override(target='car', action='force_charge', grid_setpoint=0)` from the dashboard's "Force Car Charging" prompt silently dropped the `grid_setpoint=0` because the field name didn't match the target. The dashboard bundles all four prompts into the same service call, so this gate dropped legitimate user input.
- **Decision payload cleanup** (`manual_overrides.py:apply`): The `manual_overrides` block on the decision now always reflects the current per-decision overrides (or `{}` when there are none) instead of merging via `setdefault`. Runtime mode state never leaks into this payload — sensors and the dashboard read it from the dedicated switches and planner data.
- **New runtime-mode coordinator API**: `get_arbitrage_mode_state()` and `get_negative_buy_mode_state()` return the full state dict (or `None`), used by the switches; `is_arbitrage_mode_enabled()` and `is_negative_buy_mode_enabled()` remain the boolean predicates consumed by `arbitrage_mode.py` and `negative_buy.py`.
- **Dashboard** (`dashboard_template.yaml`, both bundled dashboards): The *Manual override status* card no longer lists `arbitrage_mode` (those have their own switches) and now formats `charger_limit` / `grid_setpoint` values as watts with the same expiry/reason metadata as boolean overrides. `MANAGED_VERSION` bumped `31` → `32` so existing managed dashboards re-save automatically on next reload.
- **Tests**: 488/488 passing. Notable additions:
  - `test_force_car_with_zero_grid_setpoint_propagates_to_decision` — regression for the original `grid_setpoint=0` drop bug, asserts the value reaches the final decision.
  - `test_clear_all_preserves_arbitrage_and_negative_buy_modes` — verifies `clear(target='all')` leaves runtime mode states intact while clearing per-decision overrides.
  - `test_runtime_modes_migrate_from_legacy_manual_override_store` — exercises the full upgrade path: legacy store with `arbitrage_mode` + `negative_buy_mode` + `car_grid_charging` → runtime store seeded, manual-override store cleaned, legacy keys gone.
  - `test_runtime_modes_are_not_exposed_as_manual_override_status` — guards that runtime state can never leak into `decision["manual_overrides"]`.
  - `test_manual_override_target_mapping_excludes_runtime_modes`, `test_manual_override_service_schema_rejects_runtime_mode_targets`, `test_services_yaml_excludes_runtime_mode_targets` — pin the service-surface contract.
  - `test_mixed_boolean_target_applies_numeric_override_fields` rewritten from the previous "ignores numeric fields" expectation to assert the bundled application path.
  - Dashboard tests: `test_manual_override_status_formats_numeric_overrides_as_watts` covers the new wattage formatting; `test_bundled_single_phase_dashboard_clear_all_targets_per_decision_overrides` replaces the old "all targets" assertion.
  - `test_arbitrage_mode_persists_across_restart` and the `ArbitrageModeSwitch` / `NegativeArbitrageBuyModeSwitch` restart tests now exercise `_runtime_mode_store` and `_async_load_runtime_modes`.
- **Compatibility**: No config-schema change (still v23), no migration needed for `entry.data` / `entry.options`. Persisted runtime-mode state migrates transparently on the first coordinator load. Existing automations targeting `switch.electricity_planner_arbitrage_mode` / `switch.electricity_planner_negative_arbitrage_buy_mode` continue to work unchanged. Automations that called `electricity_planner.set_manual_override` with `target: arbitrage_mode` or `target: negative_buy` must be updated to use the corresponding switch entity (`switch.turn_on` / `switch.turn_off`) — the service schema now rejects those targets. The `extra_state_attributes` rename `override_active` → `mode_enabled` on the two runtime-mode switches is a breaking change for any dashboard / template that read the old attribute name.

**v6.2.2** (dashboard wording fix — no behavior change)

- **Changed** (`dashboard_template.yaml`, both bundled dashboards): Reworded three bullets in the *Battery Controls — quick reference* card so they match the underlying logic, which evaluates against the **capacity-weighted average** SOC across all configured batteries (not against any single battery):
  - *Grid Max SOC*: "stops buying once **any battery** reaches this level" → "stops buying once **the average battery SOC** reaches this level". Verified against `battery_analysis.py:61` (`batteries_full = average_soc >= max_threshold`).
  - *Solar Max SOC*: "Once **batteries cross** this level, surplus is offered to the EV or **exported instead of being absorbed**" → "Once **the average battery SOC crosses** this level, surplus is offered to the EV or **exported when feed-in is profitable**". Verified against `battery_charging.py:110` (`average_soc` vs `settings.max_soc_threshold_solar`).
  - *Arbitrage Reserve SOC*: "exports stop once **SOC** reaches this floor" → "exports stop once **the average SOC** reaches this floor". Verified against `charger_limit.py:135` (`average_soc < arbitrage_reserve_soc`).
- **Bumped**: `MANAGED_VERSION` `30` → `31` in `dashboard.py` so existing managed dashboards re-save the corrected wording automatically on next reload.
- **Tests**: 486/486 passing. No new tests required — the existing dashboard assertions check for the bolded control names, not the surrounding sentence text, and remain valid under the new wording.
- **Compatibility**: No code, schema, migration, entity-ID, or behavioural changes. Bundled-YAML users need to re-copy to pick up the corrected text; managed-dashboard users get it automatically on next integration reload via the `MANAGED_VERSION` bump. Drop-in replacement for v6.2.1.

**v6.2.1** (dashboard polish — no behavior change)

- **Added** (`dashboard_template.yaml`, both bundled dashboards): **Battery Controls — quick reference** `type: markdown` card inserted directly below the Battery Controls entities row. One concise line per control, grouped into three sub-sections so users can scan instead of memorise:
  - *Grid charging limits*: `Grid Max SOC` (default 70%), `Grid Max SOC (high solar)` (default 35%), `Solar Max SOC` (default 50%), `Sunny Forecast Trigger` (default 5 kWh).
  - *Arbitrage*: `Arbitrage Reserve SOC` (default 40%), `Arbitrage Deadline Hour` (default 12), `Negative Arbitrage Buy Threshold` (default −0.05 €/kWh).
  - *Mode toggles*: `Car permissive`, `Arbitrage mode`, `Negative Arbitrage Buy Mode`, `Disable Battery Charging`.
  - Defaults verified against `const.py` (notably `Arbitrage Reserve SOC` is **40%**, not 50%; `Sunny Forecast Trigger` is **5 kWh**, not a percent).
- **Bumped**: `MANAGED_VERSION` `29` → `30` in `dashboard.py` so existing managed dashboards re-save the new explanations card automatically on next reload.
- **Tests**: 486/486 passing. Added `test_bundled_dashboards_include_battery_controls_quick_reference` and `test_managed_dashboard_template_includes_battery_controls_quick_reference` — each splits the dashboard YAML between the *Battery Controls* and *Decisions* titles and asserts every one of the 11 bolded control names appears in the explanations slice (defends against either the card going missing or any single control being orphaned from the reference).
- **Compatibility**: No code, schema, migration, entity-ID, or behavioural changes. Bundled-YAML users need to re-copy to pick up the card; managed-dashboard users get it automatically on next integration reload via the `MANAGED_VERSION` bump. Drop-in replacement for v6.2.0.

**v6.2.0** (Negative Arbitrage Buy decoupled from battery SOC — behavior change)

- **Changed** (`negative_buy.py`): **Negative Arbitrage Buy is now a general grid-import request, not a battery-fill planner.** The mode no longer requires battery details to plan or activate, and no longer deactivates when batteries reach `max_soc_threshold`. The activation predicate collapsed to the obvious one: *current slot price ≤ `negative_buy_threshold` AND slot finishes before the shared `arbitrage_mode_deadline_hour`*. `_select_buy_slots` is no longer called from the planner — every eligible slot at or below the threshold counts. The exposed `buy_price_threshold` is the highest price among the eligible slots (the binding upper bound).
- **Changed** (`battery_charging.py`): The negative-buy override now runs **before** the full-battery / SOC-ceiling early returns, so an active import request takes immediate priority over normal battery state checks. The reason string surfaces the active threshold instead of the battery SOC narrative.
- **Changed** (`grid_setpoint.py`): When negative-buy is active, the grid setpoint reserves the **safe peak-limited import budget** (capped by `monthly_grid_peak` and `max_grid_power`, with the EV's current import subtracted from the headroom) regardless of the normal battery-charging branch. New SOC-unavailable branch keeps the import bounded by `max_grid_power - car_grid_import`. The reason string explicitly calls out the `negative-buy import budget <W>` and the binding peak-this-month figure.
- **Added** (`__init__.py` / `runtime_modes.py`): `CONF_NEGATIVE_BUY_THRESHOLD` and `CONF_MAX_SOC_THRESHOLD_SOLAR` registered in `LIVE_UPDATE_OPTIONS` so changes via the matching `number.*` entities propagate to the coordinator without an integration reload (matching the existing arbitrage live-update behaviour).
- **Removed** (`switch.py`): `max_soc_threshold` from the `switch.electricity_planner_negative_arbitrage_buy_mode` state attributes — it was a leftover from the SOC-gated era and no longer drives the planner. Other attributes (`active`, `solar_curtail_active`, `reason`, `threshold`, `deadline`, `required_energy_kwh`, `required_duration_hours`, `import_power`, slot details) are unchanged.
- **Changed** (`dashboard_template.yaml`, both bundled dashboards): Negative Arbitrage Buy threshold line recoloured `#c0392b` (red) → `#16a085` (teal) so it does not visually clash with the *Dynamic Threshold* (orange) on the buy-prices chart while still being clearly distinct.
- **Bumped**: `MANAGED_VERSION` `28` → `29` so existing managed dashboards re-save the new threshold colour automatically on next reload.
- **Docs** (`README.md`): Updated the *Negative Arbitrage Buy Mode* prose, service-target descriptions, number-entity table, and threshold parameter row to describe the new "peak-limited grid import" semantics and the widened `[-1.0, 1.0]` documented range (the config-flow already accepted `[-1, 1]`).
- **Tests**: 484/484 passing. New coverage:
  - `test_negative_buy_plan_allows_import_without_battery_details` — planner runs and arms with empty `battery_details`.
  - `test_negative_buy_plan_buys_when_battery_above_soc_ceiling` (formerly `..._curtails_solar_when_battery_at_ceiling`) — the planner now actively buys at full SOC instead of arming curtailment-only.
  - `test_negative_buy_forces_grid_import_when_battery_full` — engine override fires past the SOC ceiling.
  - `test_negative_buy_grid_setpoint_uses_peak_limited_import_budget` — setpoint uses `monthly_grid_peak * 0.9` minus EV import, not battery headroom.
  - `test_negative_buy_grid_setpoint_works_without_battery_soc` / `..._respects_max_grid_power_with_ev_load` — SOC-unavailable branch still imports and stays under `max_grid_power` with concurrent EV draw.
  - Existing `..._calculates_required_energy_and_duration` updated to assert the new "all-eligible-slots, full-headroom" reporting (5 kWh / 0.83h / 8 slots / 6 kW configured cap).
- **Compatibility**: No config-schema change (still v23), no migration required, no entity-ID renames. Existing automations watching `switch.electricity_planner_negative_arbitrage_buy_mode` continue to work unchanged. The behavioural shift means installations with negative-buy already enabled will start importing in scenarios they previously skipped (full batteries, missing battery data) — disable the switch to retain v6.1.1 behaviour.

**v6.1.1** (dashboard polish — no behavior change)

- **Added** (`dashboard_template.yaml`, both bundled dashboards): **Negative Arbitrage Buy reason** attribute row in the diagnostics card, sourced from `switch.electricity_planner_negative_arbitrage_buy_mode`'s `reason` attribute. Sits next to the existing *Arbitrage reason* so both arbitrage directions are visible at a glance.
- **Added** (`dashboard_template.yaml`, both bundled dashboards): Live **Negative Arbitrage Buy Threshold** horizontal line on the *Electricity Buy Prices* ApexCharts graph. The data-generator reads `switch.electricity_planner_negative_arbitrage_buy_mode.attributes.threshold` and only renders the line while the switch is `on`, so the chart stays clean when negative-buy mode is disabled. Drawn in `#c0392b` to visually distinguish it from the existing *Dynamic Threshold* (orange).
- **Removed** (`dashboard_template.yaml`, both bundled dashboards): The two redundant top-of-dashboard big buttons — *Car permissive* and *Arbitrage mode*. Both switches remain reachable from the **Battery Controls** entities row below, where the live-adjustable thresholds (`negative_buy_threshold`, `arbitrage_mode_deadline_hour`, `arbitrage_mode_reserve_soc`) also live, so users have a single grouped panel for runtime toggles instead of two competing surfaces.
- **Bumped**: `MANAGED_VERSION` `27` → `28` in `dashboard.py` so existing managed dashboards re-save automatically on next reload.
- **Tests**: 479/479 passing. Added dashboard coverage for: (1) the dropped top buttons no longer appear in the section above the *Active Grid Charging Limit* card while both switches remain reachable from the entities list below; (2) the *Negative Arbitrage Buy reason* attribute row is present in bundled dashboards and the managed template; (3) the *Negative Arbitrage Buy Threshold* series is present in the buy chart and absent from the sell chart in bundled dashboards and the managed template. Each of the three checks runs against both the bundled YAMLs and the managed `dashboard_template.yaml` (six tests total).
- **Repo housekeeping** (no integration code touched):
  - Moved `automationExample_power_sensor.yaml` → `examples/automations/car_charger_dynamic_control_power_sensor.yaml` so the variant lives next to its sibling automations; documented under the existing *Charger turned on* automation in `examples/automations/README.md`.
  - Removed the orphan `automationExample.yaml` at repo root (superseded by the curated `examples/automations/` set).
  - Moved `test_dashboard_debug.py` → `tools/dashboard_debug.py` (it isn't a pytest fixture, so it no longer pollutes test discovery); the bootstrap now resolves modules from either the repo root or a Home Assistant config root.
  - Extended `.gitignore` with `.pytest_cache/`, `.coverage`, and `.coverage.*`.
- **Compatibility**: No code-path, schema, or migration changes. Internal keys and entity IDs unchanged. Drop-in replacement for v6.1.0.

**v6.1.0** (Negative Arbitrage Buy mode + arbitrage-mode rename)

- **Added**: **Negative Arbitrage Buy mode** (`negative_buy.py`) — when the *net* feed-in price drops below `CONF_NEGATIVE_BUY_THRESHOLD` (default `-0.05 €/kWh`, i.e. truly paid-to-consume), the planner force-grid-charges batteries up to `max_soc_threshold` and curtails solar export so the inverter ramps down instead of paying to inject. Companion to the existing arbitrage *sell* path; both are gated by the same shared `arbitrage_mode_deadline_hour`.
- **Added**: `switch.electricity_planner_negative_arbitrage_buy_mode` runtime toggle (`switch.py`) wired through `runtime_modes.py` and exposed on both bundled dashboards.
- **Added**: Live-adjustable number entities — `number.electricity_planner_arbitrage_mode_deadline_hour` and `number.electricity_planner_negative_buy_threshold` — joining the existing `arbitrage_mode_reserve_soc` slider. All three live-update the coordinator without an integration reload (registered in `LIVE_UPDATE_OPTIONS`).
- **Renamed (project-wide)**: `Battery Dump` → `Arbitrage Mode`. Affects:
  - Config keys: `battery_dump_target_soc` → `arbitrage_mode_reserve_soc`, `battery_dump_deadline_hour` → `arbitrage_mode_deadline_hour`, `battery_dump_max_export_power` → `arbitrage_mode_max_export_power`.
  - Constants: `CONF_BATTERY_DUMP_*` → `CONF_ARBITRAGE_MODE_*`.
  - Module: `battery_dump.py` → `arbitrage_mode.py` (and consumers updated).
  - Number entity: `number.electricity_planner_battery_dump_target_soc` → `number.electricity_planner_arbitrage_mode_reserve_soc`. **The unique_id suffix is intentionally preserved** (`battery_dump_target_soc`) so historical statistics survive the rename — only the entity_id slug is migrated via `_async_migrate_entity_ids`.
  - Internal slot-selection keys: `select_export_slots()` returns `export_price_threshold` / `covers_full_export` (was `dump_price_threshold` / `covers_full_dump`); `arbitrage_mode.py` continues to expose them publicly as `arbitrage_price_threshold` / `slots_cover_full_arbitrage`.
- **Current behaviour**: Arbitrage Mode and Negative Arbitrage Buy Mode are switch-controlled runtime settings stored separately from manual overrides. Manual override services only target battery, car, charger limit, and grid setpoint.
- **Added**: Config-schema migration path **v21 → v22 → v23**:
  - v21→v22 backfills `negative_buy_threshold = -0.05` and defensively strips any pre-release `negative_buy_deadline_hour` key (the deadline is shared with arbitrage selling).
  - v22→v23 renames the legacy `battery_dump_*` storage keys in both `entry.data` and `entry.options` to their `arbitrage_mode_*` equivalents.
- **Added**: Solar Max SOC parity in both bundled dashboards — `number.electricity_planner_max_soc_threshold_solar` is now a first-class control alongside `max_soc_threshold` and `max_soc_threshold_sunny` (was previously only exposed via the auto-generated dashboard).
- **Audited**: Energie.be Dynamic (April 2026, Particulier — Vlaanderen) reference contract verified against codebase defaults — all per-kWh price formulas, transport tariffs, GSC/WKK, and Belgian taxes match exactly. No constant changes required.
- **Tests**: 474/474 passing (added coverage for the new override targets, dashboard entity-reference resolution for the negative-buy/arbitrage controls, and the rename-aware migration path).
- **Compatibility**: Existing config entries are migrated automatically on next load. Number-entity history for the renamed reserve-SOC slider is preserved via the unique_id-stable rename. Drop-in replacement for v6.0.2.

**v6.0.2** (logic-review follow-up — small fixes, no behavior drift)

- **Fixed** (`battery_charging.py`): Surplus-block gate replaced hardcoded `DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold` (50) with `settings.max_soc_threshold_solar`. The "significant solar + medium SOC → skip grid charging" check now tracks the user-configurable solar ceiling instead of drifting from it. Default behaviour is unchanged (both defaults are 50).
- **Fixed** (`car_charging.py`): Arbitrage-override return dict now explicitly sets `car_solar_only: False`. Arbitrage mixes solar with battery discharge, so the flag must never be true here; previously relied implicitly on the engine-level seed in `_initialize_decision_data`. Makes intent explicit and defends against future refactors.
- **Changed** (`grid_setpoint.py`): Demoted the two safety-net clamp logs from `warning` to `info` and dropped the "This is a bug in the decision logic" suffix. The clamp-to-zero still fires; only the log noise is reduced so that recoverable edge cases (e.g. transport-cost crossover mid-interval) no longer surface as persistent HA notifications.
- **Docs** (`strategies.py`): Clarified `PredictiveChargingStrategy` docstring — its advisory "wait for price drop" reason surfaces only when `DynamicPriceStrategy` is disabled (`use_dynamic_threshold=False`); otherwise the dynamic strategy's reason overwrites it. No behavior change.
- **Docs** (`CLAUDE.md`): Corrected post-refactor strategy count from 8 → 6 in the architecture overview and file-structure tree (lists actual class names; notes the inline `PriceThresholdGuard` supplants the removed `EmergencyChargingStrategy`).
- **Tests**: 456/456 passing. `tests/test_decision_engine_power.py::test_grid_setpoint_upstream_ignores_dump_power_when_arbitrage_inactive` updated to `caplog.at_level("INFO", logger="custom_components.electricity_planner.grid_setpoint")` matching the new log call-site and level.
- **Compatibility**: No config-schema change (still v21), no migration required, no public-API changes. Drop-in replacement for v6.0.1.

**v6.0.1** (documentation-only patch)

- **Added**: `.markdownlint.json` with project-appropriate rule overrides (MD013, MD033, MD036, MD051, MD060 disabled — long-form technical docs, idiomatic `<br>` in tables, `**vX.Y.Z**` changelog markers).
- **Fixed**: Remaining structural lint issues in `README.md` and `CLAUDE.md` — blank lines around lists, headings, and fenced code blocks; ASCII-tree fences tagged as `text`; disambiguated duplicate `### Arbitrage Mode` heading.
- **Fixed**: Real structural bug in `README.md` — "Permissive Mode Toggle" YAML automation example was missing its closing fence (compensated by a stray duplicate close further down), corrupting downstream rendering.
- **Net result**: 343 → 0 markdownlint errors. No code, schema, or decision-behavior changes. 456/456 tests still passing.

**v6.0.0** (major internal refactor — no behavior change)

- **Refactor**: Decomposed `ChargingDecisionEngine` (~3100 LOC) and `ElectricityPlannerCoordinator` (~3415 LOC) into 22 focused collaborator modules. Final sizes: `decision_engine.py` 1472 LOC (−52%), `coordinator.py` 1356 LOC (−60%).
- **New modules — engine-side**: `inverter_derating.py`, `feedin_decision.py`, `battery_analysis.py`, `price_analysis.py`, `threshold_calculator.py`, `charging_window.py`, `car_charging.py`, `battery_charging.py`, `solar_allocation.py`, `grid_setpoint.py`, `charger_limit.py`, `override_recalculator.py`, `phase_distributor.py`.
- **New modules — coordinator-side**: `price_timeline.py`, `transport_cost.py`, `nordpool_service.py`, `arbitrage_mode.py` (formerly `battery_dump.py`), `forecast_summary.py`, `solar_forecast.py`, `entity_status.py`, `manual_overrides.py`, `runtime_modes.py`.
- **Patterns**: Collaborators receive an `EngineSettings` snapshot or share a `CycleContext` per decision cycle. Public API of `ChargingDecisionEngine` and `ElectricityPlannerCoordinator` is unchanged; only the load-bearing orchestration methods remain on the parent classes.
- **Cleanup**: Removed 32 dead private delegator methods (unreachable after extraction) and 5 unused imports. No test churn — all existing monkeypatch / attribute access call-sites still resolve correctly.
- **Tests**: 456 pytest tests passing (433 lines of new test coverage in `tests/test_decision_engine_power.py` and `tests/test_strategies_smoke.py`).
- **Compatibility**: No config-schema change (still v21), no user-facing behavior change, no migration required. Drop-in replacement.

**v5.0.2**

- **Added**: Immediate cap-reopen path in `_calculate_inverter_derating_target` (inside the `export_below_band` branch) for when feed-in is blocked and the site is actively importing from grid (`grid_power_w > 0`). Raises the target to `min(max_inverter_power, solar_production + grid_import + export_limit)` so the inverter can promptly reclaim output instead of creeping up 100W/tick via `should_relax_cap_upward()` while grid power is being paid for.
- **Complements**: The existing `house_consumption > solar_production` fast path — new branch runs when house-consumption data is unavailable or when imports are driven by battery/EV draw rather than house load.
- **Safety**: Only opens upward (`previous_target_w is None or operating_point_target_w > previous_target_w`); bounded by `max_inverter_power`; clears `inverter_derating_unreached_since` via `**no_alarm`. Feed-in allowed, export-in-band, and low-SOC bypass paths are unchanged.
- **Tests**: 437 pytest tests passing (added `test_inverter_derating_recalculates_immediately_when_site_is_importing` in `tests/test_decision_engine_power.py`).

**v5.0.1**

- **Added**: `CONF_MAX_SOC_THRESHOLD_SOLAR` / `DEFAULT_MAX_SOC_SOLAR = 50` — independent battery SOC ceiling for solar absorption, decoupled from grid-charging `max_soc_threshold` and the sunny-day override.
- **Changed**: `_calculate_battery_solar_allocation` now consults `settings.max_soc_threshold_solar` instead of the grid ceiling, so batteries stop absorbing free PV at 50% (default) and surplus is diverted to the EV or exported while the grid ceiling remains at 70%/35%.
- **Changed**: `_bootstrap_car_solar_allocation` gate is now strictly `batteries_full OR min_soc ≥ max_soc_threshold_solar − soc_buffer` — removed the previous `average_soc >= solar_max` shortcut that could bypass a lagging battery.
- **Added**: v20 → v21 migration backfilling `max_soc_threshold_solar = 50` for existing installs.
- **Added**: Full UI wiring — config-flow/options-flow thresholds step for both single- and three-phase setups, `strings.json` translations, `MaxSocThresholdSolarNumber` live-adjust entity (`number.electricity_planner_max_soc_threshold_solar`), dashboard card, and diagnostic sensor exposure in `configured_limits`.
- **Tests**: 436 pytest tests passing.

**v5.0.0** (consolidated 5.x release)

- **Added**: Car-state-aware solar allocation policy in `_allocate_solar_power` — see `### Solar Allocation Policy` above.
- **Added**: `_bootstrap_car_solar_allocation` — offers leftover surplus to an idle car when batteries satisfy the near-full gate (`batteries_full` or every battery's SOC ≥ `max_soc_threshold − soc_buffer`), enabling solar-only mode to start without the car already drawing power.
- **Added**: `solar_headroom = allocated_car_solar + remaining_solar` used across all four grid-charging branches of `_calculate_charger_limit` (no-battery-data, low-SOC power sharing, below-max-SOC, at-or-above-max-SOC / arbitrage) so the EV's limit includes exportable surplus on top of the grid allowance. Solar-only mode intentionally keeps the tighter `allocated_solar` bound.
- **Added**: `non_grid_floor` parameter to `_apply_peak_import_limit` so allocated solar, remaining surplus, and battery-arbitrage power are preserved from the 50% grid reduction during peak-import events.
- **Added**: `_format_power_sources` helper for consistent `<W> <label>` fragments in charger-limit reason strings.
- **Removed**: Obsolete `_create_insufficient_solar_allocation` and `_calculate_car_solar_allocation` helpers (merged into the unified allocation flow).
- **Tests**: 436 pytest tests passing.

**v4.10.x**

- **Added**: Sunny day feature — reduces grid charging max SOC when solar forecast is high
- **Added**: Solar forecast caching with configurable start hour (12-23, default 20)
- **Added**: Optional "today" forecast entity for overnight validation
- **Added**: 187 pytest tests with comprehensive coverage for forecast and sunny day logic
- **Fixed**: Silent wrong-day fallback bug in forecast resolution after midnight
- **Fixed**: ~30 missing UI translation keys across config and options flows

---

## Developer Setup

This repo ships a fully configured developer environment. Use it.

### Devcontainer (recommended)

`.devcontainer/devcontainer.json` + `Dockerfile` provide a Python 3.12 image (works on amd64 and arm64) with every dev dep, pre-commit hooks, and VS Code extensions pre-installed.

- Open in VS Code → "Reopen in Container".
- Forwarded ports: 8000 (mkdocs), 8123 (HA).
- Post-create installs `requirements-dev.txt` and runs `pre-commit install`.

> **Docker group note (Pi host):** `marco` was added to the `docker` group, but already-open shells won't see it until a new login. Use `newgrp docker`, open a fresh terminal, or prefix one-off commands with `sg docker -c "..."`.
>
> **Host Python:** the Pi ships Python 3.13, which is **too new** for the pinned HA test deps (need 3.10–3.12). Stick to the devcontainer for `make test`, or install Python 3.12 via pyenv/uv if you really want a host venv.

### Make targets

Run `make help` for the full colored list. Most-used:

```
make test                       # pytest
make test-coverage              # coverage (term + htmlcov/)
make test-file f=tests/test_x.py
make test-name n=pattern
make lint                       # flake8
make format                     # black + isort
make format-check               # CI-style check
make typecheck                  # mypy
make docs-serve                 # mkdocs at :8000
make pre-commit                 # all hooks on all files
make commit                     # cz commit (conventional)
```

### Pre-commit

Configured in `.pre-commit-config.yaml`: trailing whitespace, EOF fixer, yaml/json checks, large files, debug statements, black, isort, flake8, and (manual stage) mypy.

```
pre-commit install
pre-commit run --all-files
```

### Docs

MkDocs + Material under `docs/`. Build with `make docs`, serve with `make docs-serve`. Update `architecture.md` / `decision-engine.md` / `strategies.md` whenever the pipeline shape changes.

### Git workflow

- Conventional commits (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`). Use `make commit` for a guided prompt.
- Feature branches `feat/<short-name>` → PR against `main`.
- CI (`.github/workflows/ci.yml`) runs lint → typecheck → tests/coverage → docs build.

### Testing patterns

- `tests/` uses pytest-asyncio + pytest-homeassistant-custom-component.
- Build snapshots via helpers in `tests/test_helpers.py`.
- Decision-engine tests are split by concern (power / car / errors / trees).
- Add a smoke test in `test_strategies_smoke.py` for every new strategy.
- Coverage target: ≥ 80% (enforced by `pyproject.toml`).

### Config locations

- `pyproject.toml` — black, isort, pytest, coverage, mypy, flake8, commitizen
- `Makefile` — task entry points
- `mkdocs.yml` — docs site config
- `.vscode/settings.json` + `extensions.json` — editor defaults
- `.github/workflows/ci.yml` — CI pipeline
