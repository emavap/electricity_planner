# Electricity Planner – Project Summary

This is a Home Assistant custom integration that analyses live Nord Pool prices, battery SOC and solar production to recommend when you should charge from the grid. It never controls hardware directly; instead it exposes boolean decisions, grid power limits and human-readable reasons that you can feed into your own automations.

## Quick Facts

- Supports **multiple batteries** at once, with emergency overrides and SOC-aware safety limits.
- Produces **separate decisions** for home batteries and electric vehicles, keeping batteries first in line while allowing solar surplus to top up cars only when storage is already near full.
- Optional **dynamic price threshold** mode helps you target the most economical hours inside your maximum price ceiling.
- Publishes a **Decision Diagnostics sensor** with the full analysis, so you can see exactly why a recommendation changed.
- Contract-specific pricing: configure multiplier/offset adjustments for both consumption and feed-in so the planner works with the exact €/kWh that appear on your bill.

## Documentation Map

- [README](README.md) – installation, configuration wizard, decision flow, detailed outputs, and automation ideas.
- [DASHBOARD](DASHBOARD.md) – Lovelace visualisation examples (ApexCharts, manual override buttons, etc.).
- [CHANGELOG](CHANGELOG.md) – release notes and migration history.
- [CLAUDE](CLAUDE.md) – contributor/developer guide for AI coding assistants.

### Installation Recap

1. Add the repository via HACS (Integration) **or** copy `custom_components/electricity_planner` into `<config>/custom_components/`.
2. Restart Home Assistant and add the integration from **Settings → Devices & Services → Add Integration**.
3. Use the configuration wizard to select sensors, thresholds, and limits. Reopen **Configure** later to tweak options; defaults now reflect saved choices.

### Testing & Contributions

- The project ships with a Docker-based pytest harness:

  ```bash
  docker build -f Dockerfile.tests -t electricity-planner-tests .
  docker run --rm -v "$PWD":/app -w /app -e PYTHONPATH=/app electricity-planner-tests pytest
  ```

- Enable debug logging in Home Assistant:

  ```yaml
  logger:
    logs:
      custom_components.electricity_planner: debug
  ```

- Pull requests should include updated documentation when behaviour or configuration changes. The README is the authoritative user-facing reference.
