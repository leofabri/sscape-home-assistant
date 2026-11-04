"""Shared polling, outage handling and strict account boundary."""

import asyncio
import logging
import random
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AccountSnapshot,
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    PlaceCatalog,
    RateLimited,
    SScapeClient,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_FOLLOW_ALL,
    CONF_INTERVAL,
    CONF_PLACES,
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
        self.catalog: PlaceCatalog | None = None
        self.weather_coordinators = {}
        self.weather_slots = asyncio.Semaphore(2)
        self.stream_connected = False
        self.last_push_at = None
        self.stream_reconnects = 0
        self._stream_task = None
        self._stale_since = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._base_interval),
        )

    async def _async_update_data(self) -> AccountSnapshot:
        try:
            if self.catalog is None:
                self.catalog = await self.client.async_get_catalog()
                if self.catalog.account_id != self.config_entry.data[CONF_ACCOUNT_ID]:
                    raise AuthenticationError("API key belongs to another account")
            selected = (
                None
                if self.config_entry.options.get(CONF_FOLLOW_ALL, False)
                else self.config_entry.options.get(CONF_PLACES, [])
            )
            data = await self.client.async_get_snapshot(selected)
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
        self._update_repairs(data)
        if data.places and getattr(self.config_entry, "runtime_data", None) is self:
            self.start_stream()
        return data

    def _update_repairs(self, data):
        now = datetime.now(UTC)
        stale = any(
            place.briefing.get("data_quality_status") == "stale" for place in data.places.values()
        )
        issue = self.config_entry.entry_id + "_data_delayed"
        if not stale:
            self._stale_since = None
            ir.async_delete_issue(self.hass, DOMAIN, issue)
            return
        self._stale_since = self._stale_since or now
        if now - self._stale_since >= timedelta(minutes=5):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="data_delayed",
            )

    def start_stream(self):
        if self._stream_task is not None and not self._stream_task.done():
            return
        if (
            not self.config_entry.options.get("push_updates", True)
            or "places.stream.v1" not in self.catalog.capabilities
            or (
                not self.config_entry.options.get(CONF_FOLLOW_ALL, False)
                and not self.config_entry.options.get(CONF_PLACES, [])
            )
        ):
            return
        self._stream_task = self.hass.async_create_background_task(
            self._stream_loop(), "sscape place updates"
        )
        self.config_entry.async_on_unload(self._stream_task.cancel)

    async def _stream_loop(self):
        failures = 0
        selected = (
            None
            if self.config_entry.options.get(CONF_FOLLOW_ALL, False)
            else self.config_entry.options.get(CONF_PLACES, [])
        )
        while True:
            delay = min(60, 2 ** min(failures + 1, 6))
            try:
                async for event in self.client.async_updates(selected):
                    if event == "empty":
                        await self.async_request_refresh()
                        return
                    self.stream_connected = True
                    failures = 0
                    if event == "refresh":
                        self.last_push_at = datetime.now(UTC)
                    # Reconcile on reconnect as well: queued notifications are
                    # never replayed as fresh observations or hazard events.
                    await self.async_request_refresh()
            except (AuthenticationError, PermissionDenied):
                await self.async_request_refresh()  # coordinator drives reauth
                return
            except RateLimited as err:
                delay = err.retry_after
            except CannotConnect:
                failures += 1
            finally:
                self.stream_connected = False
            self.stream_reconnects += 1
            await asyncio.sleep(delay + random.uniform(0, 1))
