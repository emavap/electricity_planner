#!/usr/bin/env python3
"""
Dashboard Creation Diagnostic Script

This script helps diagnose why the dashboard isn't being created.
Run this in your Home Assistant environment to check:
1. Is Lovelace in storage mode?
2. Are the required entities registered?
3. Can the template be loaded?
4. What's the state of lovelace data?

Usage:
1. Copy this to your HA config directory
2. Run: python3 test_dashboard_debug.py
"""

import asyncio
import sys
from pathlib import Path

# Add your custom component path
sys.path.insert(0, str(Path(__file__).parent))

from custom_components.electricity_planner.dashboard import (
    TEMPLATE_FILENAME,
    _load_template_text,
)


def test_template_loading():
    """Test if the dashboard template can be loaded."""
    print("=" * 60)
    print("1. Testing Dashboard Template Loading")
    print("=" * 60)

    template_text = _load_template_text()

    if template_text:
        print(f"✅ Template loaded successfully!")
        print(f"   - Length: {len(template_text)} characters")
        print(f"   - First 100 chars: {template_text[:100]}")
    else:
        print(f"❌ Template NOT found: {TEMPLATE_FILENAME}")
        print(f"   - Check if file exists in: custom_components/electricity_planner/")

    print()


def check_ha_environment():
    """Check if we're in a Home Assistant environment."""
    print("=" * 60)
    print("2. Checking Home Assistant Environment")
    print("=" * 60)

    try:
        from homeassistant.core import HomeAssistant
        print("✅ Home Assistant imports working")

        from homeassistant.components.lovelace import const as ll_const
        from homeassistant.components.lovelace import dashboard as ll_dashboard
        print("✅ Lovelace imports working")

        # Check for constants
        print(f"   - DOMAIN: {ll_const.DOMAIN}")
        print(f"   - Has CONF_ALLOW_SINGLE_WORD: {hasattr(ll_const, 'CONF_ALLOW_SINGLE_WORD')}")

    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("   This script must be run in a Home Assistant environment")

    print()


def generate_log_filter():
    """Generate configuration for Home Assistant logger."""
    print("=" * 60)
    print("3. Recommended Logger Configuration")
    print("=" * 60)
    print("Add this to your configuration.yaml to see detailed logs:\n")
    print("logger:")
    print("  default: info")
    print("  logs:")
    print("    custom_components.electricity_planner: debug")
    print("    custom_components.electricity_planner.dashboard: debug")
    print("\nThen restart Home Assistant and check the logs.")
    print()


def print_troubleshooting_checklist():
    """Print a troubleshooting checklist."""
    print("=" * 60)
    print("4. Troubleshooting Checklist")
    print("=" * 60)
    print("""
After reloading the integration, check your Home Assistant logs for these messages:

EXPECTED MESSAGES (in order):
✅ "Starting dashboard creation for entry: ..."
✅ "Lovelace handles retrieved successfully"
✅ "Entity map built with X entities"
✅ "Dashboard template loaded successfully (X chars)"
✅ "Built X entity replacements"
✅ "Dashboard template parsed successfully"
✅ "Dashboard URL path: electricity-planner-..."
✅ "Creating new dashboard with url_path=..."
✅ "Successfully created dashboard: ..."
✅ "Saving dashboard config with X views"
✅ "Dashboard config saved successfully"
✅ "Dashboard setup completed for entry: ..."

COMMON FAILURE POINTS:
❌ "Lovelace handles not available"
   → Lovelace is not in storage mode OR Home Assistant not fully started
   → Check: Settings > Dashboards > Three dots menu > "Take control"

❌ "No registered entities for X; skipping dashboard creation"
   → Entities not registered yet (timing issue)
   → Try: Reload the integration

❌ "Dashboard template X missing; skipping creation"
   → Template file not found
   → Check: custom_components/electricity_planner/dashboard_template.yaml exists

❌ "Unable to register managed dashboard X: ..."
   → Validation error (e.g., URL path already in use)
   → Check: No existing dashboard with same URL

TO CHECK LOVELACE MODE:
1. Go to Settings > Dashboards
2. Click the three-dot menu on any dashboard
3. If you see "Take control", you're in AUTO mode (not storage mode)
4. Click "Take control" to switch to storage mode
5. Then reload the Electricity Planner integration
""")


if __name__ == "__main__":
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + " " * 10 + "ELECTRICITY PLANNER DASHBOARD DEBUGGER" + " " * 10 + "║")
    print("╚" + "═" * 58 + "╝")
    print()

    test_template_loading()
    check_ha_environment()
    generate_log_filter()
    print_troubleshooting_checklist()

    print("=" * 60)
    print("Next Steps:")
    print("=" * 60)
    print("1. Enable debug logging (see section 3 above)")
    print("2. Reload the Electricity Planner integration")
    print("3. Check Home Assistant logs")
    print("4. Look for the messages listed in section 4")
    print("5. Share the relevant log lines if you need further help")
    print()
