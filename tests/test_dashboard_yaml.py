"""Validate structure of the bundled dashboard YAML."""
from pathlib import Path

import yaml


def test_dashboard_yaml_is_valid_yaml() -> None:
    """Ensure the dashboard file exists and parses as YAML."""
    dashboard_path = Path(__file__).resolve().parents[1] / "electricity_planner_dashboard.yaml"
    assert dashboard_path.exists(), "Dashboard YAML file missing"

    data = yaml.safe_load(dashboard_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "Dashboard YAML should resolve to a mapping"

    views = data.get("views")
    assert isinstance(views, list) and views, "Dashboard must contain at least one view"

    for view in views:
        assert "title" in view, "Each view must define a title"
        assert "cards" in view, "Each view must define cards"
        cards = view["cards"]
        assert isinstance(cards, list) and cards, "View cards must be a non-empty list"
        for card in cards:
            assert "type" in card, "Every top-level card must declare a type"

