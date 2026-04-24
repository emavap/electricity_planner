"""Nord Pool service integration and cache management.

Extracted from ``coordinator.py`` as a standalone collaborator. Responsible
for calling the Nord Pool ``get_prices_for_date`` service with retry logic
and maintaining a TTL/LRU-bounded cache keyed on ``config_entry_id`` and
target date.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import NORDPOOL_CACHE_MAX_SIZE, NORDPOOL_CACHE_TTL_MINUTES

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class NordpoolService:
    """Fetches and caches Nord Pool price responses for today/tomorrow."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    def clean_expired_cache(self) -> None:
        """Remove expired entries from Nord Pool cache based on TTL and size limit."""
        coordinator = self._coordinator
        now = dt_util.utcnow()
        ttl = timedelta(minutes=NORDPOOL_CACHE_TTL_MINUTES)

        # First, remove expired entries
        expired_keys = [
            key for key, (_, timestamp) in coordinator._nordpool_cache.items()
            if now - timestamp > ttl
        ]
        for key in expired_keys:
            del coordinator._nordpool_cache[key]
            _LOGGER.debug("Evicted expired Nord Pool cache entry: %s", key)

        # Then, enforce size limit using LRU (oldest timestamp first)
        if len(coordinator._nordpool_cache) > NORDPOOL_CACHE_MAX_SIZE:
            sorted_items = sorted(
                coordinator._nordpool_cache.items(),
                key=lambda x: x[1][1]  # Sort by timestamp
            )
            entries_to_remove = (
                len(coordinator._nordpool_cache) - NORDPOOL_CACHE_MAX_SIZE
            )
            for key, _ in sorted_items[:entries_to_remove]:
                del coordinator._nordpool_cache[key]
                _LOGGER.debug("Evicted old Nord Pool cache entry (size limit): %s", key)

    async def fetch_prices(
        self, config_entry_id: str, day: str
    ) -> dict[str, Any] | None:
        """Fetch Nord Pool prices for a specific date using the service call."""
        coordinator = self._coordinator
        try:
            # Calculate the target date
            now_local = dt_util.now()
            if day == "today":
                target_date = now_local.date()
            elif day == "tomorrow":
                target_date = (now_local + timedelta(days=1)).date()
            else:
                _LOGGER.error("Invalid day parameter: %s (expected 'today' or 'tomorrow')", day)
                return None

            # Check cache after resolving the concrete date to avoid midnight rollover bleed.
            now = dt_util.utcnow()
            cache_key = f"{config_entry_id}_{target_date.isoformat()}"
            if cache_key in coordinator._nordpool_cache:
                cached_data, cached_time = coordinator._nordpool_cache[cache_key]
                cache_age = now - cached_time
                if cache_age < timedelta(minutes=NORDPOOL_CACHE_TTL_MINUTES):
                    _LOGGER.debug(
                        "Using cached Nord Pool prices for %s (%s) (age: %.1f minutes)",
                        day,
                        target_date.isoformat(),
                        cache_age.total_seconds() / 60,
                    )
                    return cached_data

            # Call the Nord Pool service with exponential backoff retry
            max_retries = 3
            response = None
            for attempt in range(max_retries):
                try:
                    response = await coordinator.hass.services.async_call(
                        "nordpool",
                        "get_prices_for_date",
                        {
                            "config_entry": config_entry_id,
                            "date": target_date.isoformat()
                        },
                        blocking=True,
                        return_response=True
                    )
                    # Success - break out of retry loop
                    break
                except ValueError as err:
                    # Invalid parameters - don't retry
                    _LOGGER.warning("Nord Pool service call failed (invalid parameters) for %s: %s", day, err)
                    return None
                except TimeoutError as err:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        _LOGGER.warning(
                            "Nord Pool service timed out for %s, retrying in %ds (attempt %d/%d): %s",
                            day, wait_time, attempt + 1, max_retries, err
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        _LOGGER.error("Nord Pool service timed out for %s after %d attempts: %s", day, max_retries, err)
                        return None
                except Exception as err:
                    if isinstance(err, (KeyboardInterrupt, SystemExit)):
                        raise
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        _LOGGER.warning(
                            "Nord Pool service call failed for %s, retrying in %ds (attempt %d/%d): %s",
                            day, wait_time, attempt + 1, max_retries, err
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        _LOGGER.error("Nord Pool service call failed for %s after %d attempts: %s", day, max_retries, err)
                        return None

            # The service returns a dict with area-based price data
            if response and isinstance(response, dict):
                # Count total entries across all areas for logging
                total_entries = sum(len(v) for v in response.values() if isinstance(v, list))
                _LOGGER.debug("Fetched Nord Pool prices for %s (%s): %d entries across %d area(s)",
                            day, target_date.isoformat(), total_entries, len(response))

                # Evict oldest cache entry if cache is full
                if len(coordinator._nordpool_cache) >= coordinator._nordpool_cache_max_size:
                    # Defensive check: ensure cache is not empty before finding min
                    if coordinator._nordpool_cache:
                        oldest_key = min(coordinator._nordpool_cache.keys(),
                                       key=lambda k: coordinator._nordpool_cache[k][1])
                        del coordinator._nordpool_cache[oldest_key]
                        _LOGGER.debug("Evicted oldest Nord Pool cache entry: %s", oldest_key)

                # Cache the response with timestamp
                coordinator._nordpool_cache[cache_key] = (response, dt_util.utcnow())

                return response
            else:
                _LOGGER.warning("Nord Pool service returned unexpected format for %s", day)
                return None

        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.error("Unexpected error fetching Nord Pool prices for %s: %s", day, err, exc_info=True)
            return None
