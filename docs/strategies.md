# Strategies

Strategies are ordered, composable decision policies. Each one inspects the snapshot, the running decision blob, and may:

- set or clear binary gates (e.g. `battery_charging_active`)
- propose numeric limits (later clamped by safety steps)
- append reason lines to the trace

## Chain order (typical)

1. `manual_overrides` — early read of user toggles (full overrides applied at the end as well).
2. `negative_buy` — opportunistic charging when prices are very low or negative.
3. `arbitrage_mode` — daily arbitrage based on the dynamic threshold and SOC headroom.
4. `battery_charging` — grid-charging policy for top-up / sunny-day / peak-shaving cases.
5. `car_charging` — EV gating against price, SOC, and solar headroom.
6. `feedin_decision` — export gate (price floor, inverter derating, peak-export rules).
7. `charger_limit` — final power allocation across PV / battery / grid sources.
8. `grid_setpoint` — translate the unified decision into an inverter setpoint.

## Adding a strategy

1. Add a module under `custom_components/electricity_planner/` exposing a callable `(snapshot, decision) -> None` (or returning a partial update).
2. Wire it into the chain inside `decision_engine.py`.
3. Add a smoke test in `tests/test_strategies_smoke.py` plus targeted unit tests.
4. Document the new gate or limit here and in `decision-engine.md`.
