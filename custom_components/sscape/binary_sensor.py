"""Explicitly distinguish stale analysis from a calm assessment."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import SScapeEntity, async_watch_places
from .hazards import hazards_for
from .status import value_status

PARALLEL_UPDATES = 0


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_watch_places(
        hass,
        entry,
        async_add_entities,
        lambda coordinator, place: (
            [SScapeAnalysisStale(coordinator, place, "analysis_stale")]
            + [
                SScapeSignal(coordinator, place, key)
                for key in (
                    "storm_approach",
                    "lightning_detected",
                    "official_warning",
                    "wildfire_detected",
                    "earthquake_activity",
                    "hail_reported",
                )
            ]
        ),
    )


class SScapeAnalysisStale(SScapeEntity, BinarySensorEntity):
    """True means the last successful poll did not contain a fresh assessment."""

    _attr_translation_key = "analysis_stale"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.place is not None

    @property
    def is_on(self) -> bool | None:
        return not self.place.is_fresh() if self.place else None


class SScapeSignal(SScapeEntity, BinarySensorEntity):
    """A reported feature, not a new hazard classifier or an official all-clear."""

    _attr_icon = "mdi:map-marker-alert-outline"

    def __init__(self, coordinator, place, key):
        super().__init__(coordinator, place, key)
        self.key = key
        self._attr_translation_key = key
        self._attr_entity_registry_enabled_default = key in ("storm_approach", "lightning_detected")

    @property
    def available(self):
        return super().available

    @property
    def is_on(self):
        return self.place.signal(self.key) if self.place else None

    @property
    def extra_state_attributes(self):
        status = value_status(
            self.place,
            self.key,
            self.is_on,
            connected=self.coordinator.last_update_success,
            language=self.hass.config.language if self.hass else "en",
        )
        if not self.available:
            return status
        attrs = status | {
            "scope": "available observations and catalogue records; not a safety guarantee"
        }
        if self.key == "storm_approach":
            attrs["eta_minutes"] = self.place.eta_minutes
        if self.key == "lightning_detected":
            attrs["count_20m"] = self.place.metric("lightning_count_20m")
            attrs["nearest_distance_km"] = self.place.lightning_distance
        if self.key == "earthquake_activity":
            attrs["search_radius_km"] = self.place.metric("earthquake_search_radius_km")
            attrs["max_magnitude_24h"] = self.place.metric("max_earthquake_magnitude_24h")
            attrs["events"] = hazards_for(self.place).get("earthquakes", [])
        if self.key == "official_warning":
            attrs["warnings"] = hazards_for(self.place).get("official_warnings", [])
            attrs["scope"] = "monitored_radius_not_necessarily_the_exact_point"
        if self.key == "wildfire_detected":
            attrs["detections"] = [
                {key: item.get(key) for key in ("source", "observed_at", "distance_km", "kind")}
                for item in hazards_for(self.place).get("thermal_detections", [])[:10]
            ]
        return attrs
