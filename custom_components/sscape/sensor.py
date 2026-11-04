"""Useful assessment sensors without inventing meteorological measurements."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory, UnitOfTime

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
)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    async_watch_places(
        hass,
        entry,
        async_add_entities,
        lambda coordinator, place: [
            SScapeSensor(coordinator, place, description) for description in DESCRIPTIONS
        ],
    )


class SScapeSensor(SScapeEntity, SensorEntity):
    """A field from the server's assessment, not a second risk algorithm."""

    def __init__(self, coordinator, place, description) -> None:
        super().__init__(coordinator, place, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        if not (place := self.place):
            return None
        return {
            "risk": place.risk_level,
            "situation": place.headline,
            "eta": place.eta_minutes,
            "updated": place.updated_at,
        }[self.entity_description.key]

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        # Do not leave an actionable recommendation attached to a stale risk state.
        if self.entity_description.key == "risk" and self.available and self.place:
            return self.place.attributes
        return None
