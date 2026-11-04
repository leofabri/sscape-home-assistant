"""Native HA hourly forecasts through sScape's existing rights-aware broker."""

import logging
from datetime import timedelta

from homeassistant.components.weather import SingleCoordinatorWeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfPrecipitationDepth, UnitOfSpeed, UnitOfTemperature
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import AuthenticationError, CannotConnect, PermissionDenied, RateLimited
from .const import CONF_WEATHER
from .entity import async_watch_places, place_device_info
from .forecast import WeatherSnapshot

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 0


class SScapeWeatherCoordinator(DataUpdateCoordinator[WeatherSnapshot]):
    def __init__(self, hass, parent, place_id):
        self.parent = parent
        self.place_id = place_id
        super().__init__(
            hass,
            _LOGGER,
            name="sscape forecast",
            config_entry=parent.config_entry,
            update_interval=timedelta(minutes=10),
        )

    async def _async_update_data(self):
        try:
            async with self.parent.weather_slots:
                data = WeatherSnapshot.parse(
                    await self.parent.client.async_get_weather(self.place_id)
                )
        except (AuthenticationError, PermissionDenied):
            raise ConfigEntryAuthFailed("Check the API key and place permissions") from None
        except RateLimited as err:
            raise UpdateFailed("API rate limit reached", retry_after=err.retry_after) from None
        except CannotConnect:
            raise UpdateFailed("sScape forecast is temporarily unavailable") from None
        if not data.is_fresh():
            raise UpdateFailed("No fresh licensed model forecast is available")
        return data


async def async_setup_entry(hass, entry, async_add_entities):
    if not entry.options.get(CONF_WEATHER, True):
        return

    def create(parent, place):
        coordinator = SScapeWeatherCoordinator(hass, parent, place.place_id)
        parent.weather_coordinators[place.place_id] = coordinator
        return [SScapeWeather(parent, coordinator, place)]

    async_watch_places(hass, entry, async_add_entities, create)


class SScapeWeather(SingleCoordinatorWeatherEntity[SScapeWeatherCoordinator]):
    _attr_has_entity_name = True
    _attr_translation_key = "forecast"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_native_precipitation_unit = UnitOfPrecipitationDepth.MILLIMETERS
    _attr_supported_features = WeatherEntityFeature.FORECAST_HOURLY

    def __init__(self, parent, coordinator, place):
        super().__init__(coordinator)
        self.parent = parent
        self.place_id = place.place_id
        self._attr_unique_id = f"{parent.config_entry.unique_id}|{place.place_id}|forecast"
        self._attr_device_info = place_device_info(parent, place)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.parent.async_add_listener(self._handle_coordinator_update))
        task = self.hass.async_create_task(self.coordinator.async_refresh())
        self.async_on_remove(task.cancel)

    @property
    def available(self):
        return (
            super().available
            and self.parent.last_update_success
            and self.place_id in self.parent.data.places
            and bool(self.coordinator.data and self.coordinator.data.is_fresh())
        )

    @property
    def condition(self):
        return self.coordinator.data.condition if self.coordinator.data else None

    @property
    def native_temperature(self):
        return (
            self.coordinator.data.current_number("temperature_c") if self.coordinator.data else None
        )

    @property
    def native_wind_speed(self):
        return self.coordinator.data.current_number("wind_kmh") if self.coordinator.data else None

    @property
    def attribution(self):
        details = (
            self.coordinator.data.raw.get("source_details", []) if self.coordinator.data else []
        )
        credits = [
            item["attribution"]
            for item in details
            if isinstance(item, dict) and isinstance(item.get("attribution"), str)
        ]
        return "Model forecast via sScape; not a surface observation. " + " · ".join(credits)

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        raw = self.coordinator.data.raw
        return {
            "data_kind": "model_forecast",
            "model": raw.get("model"),
            "sources": raw.get("sources"),
            "source_details": [
                {
                    key: item.get(key)
                    for key in ("id", "name", "attribution", "license_url", "modification_notice")
                }
                for item in raw.get("source_details", [])
                if isinstance(item, dict)
            ],
            "fetched_at": raw.get("fetched_at"),
            "estimate_time": self.coordinator.data.current.get("time"),
            "precipitation_period": "preceding_hour",
            "gusts": "only 1-hour maxima are included",
        }

    async def async_forecast_hourly(self):
        return self.coordinator.data.hourly() if self.available else None
