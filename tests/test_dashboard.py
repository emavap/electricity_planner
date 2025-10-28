"""Dashboard automation tests for Electricity Planner."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from custom_components.electricity_planner import dashboard
from custom_components.electricity_planner.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry


def test_entity_reference_structure():
    """Test that ENTITY_REFERENCES contains valid entries."""
    assert len(dashboard.ENTITY_REFERENCES) > 0
    for ref in dashboard.ENTITY_REFERENCES:
        assert hasattr(ref, "placeholder")
        assert hasattr(ref, "unique_suffix")
        assert isinstance(ref.placeholder, str)
        assert isinstance(ref.unique_suffix, str)


def test_dashboard_url_path_generation():
    """Test that dashboard URL paths are generated correctly."""
    entry1 = MockConfigEntry(domain=DOMAIN, title="My Planner", data={})
    entry2 = MockConfigEntry(domain=DOMAIN, title="Another One", data={})

    path1 = dashboard._dashboard_url_path(entry1)
    path2 = dashboard._dashboard_url_path(entry2)

    # Should include slugified title
    assert "my-planner" in path1 or "planner" in path1
    # Should include entry ID prefix for uniqueness
    assert entry1.entry_id[:6] in path1
    # Different entries should produce different paths
    assert path1 != path2


def test_build_replacements():
    """Test entity ID replacement mapping."""
    entry = MockConfigEntry(domain=DOMAIN, title="Test", data={})

    entity_map = {
        f"{entry.entry_id}_price_analysis": "sensor.custom_price",
        f"{entry.entry_id}_battery_grid_charging": "binary_sensor.custom_battery",
    }

    replacements = dashboard._build_replacements(entry, entity_map)

    # Should create replacements for found entities
    assert "sensor.custom_price" in replacements.values()
    assert "binary_sensor.custom_battery" in replacements.values()


def test_apply_replacements():
    """Test template placeholder replacement."""
    template = "entity: sensor.electricity_planner_current_electricity_price"
    replacements = {
        "sensor.electricity_planner_current_electricity_price": "sensor.my_custom_price"
    }

    result = dashboard._apply_replacements(template, replacements)

    assert "sensor.my_custom_price" in result
    assert "sensor.electricity_planner_current_electricity_price" not in result


def test_configs_equal_same():
    """Test config equality detection for identical configs."""
    config_a = {"views": [{"title": "Test"}], "managed": True}
    config_b = {"views": [{"title": "Test"}], "managed": True}

    assert dashboard._configs_equal(config_a, config_b)


def test_configs_equal_different():
    """Test config equality detection for different configs."""
    config_a = {"views": [{"title": "Test A"}]}
    config_b = {"views": [{"title": "Test B"}]}

    assert not dashboard._configs_equal(config_a, config_b)


def test_load_template_text():
    """Test that template text can be loaded."""
    # Clear cache to test actual loading
    dashboard._load_template_text.cache_clear()

    template = dashboard._load_template_text()

    # Should load the actual template file
    assert isinstance(template, str)
    if template:  # If template exists
        assert "views:" in template or "REQUIRED" in template
