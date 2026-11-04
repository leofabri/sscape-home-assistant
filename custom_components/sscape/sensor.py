"""Useful assessment sensors without inventing meteorological measurements."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)

from .const import RISK_LEVELS
from .entity import SScapeEntity, async_watch_places

PARALLEL_UPDATES = 0
DESCRIPTIONS = (
    SensorEntityDescription(
        key="risk",
        translation_key="risk",
        icon="mdi:shield-alert-outline",
        device_class=SensorDeviceClass.ENUM,
        options=RISK_LEVELS,
    ),
    SensorEntityDescription(
        key="situation", translation_key="situation", icon="mdi:weather-cloudy-alert"
    ),
    SensorEntityDescription(
        key="eta",
        translation_key="eta",
        icon="mdi:timer-outline",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
    ),
    SensorEntityDescription(
        key="updated",
        translation_key="updated",
        icon="mdi:update",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="lightning_count",
        translation_key="lightning_count",
        icon="mdi:lightning-bolt",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="lightning_distance",
        translation_key="lightning_distance",
        icon="mdi:map-marker-distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="earthquake_count",
        translation_key="earthquake_count",
        icon="mdi:pulse",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="data_quality",
        translation_key="data_quality",
        device_class=SensorDeviceClass.ENUM,
        options=["unknown", "partial", "stale"],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SensorEntityDescription(
        key="phase",
        translation_key="phase",
        device_class=SensorDeviceClass.ENUM,
        options=["quiet", "watch", "approach", "impact"],
    ),
    SensorEntityDescription(
        key="eta_min",
        translation_key="eta_min",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="eta_max",
        translation_key="eta_max",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="storm_distance",
        translation_key="storm_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="radar_updated",
        translation_key="radar_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="official_count",
        translation_key="official_count",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="observed_temperature",
        translation_key="observed_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="observed_wind",
        translation_key="observed_wind",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="observed_gust",
        translation_key="observed_gust",
        device_class=SensorDeviceClass.WIND_SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="observed_humidity",
        translation_key="observed_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        entity_registry_enabled_default=False,
    ),
)
OBSERVATIONS = {
    "observed_temperature": "air_temperature",
    "observed_wind": "wind_speed",
    "observed_gust": "wind_gust",
    "observed_humidity": "relative_humidity",
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_watch_places(
        hass,
        entry,
        async_add_entities,
        lambda coordinator, place: [
            SScapeSensor(coordinator, place, description) for description in DESCRIPTIONS
        ],
    )
    from .outlook import setup_outlook_sensors

    setup_outlook_sensors(hass, entry, async_add_entities)
    from .local_sensors import setup_local_sensors

    setup_local_sensors(hass, entry, async_add_entities)
    from .hazard_entities import setup_hazard_entities

    setup_hazard_entities(hass, entry, async_add_entities)


class SScapeSensor(SScapeEntity, SensorEntity):
    """A field from the server's assessment, not a second risk algorithm."""

    def __init__(self, coordinator, place, description) -> None:
        super().__init__(coordinator, place, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if not (place := self.place):
            return None
        key = self.entity_description.key
        if key in OBSERVATIONS:
            reading = place.reading(OBSERVATIONS[key])
            if not reading:
                return None
            value = reading["observation"]["value"]
            return round(value * 3.6, 2) if key in ("observed_wind", "observed_gust") else value
        if key == "storm_distance":
            storm = place.briefing.get("nearest_storm")
            value = storm.get("distance_km") if isinstance(storm, dict) else None
            return value if type(value) in (int, float) and 0 <= value < 50000 else None
        if key == "radar_updated":
            from .api import _time

            quality = place.briefing.get("data_quality", {}).get("radar", {})
            return _time(quality.get("observation_time"))
        return {
            "risk": place.risk_level,
            "situation": place.headline,
            "eta": place.eta_minutes,
            "updated": place.updated_at,
            "lightning_count": place.metric("lightning_count_20m"),
            "lightning_distance": place.lightning_distance,
            "earthquake_count": place.metric("earthquakes_24h"),
            "official_count": place.metric("official_alert_count"),
            "data_quality": place.briefing.get("data_quality_status"),
            "phase": place.briefing.get("situation_phase"),
            "eta_min": place.arrival_bound("min_minutes"),
            "eta_max": place.arrival_bound("max_minutes"),
        }[self.entity_description.key]

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        # Do not leave an actionable recommendation attached to a stale risk state.
        if self.entity_description.key == "risk" and self.available and self.place:
            return self.place.attributes
        if self.available and self.place:
            if self.entity_description.key == "data_quality":
                return {
                    "feeds": self.place.briefing.get("data_quality"),
                    "scope": "observation freshness and known coverage, not a safety guarantee",
                }
            if variable := OBSERVATIONS.get(self.entity_description.key):
                row = self.place.reading(variable)
                observation = row["observation"]
                return {
                    "data_kind": "surface_observation",
                    "station": observation.get("station", {}).get("name"),
                    "distance_km": row["distance_km"],
                    "scope": row["scope"],
                    "observation_time": observation["observation_time"],
                    "source": observation["source_id"],
                    "attribution": observation["attribution"],
                    "license_url": observation["license_url"],
                    "quality": observation["quality"]["status"],
                    "source_unit": observation["unit"],
                }
        return None
