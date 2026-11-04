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
        lambda coordinator, place: [SScapeAnalysisStale(coordinator, place, "analysis_stale")],
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
