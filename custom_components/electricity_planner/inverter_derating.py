"""Inverter derating target calculation.

Extracted from ``decision_engine.py`` as a standalone collaborator so the
logic can evolve (and be tested) independently of the rest of the
decision engine. The calculator is a pure function of its inputs: given
a snapshot of the current decision cycle it returns the recommended
inverter output cap in Watts plus supporting diagnostic fields.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from .decision_engine import EngineSettings


def _safe_optional_float(value: Any) -> float | None:
    """Best-effort conversion of arbitrary input to float."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_optional_datetime(value: Any) -> datetime | None:
    """Best-effort conversion of arbitrary input to an aware datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is not None:
            return parsed
        return parsed.replace(tzinfo=timezone.utc)
    return None


class InverterDeratingCalculator:
    """Compute the recommended inverter derating target in Watts.

    The grid power sensor convention is:
    negative = import, positive = export.
    The calculator inverts this internally to use the planner convention
    (positive = import, negative = export) for its logic.
    """

    def __init__(self, settings: "EngineSettings") -> None:
        self._settings = settings

    def calculate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Calculate a recommended inverter derating target in Watts."""
        max_inverter_power = self._settings.max_inverter_power
        export_limit_w = self._settings.inverter_export_limit
        export_deadband_w = float(self._settings.inverter_export_deadband)
        unused_release_minutes = self._settings.inverter_derating_unused_release_minutes
        soc_bypass_threshold = self._settings.inverter_derating_soc_bypass_threshold
        feed_allowed = bool(data.get("feedin_solar", False))
        no_alarm = {
            "inverter_derating_alarm": False,
            "inverter_derating_alarm_reason": "No derating alarm",
            "inverter_derating_unreached_since": None,
        }

        # Note: even when Negative Arbitrage Buy curtailment is flagged, we no
        # longer force the inverter to 0W here. Derating is reserved for the
        # exporting case (handled below via the export deadband). When the site
        # is importing - including paid-to-consume arbitrage-buy slots - the
        # normal logic leaves the inverter unrestricted instead of derating.
        if feed_allowed:
            return {
                "inverter_derating_target": int(max_inverter_power),
                "inverter_derating_reason": "Feed-in allowed - set inverter to max power",
                **no_alarm,
            }

        battery_analysis = data.get("battery_analysis") or {}
        average_soc_raw = battery_analysis.get("average_soc")
        average_soc = _safe_optional_float(average_soc_raw)
        battery_below_bypass_threshold = (
            average_soc is not None and average_soc < soc_bypass_threshold
        )

        house_consumption_w = _safe_optional_float(data.get("house_consumption"))
        safe_output_w = (
            max(0.0, house_consumption_w + export_limit_w)
            if house_consumption_w is not None
            else None
        )
        solar_production_raw = data.get("solar_production")
        solar_production_w = _safe_optional_float(solar_production_raw)
        if solar_production_w is not None:
            solar_production_w = max(0.0, solar_production_w)
        previous_target_w = _safe_optional_float(
            data.get("previous_inverter_derating_target")
        )
        if previous_target_w is not None:
            previous_target_w = max(
                0.0, min(float(max_inverter_power), previous_target_w)
            )
        previous_unreached_since = _safe_optional_datetime(
            data.get("previous_inverter_derating_unreached_since")
        )
        evaluated_at = (
            _safe_optional_datetime(data.get("inverter_derating_evaluated_at"))
            or dt_util.utcnow()
        )

        grid_power_raw = data.get("grid_power")
        grid_power_w = _safe_optional_float(grid_power_raw)
        # Invert sign: sensor.grid uses negative=import, positive=export.
        # The calculator uses the opposite convention (positive=import).
        if grid_power_w is not None:
            grid_power_w = -grid_power_w
        previous_grid_power_w = _safe_optional_float(data.get("previous_grid_power"))
        if previous_grid_power_w is not None:
            previous_grid_power_w = -previous_grid_power_w

        # Preferred control path: use the current operating point and hold a
        # simple deadband around the configured export target.
        if solar_production_w is not None and grid_power_w is not None:
            export_power_w = max(0.0, -grid_power_w)
            previous_export_power_w = (
                max(0.0, -previous_grid_power_w)
                if previous_grid_power_w is not None
                else export_power_w
            )
            smoothed_export_power_w = (export_power_w + previous_export_power_w) / 2.0
            release_step_w = 100.0
            lower_export_w = max(0.0, export_limit_w - export_deadband_w)
            upper_export_w = export_limit_w + export_deadband_w
            relax_cap_after = timedelta(minutes=unused_release_minutes)
            previous_derating_active = (
                previous_target_w is not None
                and previous_target_w < float(max_inverter_power)
            )
            export_below_band = smoothed_export_power_w < lower_export_w
            current_export_above_band = export_power_w > upper_export_w
            export_above_band = (
                current_export_above_band or smoothed_export_power_w > upper_export_w
            )
            # Relaxation is allowed when export is at or below the upper band
            # (not just below the lower band).  This lets the relaxation timer
            # survive brief fluctuations into the deadband instead of resetting.
            allow_relaxation_progress = (
                previous_derating_active and not export_above_band
            )
            if (
                previous_derating_active
                and previous_unreached_since is not None
                and not export_above_band
            ):
                unreached_since = previous_unreached_since
            else:
                unreached_since = None
            if (
                previous_derating_active
                and export_below_band
                and unreached_since is None
            ):
                unreached_since = evaluated_at

            def should_relax_cap_upward() -> bool:
                return (
                    allow_relaxation_progress
                    and unreached_since is not None
                    and evaluated_at - unreached_since >= relax_cap_after
                )

            if battery_below_bypass_threshold and not export_above_band:
                return {
                    "inverter_derating_target": int(max_inverter_power),
                    "inverter_derating_reason": (
                        f"Battery SOC {average_soc:.0f}% < {soc_bypass_threshold:.0f}% and "
                        f"averaged export {smoothed_export_power_w:.0f}W is within the "
                        "low-SOC tolerance - "
                        "keep inverter unrestricted so solar can charge the battery"
                    ),
                    **no_alarm,
                }

            if export_below_band:
                if (
                    house_consumption_w is not None
                    and house_consumption_w > solar_production_w
                    and safe_output_w is not None
                ):
                    recalculated_target_w = min(
                        float(max_inverter_power),
                        safe_output_w,
                    )
                    if (
                        previous_target_w is None
                        or recalculated_target_w > previous_target_w
                    ):
                        return {
                            "inverter_derating_target": int(recalculated_target_w),
                            "inverter_derating_reason": (
                                f"Feed-in blocked, but house consumption "
                                f"{house_consumption_w:.0f}W already exceeds current solar "
                                f"{solar_production_w:.0f}W - recalculate the inverter cap "
                                f"immediately to house {house_consumption_w:.0f}W + export "
                                f"target {export_limit_w}W"
                            ),
                            **no_alarm,
                        }
                if grid_power_w > 0:
                    operating_point_target_w = min(
                        float(max_inverter_power),
                        max(
                            0.0,
                            solar_production_w + grid_power_w + export_limit_w,
                        ),
                    )
                    if (
                        previous_target_w is None
                        or operating_point_target_w > previous_target_w
                    ):
                        return {
                            "inverter_derating_target": int(operating_point_target_w),
                            "inverter_derating_reason": (
                                f"Feed-in blocked, but the site is already importing "
                                f"{grid_power_w:.0f}W while solar is capped at "
                                f"{solar_production_w:.0f}W - recalculate the inverter cap "
                                f"immediately toward current solar + grid import + export "
                                f"target ({int(operating_point_target_w)}W)"
                            ),
                            **no_alarm,
                        }
                if should_relax_cap_upward() and previous_target_w is not None:
                    reopened_target_w = min(
                        float(max_inverter_power),
                        max(0.0, previous_target_w + release_step_w),
                    )
                    return {
                        "inverter_derating_target": int(reopened_target_w),
                        "inverter_derating_reason": (
                            f"Feed-in blocked and averaged export stayed low at "
                            f"{smoothed_export_power_w:.0f}W < "
                            f"{lower_export_w:.0f}W for {unused_release_minutes} minutes - "
                            f"raise the inverter cap cautiously from {previous_target_w:.0f}W "
                            f"to {reopened_target_w:.0f}W instead of jumping back to max power"
                        ),
                        "inverter_derating_alarm": False,
                        "inverter_derating_alarm_reason": "No derating alarm",
                        "inverter_derating_unreached_since": (
                            evaluated_at
                            if reopened_target_w < float(max_inverter_power)
                            else None
                        ),
                    }
                if previous_derating_active and previous_target_w is not None:
                    return {
                        "inverter_derating_target": int(previous_target_w),
                        "inverter_derating_reason": (
                            f"Feed-in blocked but averaged export is only "
                            f"{smoothed_export_power_w:.0f}W < "
                            f"{lower_export_w:.0f}W - hold the current derating target "
                            f"{previous_target_w:.0f}W steady until low export has remained "
                            f"stable for {unused_release_minutes} minutes before increasing it"
                        ),
                        "inverter_derating_alarm": False,
                        "inverter_derating_alarm_reason": "No derating alarm",
                        "inverter_derating_unreached_since": unreached_since,
                    }
                reopened_target_w = previous_target_w
                if reopened_target_w is None:
                    reopened_target_w = solar_production_w
                reopened_target_w = min(
                    float(max_inverter_power),
                    max(0.0, reopened_target_w + release_step_w),
                )
                return {
                    "inverter_derating_target": int(reopened_target_w),
                    "inverter_derating_reason": (
                        f"Feed-in blocked but averaged export is only "
                        f"{smoothed_export_power_w:.0f}W < "
                        f"{lower_export_w:.0f}W - reopen inverter gradually toward the "
                        "export target instead of jumping straight to max power"
                    ),
                    **no_alarm,
                }

            if export_above_band:
                target = solar_production_w - (
                    max(export_power_w, smoothed_export_power_w) - export_limit_w
                )
                target = max(0.0, min(float(max_inverter_power), target))
                reason = (
                    f"Feed-in blocked and export is {max(export_power_w, smoothed_export_power_w):.0f}W > "
                    f"{upper_export_w:.0f}W - "
                    f"reduce from current solar {solar_production_w:.0f}W toward {export_limit_w}W export"
                )
                response = {
                    "inverter_derating_target": int(target),
                    "inverter_derating_reason": reason,
                    **no_alarm,
                }
                if battery_below_bypass_threshold:
                    response["inverter_derating_alarm"] = True
                    response["inverter_derating_alarm_reason"] = (
                        f"Battery SOC {average_soc:.0f}% is below the "
                        f"{soc_bypass_threshold:.0f}% bypass threshold, but export is still "
                        f"{max(export_power_w, smoothed_export_power_w):.0f}W. Derating was forced to protect the "
                        "grid target."
                    )
                return response

            # Within-band path: export is between lower and upper deadband.
            # We still allow relaxation here (not only in the below-band path)
            # so the timer isn't wasted when export oscillates around the edge.
            # unreached_since is preserved in the return so the countdown
            # carries over to the next evaluation cycle.
            held_target_w = previous_target_w
            if held_target_w is None:
                held_target_w = solar_production_w
            if should_relax_cap_upward() and previous_target_w is not None:
                reopened_target_w = min(
                    float(max_inverter_power),
                    max(0.0, previous_target_w + release_step_w),
                )
                return {
                    "inverter_derating_target": int(reopened_target_w),
                    "inverter_derating_reason": (
                        f"Feed-in blocked and averaged export has stayed at or below the "
                        f"{lower_export_w:.0f}-{upper_export_w:.0f}W control band for "
                        f"{unused_release_minutes} minutes - raise the inverter cap cautiously "
                        f"from {previous_target_w:.0f}W to {reopened_target_w:.0f}W"
                    ),
                    "inverter_derating_alarm": False,
                    "inverter_derating_alarm_reason": "No derating alarm",
                    "inverter_derating_unreached_since": (
                        evaluated_at
                        if reopened_target_w < float(max_inverter_power)
                        else None
                    ),
                }
            return {
                "inverter_derating_target": int(held_target_w),
                "inverter_derating_reason": (
                    f"Feed-in blocked and averaged export {smoothed_export_power_w:.0f}W is "
                    f"within the "
                    f"{lower_export_w:.0f}-{upper_export_w:.0f}W band - hold the current "
                    "derating target steady"
                ),
                "inverter_derating_alarm": False,
                "inverter_derating_alarm_reason": "No derating alarm",
                "inverter_derating_unreached_since": unreached_since,
            }

        # Fallback when grid or solar telemetry is missing: use a conservative
        # absolute cap based on house load plus allowed export.
        if battery_below_bypass_threshold:
            return {
                "inverter_derating_target": int(max_inverter_power),
                "inverter_derating_reason": (
                    f"Battery SOC {average_soc:.0f}% < {soc_bypass_threshold:.0f}% and "
                    "grid/solar telemetry is incomplete - keep inverter unrestricted"
                ),
                **no_alarm,
            }

        if house_consumption_w is None:
            return {
                "inverter_derating_target": None,
                "inverter_derating_reason": (
                    "House consumption unavailable - cannot calculate inverter target fallback"
                ),
                **no_alarm,
            }

        if solar_production_w is not None and solar_production_w <= safe_output_w:
            target = min(float(max_inverter_power), safe_output_w)
            return {
                "inverter_derating_target": int(target),
                "inverter_derating_reason": (
                    f"Available solar {solar_production_w:.0f}W is already below "
                    f"house {house_consumption_w:.0f}W + export target {export_limit_w}W - "
                    "keep publishing the stable fallback cap"
                ),
                **no_alarm,
            }

        target = min(float(max_inverter_power), safe_output_w)
        return {
            "inverter_derating_target": int(target),
            "inverter_derating_reason": (
                f"Feed-in blocked with incomplete telemetry - fallback to house "
                f"{house_consumption_w:.0f}W + export target {export_limit_w}W"
            ),
            **no_alarm,
        }
