# Tests

## Running Tests

This integration requires Home Assistant to run tests. Install test dependencies:

```bash
pip install pytest homeassistant
```

Then run tests:

```bash
# Set PYTHONPATH so tests can import custom_components
PYTHONPATH=. pytest tests/test_strategies_smoke.py -v
```

**Note:** The integration uses relative imports (e.g., `from .defaults import ...`) which require the full Home Assistant environment. Standalone tests without HA are not possible due to this design.

## Test Files

- **`test_strategies_smoke.py`** - Basic smoke tests for all charging strategies
  - Tests emergency charging logic
  - Tests solar priority logic
  - Tests dynamic price analysis with None handling
  - Tests SOC-based charging
  - Tests strategy manager priority order

These tests verify that:
1. **None price handling works** - Nord Pool sensors sometimes return `unknown` during refresh
2. **Priority order is correct** - No duplicate priorities, strategies run in correct order
3. **Emergency charging overrides high prices** - Safety feature works
4. **Solar forecast affects selectivity** - Excellent solar makes dynamic pricing more selective

## Manual Testing

If you can't run automated tests, manually verify:

1. **Nord Pool refresh doesn't crash coordinator**
   - Watch logs when Nord Pool updates (usually around midnight)
   - Check that binary sensors still update properly

2. **Solar forecast affects charging decisions**
   - With excellent solar forecast (>80%), check that charging only happens at very low prices
   - With poor solar forecast (<40%), check that charging happens at okay prices

3. **Priority order is respected**
   - Emergency SOC (<15%) should charge regardless of price
   - Very low prices (bottom 30%) should always trigger charging
   - Dynamic threshold should be more selective than SOC-based strategy
