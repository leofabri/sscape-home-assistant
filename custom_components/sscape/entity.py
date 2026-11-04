"""Shared place device and dynamically discovered entities."""

from collections.abc import Callable

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import PlaceSnapshot
from .const import CONF_FOLLOW_ALL, CONF_PLACES, DOMAIN
from .coordinator import SScapeCoordinator


def place_device_info(coordinator, place):
    return DeviceInfo(
        identifiers={(DOMAIN, f"{coordinator.config_entry.unique_id}|{place.place_id}")},
        name=place.name,
        manufacturer="sScape",
        model="Monitored place",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url=f"{coordinator.client.host}/profile",
    )


class SScapeEntity(CoordinatorEntity[SScapeCoordinator]):
    """Stable per-account, per-place IDs survive API key rotation and renaming."""

    _attr_has_entity_name = True
    _attr_attribution = "Environmental analysis by sScape"

    def __init__(self, coordinator: SScapeCoordinator, place: PlaceSnapshot, key: str) -> None:
        super().__init__(coordinator)
        self.place_id = place.place_id
        identity = f"{coordinator.config_entry.unique_id}|{self.place_id}"
        self._attr_unique_id = f"{identity}|{key}"
        self._attr_device_info = place_device_info(coordinator, place)

    @property
    def place(self) -> PlaceSnapshot | None:
        return self.coordinator.data.places.get(self.place_id)

    @property
    def available(self) -> bool:
        return super().available and self.place is not None and self.place.is_fresh()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Follow server-side renames without overwriting HA's name_by_user."""
        if (place := self.place) and self.registry_entry and self.registry_entry.device_id:
            registry = dr.async_get(self.hass)
            device = registry.async_get(self.registry_entry.device_id)
            if device and device.name != place.name:
                registry.async_update_device(device.id, name=place.name)
        super()._handle_coordinator_update()


def async_watch_places(hass, entry, async_add_entities, factory: Callable) -> None:
    """Add new saved places without restart; missing places become unavailable."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new_places() -> None:
        if (
            coordinator.stopping
            or hass.config_entries.async_get_entry(entry.entry_id) is not entry
            or getattr(entry, "runtime_data", None) is not coordinator
        ):
            return
        selected = entry.options.get(CONF_PLACES, [])
        follow_all = entry.options.get(CONF_FOLLOW_ALL, False)
        new = []
        for place_id, place in coordinator.data.places.items():
            if place_id not in known and (follow_all or place_id in selected):
                known.add(place_id)
                new.extend(factory(coordinator, place))
        if new:
            async_add_entities(new)

    add_new_places()
    entry.async_on_unload(coordinator.async_add_listener(add_new_places))
