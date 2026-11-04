"""Shared polling, outage handling and strict account boundary."""

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AccountSnapshot,
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    RateLimited,
    SScapeClient,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_INTERVAL,
    DEFAULT_INTERVAL,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class SScapeCoordinator(DataUpdateCoordinator[AccountSnapshot]):
    """All entities share one account poll, not one poll per place or metric."""

    def __init__(self, hass, entry: ConfigEntry, client: SScapeClient) -> None:
        self.client = client
        interval = entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
        self._base_interval = min(MAX_INTERVAL, max(MIN_INTERVAL, interval))
        self._failures = 0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._base_interval),
        )

    async def _async_update_data(self) -> AccountSnapshot:
        try:
            data = await self.client.async_get_snapshot()
            if data.account_id and data.account_id != self.config_entry.data[CONF_ACCOUNT_ID]:
                raise AuthenticationError("API key belongs to another account")
        except (AuthenticationError, PermissionDenied):
            raise ConfigEntryAuthFailed(
                "Check the API key, places:read scope and API access plan"
            ) from None
        except RateLimited as err:
            raise UpdateFailed("API rate limit reached", retry_after=err.retry_after) from None
        except CannotConnect:
            self._failures += 1
            self.update_interval = timedelta(
                seconds=min(MAX_INTERVAL, self._base_interval * 2 ** min(self._failures, 4))
            )
            raise UpdateFailed("sScape is temporarily unavailable") from None
        self._failures = 0
        self.update_interval = timedelta(seconds=self._base_interval)
        return data
