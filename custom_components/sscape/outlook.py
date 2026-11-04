"""Optional numeric forecast sensors for user-defined automations, not observations."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import UnitOfLength, UnitOfSpeed, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .entity import async_watch_places, place_device_info
from .forecast import number
from .weather import get_weather_coordinator, start_weather_refresh

DESCRIPTIONS = (
    SensorEntityDescription(
        key="minimum_temperature_c",
        translation_key="forecast_minimum",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="precipitation_mm",
        translation_key="forecast_rain",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="peak_gust_kmh",
        translation_key="forecast_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        entity_registry_enabled_default=False,
    ),
)


def setup_outlook_sensors(hass, entry, async_add_entities):
    if not entry.options.get("weather_enabled", True):
        return

    def create(parent, place):
        coordinator = get_weather_coordinator(hass, parent, place.place_id)
        return [SScapeOutlook(parent, coordinator, place, d) for d in DESCRIPTIONS]

    async_watch_places(hass, entry, async_add_entities, create)


class SScapeOutlook(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, parent, coordinator, place, description):
        super().__init__(coordinator)
        self.parent, self.place_id = parent, place.place_id
        self.entity_description = description
        self._attr_unique_id = (
            f"{parent.config_entry.unique_id}|{place.place_id}|forecast_{description.key}"
        )
        self._attr_device_info = place_device_info(parent, place)

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.parent.async_add_listener(self._handle_coordinator_update))
        start_weather_refresh(self.hass, self.coordinator, self.async_on_remove)

    @property
    def available(self):
        return (
            super().available
            and self.parent.last_update_success
            and self.place_id in self.parent.data.places
            and self.coordinator.data
            and self.coordinator.data.is_fresh()
            and self.native_value is not None
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        key = self.entity_description.key
        return number(
            self.coordinator.data.raw.get(key), nonnegative=key != "minimum_temperature_c"
        )

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        raw = self.coordinator.data.raw
        return {
            "data_kind": "model_forecast",
            "period_start": raw.get("start"),
            "period_end": raw.get("end"),
            "model": raw.get("model"),
            "sources": raw.get("sources"),
            "source_details": raw.get("source_details"),
            "fetched_at": raw.get("fetched_at"),
        }
