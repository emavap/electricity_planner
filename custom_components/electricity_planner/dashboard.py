"""Dashboard creation service for Electricity Planner."""
from __future__ import annotations

import json
import logging
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

DASHBOARD_CONFIG = {
    "type": "vertical-stack",
    "cards": [
        {
            "type": "custom:mushroom-template-card",
            "primary": "Electricity Planner",
            "secondary": """
                {% if is_state('binary_sensor.electricity_planner_battery_grid_charging', 'on') and is_state('binary_sensor.electricity_planner_car_grid_charging', 'on') %}
                  🔋⚡ Charge Both from Grid
                {% elif is_state('binary_sensor.electricity_planner_battery_grid_charging', 'on') %}
                  🔋 Charge Battery from Grid
                {% elif is_state('binary_sensor.electricity_planner_car_grid_charging', 'on') %}
                  ⚡ Charge Car from Grid
                {% else %}
                  ⏳ Wait - No Grid Charging
                {% endif %}
            """,
            "icon": """
                {% if is_state('binary_sensor.electricity_planner_battery_grid_charging', 'on') or is_state('binary_sensor.electricity_planner_car_grid_charging', 'on') %}
                  mdi:flash
                {% else %}
                  mdi:flash-off
                {% endif %}
            """,
            "icon_color": """
                {% if is_state('binary_sensor.electricity_planner_battery_grid_charging', 'on') or is_state('binary_sensor.electricity_planner_car_grid_charging', 'on') %}
                  green
                {% else %}
                  red
                {% endif %}
            """,
            "badge_icon": "mdi:currency-eur",
            "badge_color": """
                {% set price = states('sensor.electricity_planner_price_analysis') | float(0) %}
                {% if price < 0.10 %}
                  green
                {% elif price < 0.20 %}
                  orange
                {% else %}
                  red
                {% endif %}
            """
        },
        {
            "type": "custom:mushroom-title-card",
            "title": "📊 Historical Data & Trends"
        },
        {
            "type": "custom:apexcharts-card",
            "header": {
                "show": True,
                "title": "⚡ Electricity Price Trends (24h)",
                "show_states": True,
                "colorize_states": True
            },
            "graph_span": "24h",
            "span": {"end": "day"},
            "apex_config": {
                "chart": {"height": 280},
                "stroke": {"curve": "smooth", "width": 2},
                "fill": {"type": "gradient", "gradient": {"shadeIntensity": 0.1}},
                "yaxis": [{
                    "decimalsInFloat": 3,
                    "min": 0,
                    "title": {"text": "Price (€/kWh)"}
                }],
                "annotations": {
                    "yaxis": [{
                        "y": 0.15,
                        "borderColor": "#FFA500",
                        "label": {
                            "text": "Price Threshold",
                            "style": {"color": "#FFA500"}
                        }
                    }]
                }
            },
            "series": [
                {
                    "entity": "sensor.electricity_planner_price_analysis",
                    "attribute": "current_price",
                    "name": "Current Price",
                    "color": "#2196F3",
                    "stroke_width": 3
                }
            ]
        },
        {
            "type": "custom:apexcharts-card",
            "header": {
                "show": True,
                "title": "🔋 Battery SOC & Charging Decisions (24h)",
                "show_states": True,
                "colorize_states": True
            },
            "graph_span": "24h",
            "apex_config": {
                "chart": {"height": 280},
                "stroke": {"curve": "smooth", "width": 3},
                "yaxis": [
                    {
                        "id": "soc",
                        "min": 0,
                        "max": 100,
                        "title": {"text": "Battery SOC (%)"},
                        "axisBorder": {"show": True}
                    },
                    {
                        "id": "charging",
                        "opposite": True,
                        "min": 0,
                        "max": 1.2,
                        "title": {"text": "Charging Decision"}
                    }
                ]
            },
            "series": [
                {
                    "entity": "sensor.electricity_planner_battery_analysis",
                    "name": "Battery SOC Average",
                    "yaxis_id": "soc",
                    "color": "#4CAF50",
                    "stroke_width": 3
                },
                {
                    "entity": "binary_sensor.electricity_planner_battery_grid_charging",
                    "name": "Battery Grid Charging",
                    "yaxis_id": "charging",
                    "color": "#FF5722",
                    "stroke_width": 2,
                    "transform": "return hass.states[entity.entity_id].state === 'on' ? 1 : 0;"
                },
                {
                    "entity": "binary_sensor.electricity_planner_car_grid_charging",
                    "name": "Car Grid Charging",
                    "yaxis_id": "charging",
                    "color": "#9C27B0",
                    "stroke_width": 2,
                    "transform": "return hass.states[entity.entity_id].state === 'on' ? 1 : 0;"
                }
            ]
        }
    ]
}

SERVICE_SCHEMA = vol.Schema({
    vol.Optional("dashboard_title", default="Electricity Planner"): cv.string,
    vol.Optional("dashboard_url", default="electricity-planner"): cv.string,
})


async def async_setup_dashboard_services(hass: HomeAssistant) -> None:
    """Set up dashboard creation services."""
    
    async def create_dashboard(call: ServiceCall) -> None:
        """Create the Electricity Planner dashboard."""
        dashboard_title = call.data.get("dashboard_title", "Electricity Planner")
        dashboard_url = call.data.get("dashboard_url", "electricity-planner")
        
        try:
            # Get the lovelace config
            lovelace_config = hass.data.get("lovelace", {})
            
            # Create new dashboard configuration
            dashboard_config = {
                "mode": "yaml",
                "title": dashboard_title,
                "icon": "mdi:lightning-bolt",
                "show_in_sidebar": True,
                "require_admin": False,
                "views": [
                    {
                        "title": "Overview",
                        "path": "overview", 
                        "icon": "mdi:view-dashboard",
                        "cards": DASHBOARD_CONFIG["cards"]
                    }
                ]
            }
            
            # Use the lovelace service to create the dashboard
            await hass.services.async_call(
                "lovelace",
                "save_config",
                {
                    "url_path": dashboard_url,
                    "config": dashboard_config,
                    "mode": "yaml"
                }
            )
            
            _LOGGER.info(
                "Successfully created Electricity Planner dashboard at /lovelace/%s",
                dashboard_url
            )
            
        except Exception as e:
            _LOGGER.error("Failed to create dashboard: %s", e)
            raise
    
    hass.services.async_register(
        "electricity_planner",
        "create_dashboard", 
        create_dashboard,
        schema=SERVICE_SCHEMA
    )