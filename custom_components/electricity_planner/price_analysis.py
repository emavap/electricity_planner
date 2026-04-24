"""Comprehensive pricing analysis.

Extracted from ``decision_engine.py`` as a standalone collaborator. The
calculator consumes raw sensor data (current / highest / lowest / next prices,
transport cost, optional overrides) and returns the normalised price-analysis
dictionary consumed by the rest of the decision pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .defaults import DEFAULT_ALGORITHM_THRESHOLDS
from .helpers import PriceCalculator, apply_price_adjustment

if TYPE_CHECKING:
    from .decision_engine import EngineSettings

_LOGGER = logging.getLogger(__name__)


class PriceAnalysisCalculator:
    """Produce the comprehensive price-analysis dict from raw inputs."""

    def __init__(
        self,
        settings: "EngineSettings",
        price_calculator: PriceCalculator,
    ) -> None:
        self._settings = settings
        self._price_calculator = price_calculator

    def analyze(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze comprehensive pricing data from Nord Pool."""
        overrides = data.get("price_analysis_overrides") or {}
        raw_current_price = overrides.get("raw_current_price", data.get("current_price"))
        raw_highest_price = overrides.get("raw_highest_price", data.get("highest_price"))
        raw_lowest_price = overrides.get("raw_lowest_price", data.get("lowest_price"))
        raw_next_price = overrides.get("raw_next_price", data.get("next_price"))

        price_multiplier = self._settings.price_adjustment_multiplier
        price_offset = self._settings.price_adjustment_offset

        if overrides:
            current_price = overrides.get("current_price")
            highest_price = overrides.get("highest_price")
            lowest_price = overrides.get("lowest_price")
            next_price = overrides.get("next_price")
            transport_cost = overrides.get("transport_cost", data.get("transport_cost") or 0)
        else:
            current_price = apply_price_adjustment(raw_current_price, price_multiplier, price_offset)
            highest_price = apply_price_adjustment(raw_highest_price, price_multiplier, price_offset)
            lowest_price = apply_price_adjustment(raw_lowest_price, price_multiplier, price_offset)
            next_price = apply_price_adjustment(raw_next_price, price_multiplier, price_offset)
            transport_cost = data.get("transport_cost") or 0

        if current_price is None:
            _LOGGER.error(
                "Current price unavailable after adjustment (raw=%s, multiplier=%s, offset=%s) - "
                "disabling charging decisions for safety",
                raw_current_price, price_multiplier, price_offset
            )
            return self.create_unavailable_analysis(
                raw_highest_price, raw_lowest_price, raw_next_price,
                self._settings.price_threshold,
                transport_cost,
            )

        if not overrides:
            # Add transport cost to all prices when interval-aware overrides are unavailable.
            adjusted_prices = self.add_transport_cost_to_prices(
                {
                    "current_price": current_price,
                    "highest_price": highest_price,
                    "lowest_price": lowest_price,
                    "next_price": next_price,
                },
                transport_cost
            )
            current_price = adjusted_prices["current_price"]
            highest_price = adjusted_prices["highest_price"]
            lowest_price = adjusted_prices["lowest_price"]
            next_price = adjusted_prices["next_price"]

        # Determine which threshold to use
        use_average_threshold = self._settings.use_average_threshold
        average_threshold = data.get("average_threshold")

        if use_average_threshold and average_threshold is not None:
            price_threshold = average_threshold
        else:
            price_threshold = self._settings.price_threshold

        very_low_threshold = self._settings.very_low_price_threshold_ratio

        # Use cached price position calculation with explicit None handling
        # Note: Use explicit None checks to handle zero and negative prices correctly
        # If both highest and lowest are None, we don't have daily range data
        if highest_price is None and lowest_price is None:
            price_position = None
            _LOGGER.debug("Daily price range unavailable - price_position set to None")
        else:
            effective_highest = current_price if highest_price is None else highest_price
            effective_lowest = current_price if lowest_price is None else lowest_price
            price_position = self._price_calculator.calculate_price_position(
                current_price, effective_highest, effective_lowest
            )

        # Check price trends
        next_price_higher = next_price is not None and next_price > current_price
        price_trend_improving = next_price is not None and next_price < current_price
        significant_price_drop = self._price_calculator.is_significant_price_drop(
            current_price, next_price, DEFAULT_ALGORITHM_THRESHOLDS.significant_price_drop
        )

        return {
            "current_price": current_price,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "raw_current_price": raw_current_price,
            "raw_highest_price": raw_highest_price,
            "raw_lowest_price": raw_lowest_price,
            "raw_next_price": raw_next_price,
            "price_adjustment_multiplier": price_multiplier,
            "price_adjustment_offset": price_offset,
            "transport_cost": transport_cost,
            "price_threshold": price_threshold,
            "is_low_price": current_price <= price_threshold,
            "is_lowest_price": lowest_price is not None and abs(current_price - lowest_price) < 1e-6,
            "price_position": price_position,
            "next_price_higher": next_price_higher,
            "price_trend_improving": price_trend_improving,
            "significant_price_drop": significant_price_drop,
            "very_low_price": price_position is not None and price_position <= very_low_threshold,
            "data_available": True,
        }

    @staticmethod
    def add_transport_cost_to_prices(
        prices: dict[str, float | None],
        transport_cost: float,
    ) -> dict[str, float | None]:
        """Add transport cost to all non-None prices."""
        return {
            key: (price + transport_cost if price is not None else None)
            for key, price in prices.items()
        }

    def create_unavailable_analysis(
        self,
        highest_price: float | None,
        lowest_price: float | None,
        next_price: float | None,
        price_threshold: float,
        transport_cost: float = 0.0,
    ) -> dict[str, Any]:
        """Create price analysis when current price is unavailable."""
        return {
            "current_price": None,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "price_threshold": price_threshold,
            "is_low_price": False,
            "is_lowest_price": False,
            "price_position": None,
            "next_price_higher": False,
            "price_trend_improving": False,
            "significant_price_drop": False,
            "very_low_price": False,
            "data_available": False,
            "raw_current_price": None,
            "raw_highest_price": highest_price,
            "raw_lowest_price": lowest_price,
            "raw_next_price": next_price,
            "price_adjustment_multiplier": self._settings.price_adjustment_multiplier,
            "price_adjustment_offset": self._settings.price_adjustment_offset,
            "transport_cost": transport_cost,
        }
