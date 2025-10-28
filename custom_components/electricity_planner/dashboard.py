"""Dashboard automation helpers for the Electricity Planner integration.

This module automatically provisions a managed Lovelace dashboard for each config entry,
eliminating the need for manual YAML configuration while maintaining per-instance entity IDs.

IMPORTANT: The generated dashboard requires these HACS frontend cards to function properly:
- gauge-card-pro (https://github.com/benjamin-dcs/gauge-card-pro) - for price gauges
- apexcharts-card (https://github.com/RomRider/apexcharts-card) - for historical charts
- button-card (https://github.com/custom-cards/button-card) - for manual override controls

Users must install these cards from HACS before the dashboard will render correctly.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import yaml

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from homeassistant.components.lovelace import dashboard as ll_dashboard

_LOGGER = logging.getLogger(__name__)

MANAGED_KEY = "electricity_planner_managed"
MANAGED_VERSION = 1
TEMPLATE_FILENAME = "dashboard_template.yaml"

# How long to wait for the core entities created by the platforms to appear in the
# entity registry before giving up on automatic dashboard creation (seconds)
ENTITY_WAIT_TIMEOUT = 30
ENTITY_WAIT_INTERVAL = 1.0


@dataclass(frozen=True)
class EntityReference:
    """Mapping between template placeholders and unique ID suffixes."""

    placeholder: str
    unique_suffix: str


ENTITY_REFERENCES: tuple[EntityReference, ...] = (
    EntityReference("binary_sensor.electricity_planner_battery_charge_from_grid", "battery_grid_charging"),
    EntityReference("binary_sensor.electricity_planner_car_charge_from_grid", "car_grid_charging"),
    EntityReference("binary_sensor.electricity_planner_data_nord_pool_available", "data_availability"),
    EntityReference("binary_sensor.electricity_planner_price_below_threshold", "low_price"),
    EntityReference("binary_sensor.electricity_planner_solar_producing_power", "solar_production"),
    # Feed-in sensor has two placeholder entries to support backward compatibility
    # and different entity naming conventions. Both map to the same unique_id suffix.
    EntityReference("binary_sensor.electricity_planner_solar_feed_in_grid", "feedin_solar"),
    EntityReference("binary_sensor.solar_feed_in_grid", "feedin_solar"),
    EntityReference("sensor.electricity_planner_current_electricity_price", "price_analysis"),
    EntityReference("sensor.electricity_planner_decision_diagnostics", "decision_diagnostics"),
    EntityReference("sensor.electricity_planner_decision_diagnostics_diagnostic", "decision_diagnostics"),
    EntityReference("sensor.electricity_planner_battery_soc_average", "battery_analysis"),
    EntityReference("sensor.electricity_planner_car_charger_limit", "charger_limit"),
    EntityReference("sensor.electricity_planner_grid_setpoint", "grid_setpoint"),
    EntityReference("sensor.electricity_planner_solar_surplus_power", "power_analysis"),
    EntityReference("sensor.electricity_planner_data_unavailable_duration", "data_unavailable_duration"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_current_feed_in_price", "feedin_price"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_feed_in_price_threshold", "feedin_price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_price_threshold", "price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_price_forecast_insights", "forecast_insights"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_nord_pool_prices", "nordpool_prices"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_significant_solar_threshold", "significant_solar_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_very_low_price_threshold", "very_low_price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_emergency_soc_threshold", "emergency_soc_threshold"),
    EntityReference("switch.electricity_planner_car_permissive_mode", "car_permissive_mode"),
)

CORE_ENTITY_SUFFIXES: tuple[str, ...] = (
    "battery_grid_charging",
    "car_grid_charging",
    "price_analysis",
)


async def async_setup_or_update_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create or update the managed dashboard for the given config entry."""
    _LOGGER.info("Starting dashboard setup for entry: %s", entry.title or entry.entry_id)

    try:
        entity_map = await _async_wait_for_entity_map(hass, entry)
    except asyncio.TimeoutError:
        _LOGGER.warning(
            "Timed out waiting for Electricity Planner entities before creating dashboard. "
            "Will attempt to create dashboard with currently available entities."
        )
        entity_map = _build_entity_map(hass, entry)

    if not entity_map:
        _LOGGER.warning(
            "No entities registered for entry %s; skipping dashboard auto-creation. "
            "This usually means entities haven't been created yet. Try reloading the integration.",
            entry.entry_id
        )
        return

    replacements = _build_replacements(entry, entity_map)
    template_text = _load_template_text()
    if not template_text:
        _LOGGER.error(
            "Dashboard template %s not found; skipping auto-creation. "
            "Please ensure the file exists in the integration directory.",
            TEMPLATE_FILENAME
        )
        return

    rendered_template = _apply_replacements(template_text, replacements)
    try:
        dashboard_config = yaml.safe_load(rendered_template)
    except yaml.YAMLError as error:
        _LOGGER.error("Failed to parse dashboard template: %s", error)
        return

    if not isinstance(dashboard_config, dict) or "views" not in dashboard_config:
        _LOGGER.debug("Dashboard template did not resolve to a Lovelace config; skipping")
        return

    dashboard_config[MANAGED_KEY] = {
        "entry_id": entry.entry_id,
        "version": MANAGED_VERSION,
    }

    title = entry.title or "Electricity Planner"
    url_path = _dashboard_url_path(entry)

    try:
        existing_config = await ll_dashboard.async_get_dashboard(hass, url_path)
        managed_by_us = existing_config.get(MANAGED_KEY, {}).get("entry_id") == entry.entry_id
        if not managed_by_us:
            _LOGGER.debug(
                "Dashboard %s already exists and is not managed by Electricity Planner; leaving untouched",
                url_path,
            )
            return

        if _configs_equal(existing_config, dashboard_config):
            _LOGGER.debug("Dashboard %s already up to date", url_path)
            return

        await ll_dashboard.async_update_dashboard(hass, url_path, dashboard_config)
        _LOGGER.info("Updated Electricity Planner dashboard at /d/%s", url_path)
    except ll_dashboard.LovelaceNotFoundError:
        try:
            await ll_dashboard.async_create_dashboard(
                hass,
                url_path,
                dashboard_config,
                title=title,
                icon="mdi:lightning-bolt",
                show_in_sidebar=True,
                require_admin=False,
            )
            _LOGGER.info("Created Electricity Planner dashboard at /d/%s", url_path)
        except (ll_dashboard.LovelaceError, HomeAssistantError) as error:
            _LOGGER.warning("Unable to create Electricity Planner dashboard: %s", error)
    except (ll_dashboard.LovelaceError, HomeAssistantError) as error:
        _LOGGER.warning("Unable to update Electricity Planner dashboard: %s", error)


async def async_remove_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the managed dashboard when the config entry is unloaded."""
    url_path = _dashboard_url_path(entry)

    try:
        existing_config = await ll_dashboard.async_get_dashboard(hass, url_path)
    except ll_dashboard.LovelaceNotFoundError:
        return
    except (ll_dashboard.LovelaceError, HomeAssistantError) as error:
        _LOGGER.debug("Unable to inspect dashboard %s during removal: %s", url_path, error)
        return

    managed_by_us = existing_config.get(MANAGED_KEY, {}).get("entry_id") == entry.entry_id
    if not managed_by_us:
        return

    try:
        await ll_dashboard.async_delete_dashboard(hass, url_path)
        _LOGGER.info("Removed Electricity Planner dashboard at /d/%s", url_path)
    except (ll_dashboard.LovelaceError, HomeAssistantError) as error:
        _LOGGER.debug("Unable to remove dashboard %s: %s", url_path, error)


async def _async_wait_for_entity_map(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Poll the entity registry until the core entities for this entry exist."""
    end = hass.loop.time() + ENTITY_WAIT_TIMEOUT
    while True:
        entity_map = _build_entity_map(hass, entry)
        if all(f"{entry.entry_id}_{suffix}" in entity_map for suffix in CORE_ENTITY_SUFFIXES):
            return entity_map
        if hass.loop.time() >= end:
            raise asyncio.TimeoutError
        await asyncio.sleep(ENTITY_WAIT_INTERVAL)


def _build_entity_map(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, str]:
    """Return a mapping of unique_id -> entity_id for the config entry."""
    registry = er.async_get(hass)
    mapped: dict[str, str] = {}
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.entity_id:
            mapped[entity_entry.unique_id] = entity_entry.entity_id
    return mapped


def _build_replacements(entry: ConfigEntry, entity_map: dict[str, str]) -> dict[str, str]:
    """Create replacement map from template placeholders to real entity IDs."""
    replacements: dict[str, str] = {}

    for ref in ENTITY_REFERENCES:
        unique_id = f"{entry.entry_id}_{ref.unique_suffix}"
        entity_id = entity_map.get(unique_id)
        if entity_id:
            replacements[ref.placeholder] = entity_id

    return replacements


def _dashboard_url_path(entry: ConfigEntry) -> str:
    """Return a deterministic dashboard URL path for the entry."""
    slug = slugify(entry.title or "")
    if not slug:
        slug = "planner"
    suffix = entry.entry_id[:6]
    return f"electricity-planner-{slug}-{suffix}"


def _configs_equal(config_a: dict[str, Any], config_b: dict[str, Any]) -> bool:
    """Return True when two dashboard configs are logically equivalent."""
    return json.dumps(config_a, sort_keys=True) == json.dumps(config_b, sort_keys=True)


@lru_cache(maxsize=1)
def _load_template_text() -> str:
    """Load the bundled dashboard template."""
    try:
        template_path = resources.files(__package__) / TEMPLATE_FILENAME
    except FileNotFoundError:
        return ""

    try:
        return template_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _apply_replacements(template: str, replacements: dict[str, str]) -> str:
    """Apply entity replacements to the raw template text."""
    rendered = template
    for placeholder, entity_id in replacements.items():
        rendered = rendered.replace(placeholder, entity_id)
    return rendered
