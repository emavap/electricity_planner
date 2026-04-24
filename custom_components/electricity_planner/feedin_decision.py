"""Solar feed-in decision calculation.

Extracted from ``decision_engine.py`` as a standalone collaborator. The
calculator receives a fully-materialized :class:`CycleContext` and the
current :class:`EngineSettings`, returning the three feed-in decision
fields used by downstream sensors.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .helpers import apply_price_adjustment

if TYPE_CHECKING:
    from .decision_engine import CycleContext, EngineSettings


class FeedInDecisionCalculator:
    """Decide whether to enable solar feed-in for the current cycle."""

    def __init__(self, settings: "EngineSettings") -> None:
        self._settings = settings

    def decide(self, ctx: "CycleContext") -> dict[str, Any]:
        """Return the feed-in decision for the given cycle context."""
        if not ctx.has_price_data:
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No price data available",
                "feedin_effective_price": None,
            }

        current_price = ctx.current_price
        raw_price = ctx.raw_current_price

        if current_price is None:
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No adjusted price available for feed-in",
                "feedin_effective_price": None,
            }

        if raw_price is None:
            raw_price = current_price

        feed_multiplier = self._settings.feedin_adjustment_multiplier
        feed_offset = self._settings.feedin_adjustment_offset
        feedin_threshold = self._settings.feedin_threshold
        remaining_solar = int(ctx.remaining_solar)

        adjusted_feed_price = apply_price_adjustment(raw_price, feed_multiplier, feed_offset)
        if adjusted_feed_price is None:
            adjusted_feed_price = raw_price

        enable_feedin = adjusted_feed_price >= feedin_threshold
        comparator = "≥" if enable_feedin else "<"
        action = "enable" if enable_feedin else "disable"
        reason = (
            f"Net feed-in price {adjusted_feed_price:.3f}€/kWh {comparator} "
            f"{feedin_threshold:.3f}€/kWh - {action} solar export "
            f"(surplus: {remaining_solar}W)"
        )

        return {
            "feedin_solar": enable_feedin,
            "feedin_solar_reason": reason,
            "feedin_effective_price": adjusted_feed_price,
        }
