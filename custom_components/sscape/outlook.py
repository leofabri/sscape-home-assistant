"""Optional numeric forecast sensors for user-defined automations, not observations."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import UnitOfLength, UnitOfSpeed, UnitOfTemperature
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import _time
from .entity import async_watch_places, place_device_info
from .forecast import number
from .planning import make_outlook
from .weather import get_weather_coordinator, start_weather_refresh

DESCRIPTIONS = (
    SensorEntityDescription(
        key="snowfall_cm",
        translation_key="forecast_snowfall",
        icon="mdi:weather-snowy",
        device_class=SensorDeviceClass.PRECIPITATION,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="visibility_minimum_m",
        translation_key="forecast_visibility",
        icon="mdi:weather-fog",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.METERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="precipitation_outlook",
        translation_key="precipitation_outlook",
        icon="mdi:weather-rainy",
        device_class=SensorDeviceClass.ENUM,
        options=["expected", "not_expected", "data_incomplete"],
    ),
    *(
        SensorEntityDescription(
            key=key,
            translation_key=key,
            device_class=SensorDeviceClass.TIMESTAMP,
            entity_registry_enabled_default=False,
        )
        for key in ("next_rain_window", "gust_peak_time", "temperature_minimum_time")
    ),
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
PLANNING_KEYS = {
    "snowfall_cm",
    "visibility_minimum_m",
    "precipitation_outlook",
    "next_rain_window",
    "gust_peak_time",
    "temperature_minimum_time",
}


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
        start_weather_refresh(self.hass, self.coordinator)

    @property
    def available(self):
        return (
            super().available
            and self.parent.last_update_success
            and self.place_id in self.parent.data.places
            and self.coordinator.data
            and self.coordinator.data.is_fresh()
            and (
                self.entity_description.key
                in PLANNING_KEYS - {"snowfall_cm", "visibility_minimum_m"}
                or self.native_value is not None
            )
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        key = self.entity_description.key
        if key in PLANNING_KEYS:
            plan = self._plan()
            if not plan["available"]:
                return None
            if key == "precipitation_outlook":
                status = plan["precipitation"]["status"]
                return "data_incomplete" if status == "unknown" else status
            if key == "next_rain_window":
                window = plan["precipitation"]["first_window"]
                return _time(window["start"]) if window else None
            if key == "gust_peak_time":
                return _time(plan["wind_gust"]["period_end"])
            if key == "snowfall_cm":
                return plan["snowfall"]["total_cm"]
            if key == "visibility_minimum_m":
                return plan["visibility"]["minimum_m"]
            return _time(plan["temperature"]["time"])
        return number(
            self.coordinator.data.raw.get(key), nonnegative=key != "minimum_temperature_c"
        )

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        raw = self.coordinator.data.raw
        if self.entity_description.key in PLANNING_KEYS:
            plan = self._plan()
            category = (
                "snowfall"
                if self.entity_description.key == "snowfall_cm"
                else "visibility"
                if self.entity_description.key == "visibility_minimum_m"
                else "wind_gust"
                if self.entity_description.key == "gust_peak_time"
                else "temperature"
                if self.entity_description.key == "temperature_minimum_time"
                else "precipitation"
            )
            return {
                key: value
                for key, value in plan.items()
                if key
                not in ("precipitation", "wind_gust", "temperature", "snowfall", "visibility")
            } | {"outlook": plan.get(category)}
        return {
            "data_kind": "model_forecast",
            "period_start": raw.get("start"),
            "period_end": raw.get("end"),
            "model": raw.get("model"),
            "sources": raw.get("sources"),
            "source_details": raw.get("source_details"),
            "fetched_at": raw.get("fetched_at"),
        }

    def _plan(self):
        # Timestamp companions share the 24h horizon of the numeric sensors.
        hours = 6 if self.entity_description.key == "precipitation_outlook" else 24
        return make_outlook(self.coordinator.data, hours=hours)
