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

### Running Tests with Docker

You can run the full test suite inside Docker without installing any Python dependencies locally:

```bash
docker build -f Dockerfile.tests -t electricity-planner-tests .
docker run --rm -v "$(pwd)":/app electricity-planner-tests
```

The image installs the development dependencies declared in `requirements-dev.txt` and executes `pytest tests -v`. Mounting the repository into `/app` lets you iterate on code without rebuilding unless dependencies change.

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
4. **Solar surplus affects selectivity** - Significant surplus makes dynamic pricing more selective

## Manual Testing

If you can't run automated tests, manually verify:

1. **Nord Pool refresh doesn't crash coordinator**
   - Watch logs when Nord Pool updates (usually around midnight)
   - Check that binary sensors still update properly

2. **Solar surplus affects charging decisions**
   - With significant surplus (>1 kW), check that charging only happens at very low prices
   - With little or no surplus, check that charging happens when prices are merely okay

3. **Priority order is respected**
   - Emergency SOC (<15%) should charge regardless of price
   - Very low prices (bottom 30%) should always trigger charging
   - Dynamic threshold should be more selective than SOC-based strategy
