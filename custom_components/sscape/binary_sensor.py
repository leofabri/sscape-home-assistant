"""Explicitly distinguish stale analysis from a calm assessment."""

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from .entity import SScapeEntity, async_watch_places

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
        return super().available and self.is_on is not None

    @property
    def is_on(self):
        return self.place.signal(self.key) if self.place else None

    @property
    def extra_state_attributes(self):
        if not self.available:
            return None
        attrs = {"scope": "available observations and catalogue records; not a safety guarantee"}
        briefing = self.place.briefing
        if self.key == "storm_approach":
            attrs["eta_minutes"] = self.place.eta_minutes
        if self.key == "lightning_detected":
            attrs["count_20m"] = self.place.metric("lightning_count_20m")
            attrs["nearest_distance_km"] = self.place.metric("nearest_lightning_distance_km")
        if self.key == "earthquake_activity":
            attrs["search_radius_km"] = self.place.metric("earthquake_search_radius_km")
            attrs["max_magnitude_24h"] = self.place.metric("max_earthquake_magnitude_24h")
        if self.key == "wildfire_detected":
            attrs["detections"] = [
                {key: item.get(key) for key in ("source", "observed_at", "distance_km", "kind")}
                for item in briefing.get("environmental_signals", [])[:10]
                if isinstance(item, dict) and item.get("kind") == "fire"
            ]
        return attrs
