# Decision Engine

`decision_engine.py` consumes a coordinator snapshot and emits a structured decision used by every entity platform.

## Pipeline

1. **Input validation** — sanity-check prices, SOC, PV, load. Missing inputs degrade to "permissive" defaults.
2. **Forecast resolution** — resolve today's solar forecast from cache, with overnight fallback.
3. **Dynamic threshold** — compute the buy/sell thresholds from Nord Pool prices (`dynamic_threshold.py`).
4. **Strategy chain** — run ordered strategies (`battery_charging`, `car_charging`, `arbitrage_mode`, `negative_buy`, `feedin_decision`, …). Each strategy may add reason lines and override values.
5. **Solar allocation** — distribute PV power across batteries, EV charger, and house load (`_allocate_solar_power`).
6. **Charger limit** — apply peak-import limits, inverter derating, and 3-phase safety bounds (`charger_limit.py`, `inverter_derating.py`).
7. **Grid setpoint** — translate the decision into a Victron/Huawei-style setpoint (`grid_setpoint.py`).
8. **Manual overrides** — apply any user toggles last (`manual_overrides.py`).
9. **Trace** — record the reasoning chain for diagnostics.

## Output shape (logical)

| Field                       | Type        | Notes                                 |
| --------------------------- | ----------- | ------------------------------------- |
| `battery_charging_active`   | bool        | Are batteries currently being charged |
| `car_charging_active`       | bool        | EV charging gate                      |
| `charger_limit_watts`       | int         | Power cap for the EV charger          |
| `grid_setpoint_watts`       | int         | Inverter setpoint                     |
| `feedin_allowed`            | bool        | Export gate                           |
| `arbitrage_mode`            | enum        | idle / charge / discharge             |
| `reasons`                   | list[str]   | Human-readable trace                  |

## Permissive mode

When required inputs are missing, the engine degrades to a documented "permissive" fallback rather than failing the tick. See `test_permissive_mode.py`.
