# Release v4.2.0 - Automatic Dashboard Provisioning

## 🎉 Major Feature: Automatic Dashboard Creation

The integration now **automatically creates a fully-configured dashboard** when you install it! No more manual YAML editing or dashboard imports required.

### ✨ What's New

- **Auto-Generated Dashboard**: A beautiful, ready-to-use dashboard appears in your sidebar automatically
- **Per-Instance Dashboards**: Each integration instance gets its own dashboard with the correct entity IDs
- **Sidebar Integration**: Dashboard appears with ⚡ lightning bolt icon in Home Assistant sidebar
- **Smart Entity Mapping**: Template automatically filled with your configured entities
- **Managed Updates**: Dashboard stays in sync with your integration configuration

### 📊 Dashboard Features

The auto-generated dashboard includes:

- **Price Gauges**: Dynamic buy/sell electricity price visualization
- **Nord Pool Integration**: Current, min, max, and next-hour price displays
- **Battery Status**: SOC monitoring and charging decision indicators
- **Solar Production**: Live solar surplus and feed-in status
- **Decision Diagnostics**: Complete visibility into charging logic
- **Manual Override Controls**: Quick buttons for force charge/wait
- **Historical Charts**: ApexCharts for price and power trends
- **Threshold Monitoring**: All configurable thresholds displayed

### 🔧 Technical Improvements

- **Proper HA API Usage**: Uses official DashboardsCollection and LovelaceStorage APIs
- **Frontend Panel Registration**: Dashboard properly registered in Home Assistant UI
- **Async File Operations**: Non-blocking template loading via executor
- **Future-Proof**: Fixed Home Assistant 2026.2 deprecation warnings
- **Comprehensive Logging**: Detailed debug info for troubleshooting
- **Error Resilience**: Graceful fallbacks for missing entities or data

### 📦 Requirements

The generated dashboard uses these **HACS frontend cards** (install separately):

1. **[gauge-card-pro](https://github.com/benjamin-dcs/gauge-card-pro)** - For price gauges
2. **[apexcharts-card](https://github.com/RomRider/apexcharts-card)** - For historical charts
3. **[button-card](https://github.com/custom-cards/button-card)** - For manual override buttons

> **Note**: The dashboard will show card errors until these HACS cards are installed.

### 🚀 How It Works

1. **Install/Update** the Electricity Planner integration
2. **Reload** the integration (or restart Home Assistant)
3. **Check your sidebar** - Look for "Electricity Planner" with ⚡ icon
4. **Install HACS cards** if you see "Custom element doesn't exist" errors
5. **Enjoy** your fully-configured dashboard!

### 🔍 Dashboard Customization

The auto-created dashboard is **fully editable**:

- Click the three-dot menu → "Edit Dashboard"
- Modify cards, layout, colors, etc.
- Your changes are preserved across integration updates
- Dashboard marked as "managed" with metadata tracking

### 🐛 Bug Fixes

- Fixed Home Assistant 2026.2 deprecation warning (lovelace_data access)
- Fixed Home Assistant 2025.12 deprecation warning (options flow config_entry)
- Fixed blocking I/O operations in event loop
- Improved error handling and logging throughout dashboard creation

### 📝 Migration from 4.1.x

If you're upgrading from a previous version:

- Your existing manual dashboard (if any) is **not affected**
- A new managed dashboard will be created automatically
- Both dashboards can coexist
- You can delete your old manual dashboard if desired

### 🙏 Credits

Dashboard implementation pattern inspired by the [HeatingControl](https://github.com/emavap/HeatingControl) integration.

### 🤖 AI Collaboration

This feature was developed with assistance from [Claude Code](https://claude.com/claude-code).

---

## Upgrade Instructions

1. **Update via HACS** or manually copy files
2. **Restart Home Assistant** (recommended) or reload the integration
3. **Install HACS cards** listed above for full functionality
4. **Check sidebar** for the new Electricity Planner dashboard

## Known Limitations

- Dashboard created only in **storage mode** (not YAML mode)
- Requires entities to be registered before dashboard creation (usually instant)
- Custom card errors appear until HACS cards are installed (expected)

## Troubleshooting

Enable debug logging to see detailed dashboard creation steps:

```yaml
logger:
  logs:
    custom_components.electricity_planner.dashboard: debug
```

Then check Home Assistant logs for dashboard creation messages.

---

**Full Changelog**: [v4.1.4...v4.2.0](https://github.com/emavap/electricity_planner/compare/v4.1.4...v4.2.0)
