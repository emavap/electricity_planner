# Changelog

All notable changes to the Electricity Planner integration will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2025-10-18

### Added
- **Manual Override Services** – new `set_manual_override` / `clear_manual_override` domain services with duration and reason support, exposed in Lovelace for one-touch boosts or pauses.
- **Forecast Insights Sensor** – `sensor.electricity_planner_price_forecast_insights` surfaces the cheapest upcoming interval and best charging window in diagnostics dashboards.
- **Strategy Trace & Diagnostics** – decision diagnostics now include strategy evaluation traces plus live override metadata to aid troubleshooting.

### Changed
- **Options Flow** – writes changes to config entry options (instead of data) and persists per-battery capacity inputs without mutating the original entry.
- **Options Defaults** – forms now preload values from merged entry data + options, so previously saved adjustments appear when reopening the wizard.
- **Service UX** – services auto-select the sole coordinator when only one instance is running; `entry_id` is now optional in both UI strings and Lovelace calls.
- **Dashboard** – bundled dashboard YAML shows manual override status and includes ready-made buttons leveraging the new services.

### Fixed
- **Negative Price Windows** – forecast window logic accounts for zero or negative €/kWh pricing while still exposing the best average price.
- **Manual Override Expiry** – overrides clear cleanly after expiry and strategy traces note overrides explicitly, preventing stale decisions.
- **Feed-in Threshold Attributes** – feed-in binary sensor attributes now source thresholds from the coordinator’s merged configuration, keeping dashboards aligned after option tweaks.

## [2.8.0] - 2025-10-15

### Added
- **Car Charging Threshold Floor** - Prevents mid-session interruptions from threshold drift
  - Locks price threshold when car starts charging (OFF→ON transition)
  - Uses `max(locked_threshold, current_threshold)` during active charging
  - Prevents threshold decreases from stopping charging
  - Allows threshold increases to take effect immediately
  - Clears lock when charging stops (ON→OFF transition)
  - Particularly important with 24h rolling average threshold that can drop when day-ahead data arrives

### Improved
- **Charging Continuity** - Car charging sessions now complete reliably even when threshold drifts downward
- **Debug Logging** - Added threshold floor logging showing locked vs current vs effective threshold

## [2.7.0] - 2025-10-15

### Added
- **24-Hour Minimum Window for Average Threshold** - More stable threshold calculation
  - Forces minimum 24-hour rolling window for average threshold
  - Backfills with recent past prices when future data < 24h
  - Prevents late-evening threshold spikes from sparse data
  - Gracefully degrades to future-only when insufficient past data available
  - Dynamic interval resolution detection (15-min or hourly)

### Improved
- **Threshold Stability** - Eliminates volatility from short future windows
- **Better for Batteries** - More consistent charging decisions with full 24h context
- **Robust Past Collection** - Handles missing historical data gracefully

## [2.6.0] - 2025-10-15

### Fixed
- **Car Charging Window Validation** - Fixed `_check_minimum_charging_window()` to ensure continuous low-price windows start NOW
  - Previously: Method could find any 2-hour window in the future and approve charging immediately
  - Now: Only approves if current time is within a continuous N-hour low-price window
  - Improved interval handling to include currently active intervals
  - Added automatic interval resolution detection (15-min vs hourly)
  - Added gap detection with 5-second tolerance for consecutive intervals
  - Counts only time remaining from NOW forward, not past portions of intervals

### Improved
- **Clearer Hysteresis Documentation** - Added detailed explanation of OFF→ON vs ON→OFF behavior
- **Better Logging** - Enhanced debug messages show why windows are rejected
- **Robust Timeline Building** - Explicit start/end times for all intervals handle missing data gracefully

## [2.2.0] - 2024-01-10

### Changed
- **Refactored Charging Strategies** - Removed redundant time-based logic
  - Charging decisions now rely purely on Nord Pool price data
  - Removed night/winter charging overrides (price data is more accurate)
  - Renamed TimeBasedChargingStrategy → SolarAwareChargingStrategy
  - Only solar peak timing is relevant (wait for free solar vs cheap grid)

### Removed
- **Deprecated Config Options**:
  - `emergency_soc_override` - No longer needed with price-based logic
  - `winter_night_soc_override` - Price data already reflects night/winter patterns

### Fixed
- **Critical Bug**: Night time detection logic (midnight-spanning windows)

### Improved
- **Simpler Logic** - Price data-driven decisions are clearer and more accurate
- **Better Adaptation** - Responds to actual market conditions, not time assumptions
- **Cleaner Code** - Removed 78 lines of redundant time-based checks

### Migration
- **v3 → v4**: Automatic migration removes deprecated time-based config options

## [2.1.0] - 2024-01-10

### Added
- **Dynamic Threshold System** (Opt-in) - Intelligent price-based charging within threshold range
  - Treats price threshold as maximum ceiling, not simple trigger
  - Analyzes price quality within acceptable range
  - Adapts to market volatility (more selective when volatile)
  - SOC-influenced confidence thresholds
  - Future price consideration
  - **Default: OFF** - Users must enable in settings
- **Price Ranking Strategy** - Ranks current price within time window
- **Adaptive Charging Strategy** - Learns from historical price patterns
- **Smart Charging Decision Engine** - Multi-factor confidence scoring
- **New Configuration Options**:
  - `use_dynamic_threshold` - Enable/disable dynamic threshold (default: **false** - opt-in)
  - `dynamic_threshold_confidence` - Base confidence requirement (default: 60%, range: 30-90%)

### Changed
- **Battery Charging Logic** - When dynamic mode enabled:
  - Much more selective about when to charge
  - Won't charge at 0.14€ just because it's below 0.15€ threshold
  - Waits for actually good prices within acceptable range
  - Considers price position in daily range
  - Evaluates if better prices are coming soon
- **Car Charging Logic** - **UNCHANGED** - Remains simple threshold-based for high energy needs

### Improved
- **Cost Savings** - Potential 20-40% lower average charging prices (when dynamic mode enabled)
- **Decision Quality** - Fewer but better charging sessions
- **Market Adaptation** - Adjusts behavior based on price volatility
- **User Control** - Threshold is now a maximum, dynamic mode is opt-in

### Migration
- **v2 → v3**: Automatic migration adds new config options with safe defaults (dynamic OFF)

## [2.0.0] - 2024-01-10

### Added
- **Comprehensive Decision Diagnostics Sensor** - Complete visibility into all decision parameters
- **Strategy Pattern for Decisions** - Cleaner, more maintainable decision logic
- **Data Validation Framework** - Robust validation of all inputs and outputs
- **Helper Utilities Module** - Reusable validation and calculation functions
- **Circuit Breaker Pattern** - Prevents cascading failures when data unavailable
- **Cached Price Calculations** - Improved performance with LRU caching
- **Configuration Migration System** - Smooth upgrades between versions
- **Unit Test Infrastructure** - Comprehensive test coverage with pytest
- **Troubleshooting Guide** - Detailed diagnostic and debugging documentation
- **Threshold Monitoring Sensors** - Real-time visibility of all configured thresholds
- **Power Allocation Validator** - Ensures solar allocation never exceeds available
- **Type Hints Throughout** - Better IDE support and type safety
- **Developer Documentation** - Clear code organization and patterns

### Changed
- **Refactored Decision Engine** - Separated into smaller, testable components
- **Improved Error Handling** - Better resilience when data is unavailable
- **Enhanced Validation** - All power values and configurations are validated
- **Optimized Entity Fetching** - Parallel fetching where possible
- **Simplified Complex Methods** - Breaking down large methods into smaller ones
- **Better Code Organization** - Separated concerns into dedicated modules
- **Clearer Decision Reasoning** - More descriptive and formatted reasons

### Fixed
- **Race Condition in Power Allocation** - Solar allocation now properly synchronized
- **Battery Capacity Weighting** - Accurate weighted average SOC calculation
- **Predictive Logic Edge Cases** - Emergency overrides now work correctly
- **Data Unavailability Handling** - Graceful degradation when sensors offline
- **Memory Leaks in History Sensor** - Proper cleanup of old data

### Improved
- **Performance** - Reduced CPU usage with caching and optimization
- **Maintainability** - Cleaner code structure with separation of concerns
- **Testability** - Comprehensive unit tests for critical logic
- **Debuggability** - Enhanced logging and diagnostic tools
- **Documentation** - Added troubleshooting guide and inline documentation
- **Type Safety** - Complete type hints for better IDE support
- **Error Messages** - Clearer, more actionable error descriptions

## [1.0.0] - 2024-01-01

### Initial Release
- Multi-battery support with capacity-weighted SOC calculation
- Smart grid charging decisions based on Nord Pool pricing
- Car charging recommendations with power limiting
- Solar optimization with hierarchical allocation
- Time-aware charging logic (night, winter, solar peak)
- Predictive price logic to wait for better rates
- Emergency charging overrides for critical SOC
- Comprehensive price analysis with position calculation
- Power flow monitoring and analysis
- Solar production forecasting integration
- Feed-in tariff optimization
- GUI-based configuration
- Real-time coordinator with 30-second updates
- Entity change listeners for immediate updates
- Configurable safety parameters
- Detailed decision reasoning
- Home Assistant dashboard cards

## Upgrade Guide

### From 1.0.0 to 2.0.0

1. **Backup your configuration** before upgrading
2. **Install the update** through HACS or manually
3. **Restart Home Assistant** 
4. **Check migration** - Configuration will be automatically migrated
5. **Verify operation** - Check Decision Diagnostics sensor
6. **Review new sensors** - Several new diagnostic sensors available

#### Breaking Changes
- None - Full backward compatibility maintained

#### New Configuration Options
- `grid_battery_charging_limit_soc` - SOC above which grid charging is selective (default: 80%)
- `base_grid_setpoint` - Base grid power limit (default: 2500W)

These will be added automatically with default values during migration.

## Support

For issues or questions about upgrading:
1. Check the [Troubleshooting Guide](TROUBLESHOOTING.md)
2. Review [Decision Diagnostics sensor](README.md#decision-validation)
3. Open an issue on [GitHub](https://github.com/emavap/electricity_planner/issues)
