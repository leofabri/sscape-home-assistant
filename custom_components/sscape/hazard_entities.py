"""Optional detailed warning and catalogue entities, not new hazard forecasts."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import UnitOfLength

from .api import _time
from .entity import SScapeEntity, async_watch_places
from .hazards import hazards_for
from .status import value_status

DESCRIPTIONS = (
    SensorEntityDescription(
        key="official_warning_details",
        translation_key="official_warning_details",
        icon="mdi:alert-outline",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="official_warning_expires",
        translation_key="official_warning_expires",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="latest_earthquake_magnitude",
        translation_key="latest_earthquake_magnitude",
        icon="mdi:pulse",
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="latest_earthquake_distance",
        translation_key="latest_earthquake_distance",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="latest_earthquake_time",
        translation_key="latest_earthquake_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="nearest_thermal_detection",
        translation_key="nearest_thermal_detection",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        entity_registry_enabled_default=False,
    ),
)


def setup_hazard_entities(hass, entry, async_add_entities):
    async_watch_places(
        hass,
        entry,
        async_add_entities,
        lambda parent, place: [HazardSensor(parent, place, d) for d in DESCRIPTIONS],
    )


class HazardSensor(SScapeEntity, SensorEntity):
    def __init__(self, parent, place, description):
        super().__init__(parent, place, description.key)
        self.entity_description = description

    @property
    def evidence(self):
        data = hazards_for(self.place) if self.place else {"available": False}
        if not data["available"]:
            return None
        key = self.entity_description.key
        if key.startswith("official_warning"):
            return next(iter(data["official_warnings"]), None)
        if key.startswith("latest_earthquake"):
            return next(iter(data["earthquakes"]), None)
        return min(data["thermal_detections"], key=lambda row: row["distance_km"], default=None)

    @property
    def native_value(self):
        if not (data := self.evidence):
            return None
        key = self.entity_description.key
        if key == "official_warning_details":
            return (data.get("headline") or data.get("type") or data.get("severity") or "Warning")[
                :255
            ]
        if key == "official_warning_expires":
            return _time(data["expires_at"])
        if key == "latest_earthquake_magnitude":
            return data["magnitude"]
        if key == "latest_earthquake_time":
            return _time(data["occurred_at"])
        return data["distance_km"]

    @property
    def available(self):
        return super().available

    @property
    def extra_state_attributes(self):
        status = value_status(
            self.place,
            self.entity_description.key,
            self.native_value,
            connected=self.coordinator.last_update_success,
            language=self.hass.config.language if self.hass else "en",
        )
        return status | (self.evidence or {}) if self.available else status
