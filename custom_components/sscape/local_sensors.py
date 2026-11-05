"""Local-only weather instruments, separate from forecasts and remote stations."""

import logging
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.util.unit_conversion import (
    DistanceConverter,
    PressureConverter,
    SpeedConverter,
    TemperatureConverter,
)

from .api import _time
from .const import DOMAIN
from .entity import async_watch_places, place_device_info
from .targets import allows_target


@dataclass(frozen=True)
class LocalSpec:
    unit: str
    device_class: str
    converter: type | None
    units: tuple
    lower: float
    upper: float
    age_minutes: int


LOCAL_SPECS = {
    "temperature": LocalSpec(
        "°C", SensorDeviceClass.TEMPERATURE, TemperatureConverter, ("°C", "°F", "K"), -100, 80, 60
    ),
    "humidity": LocalSpec("%", SensorDeviceClass.HUMIDITY, None, ("%",), 0, 100, 60),
    "pressure": LocalSpec(
        "hPa",
        SensorDeviceClass.PRESSURE,
        PressureConverter,
        ("hPa", "Pa", "kPa", "mbar", "bar", "inHg", "mmHg"),
        100,
        1200,
        60,
    ),
    "wind_speed": LocalSpec(
        "km/h",
        SensorDeviceClass.WIND_SPEED,
        SpeedConverter,
        ("km/h", "m/s", "mph", "kn", "ft/s", "m/min"),
        0,
        540,
        20,
    ),
    "wind_gust": LocalSpec(
        "km/h",
        SensorDeviceClass.WIND_SPEED,
        SpeedConverter,
        ("km/h", "m/s", "mph", "kn", "ft/s", "m/min"),
        0,
        540,
        20,
    ),
    "rain_rate": LocalSpec(
        "mm/h",
        SensorDeviceClass.PRECIPITATION_INTENSITY,
        SpeedConverter,
        ("mm/h", "in/h", "mm/d", "in/d"),
        0,
        3000,
        20,
    ),
    "rain_counter": LocalSpec(
        "mm", SensorDeviceClass.PRECIPITATION, DistanceConverter, ("mm", "cm", "in"), 0, 1e9, 60
    ),
}


def validate_binding(hass, variable, entity_id):
    if (
        variable not in LOCAL_SPECS
        or not isinstance(entity_id, str)
        or not entity_id.startswith("sensor.")
    ):
        raise ValueError("Choose a local weather sensor")
    state = hass.states.get(entity_id)
    registered = er.async_get(hass).async_get(entity_id)
    if state is None or (registered and registered.platform == DOMAIN):
        raise ValueError("Do not present an sScape forecast as a local measurement")
    if state.attributes.get("unit_of_measurement") not in LOCAL_SPECS[variable].units:
        raise ValueError("Incompatible measurement unit")


def local_reading(hass, variable, entity_id):
    result = {
        "valid": False,
        "value": None,
        "source_entity_id": entity_id,
        "data_kind": "local_surface_observation",
        "uploaded": False,
        "quality": "unavailable",
        "observation_time": None,
    }
    try:
        validate_binding(hass, variable, entity_id)
    except ValueError:
        return result | {"quality": "incompatible_or_missing_source"}
    state, spec = hass.states.get(entity_id), LOCAL_SPECS[variable]
    result.update(unit=spec.unit, source_unit=state.attributes.get("unit_of_measurement"))
    if state.attributes.get("restored") is True or state.state in ("unknown", "unavailable"):
        return result
    if "observation_time" in state.attributes:
        stamp = _time(state.attributes["observation_time"])
        result.update(
            observation_time=state.attributes["observation_time"],
            time_basis="source_observation_time",
        )
    else:
        stamp = state.last_reported
        result.update(reported_at=stamp.isoformat(), time_basis="home_assistant_report_time")
    if stamp is None:
        return result | {"quality": "invalid_timestamp"}
    age = datetime.now(UTC) - stamp
    if age < -timedelta(seconds=60) or age > timedelta(minutes=spec.age_minutes):
        return result | {"quality": "stale"}
    try:
        value = float(state.state)
        if not math.isfinite(value):
            raise ValueError()
        if spec.converter:
            value = spec.converter.convert(value, result["source_unit"], spec.unit)
        if not spec.lower <= value <= spec.upper:
            raise ValueError()
    except (ValueError, TypeError):
        return result | {"quality": "invalid_value"}
    return result | {
        "valid": True,
        "value": value,
        "quality": "reported_not_independently_verified",
        "measurement_kind": "cumulative_counter_unknown_period"
        if variable == "rain_counter"
        else "instantaneous",
    }


class LocalCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, parent):
        self.parent = parent
        self._unsub_source = None
        self.bindings = {
            (place_id, variable): entity
            for place_id, rows in parent.config_entry.options.get("local_sensors", {}).items()
            if allows_target(parent.config_entry, place_id) and isinstance(rows, dict)
            for variable, entity in rows.items()
            if variable in LOCAL_SPECS and isinstance(entity, str)
        }
        super().__init__(
            hass,
            logging.getLogger(__name__),
            name="sscape local instruments",
            config_entry=parent.config_entry,
            update_interval=timedelta(minutes=1),
        )
        self.async_set_updated_data(self._read())
        if self.bindings:
            self._unsub_source = async_track_state_change_event(
                hass, set(self.bindings.values()), self._source_updated
            )

    def _read(self):
        return {
            key: local_reading(self.hass, key[1], entity) for key, entity in self.bindings.items()
        }

    @callback
    def _source_updated(self, event):
        if not self.parent.stopping:
            self.async_set_updated_data(self._read())

    async def _async_update_data(self):
        return self._read()

    async def async_shutdown(self):
        if self._unsub_source:
            self._unsub_source()
            self._unsub_source = None
        await super().async_shutdown()


def setup_local_sensors(hass, entry, async_add_entities):
    if not entry.options.get("local_sensors"):
        return
    parent = entry.runtime_data
    parent.local_coordinator = LocalCoordinator(hass, parent)

    def create(parent, place):
        return [
            LocalSensor(parent, place, variable)
            for variable in LOCAL_SPECS
            if (place.place_id, variable) in parent.local_coordinator.bindings
        ]

    async_watch_places(hass, entry, async_add_entities, create)


class LocalSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, parent, place, variable):
        super().__init__(parent.local_coordinator)
        self.parent, self.place_id, self.variable = parent, place.place_id, variable
        self._attr_unique_id = f"{parent.config_entry.unique_id}|{place.place_id}|local_{variable}"
        self._attr_translation_key = "local_" + variable
        self._attr_device_class = LOCAL_SPECS[variable].device_class
        self._attr_native_unit_of_measurement = LOCAL_SPECS[variable].unit
        self._attr_device_info = place_device_info(parent, place)

    @property
    def record(self):
        return self.coordinator.data.get((self.place_id, self.variable), {})

    @property
    def available(self):
        source = (
            self.hass.states.get(self.record.get("source_entity_id", "")) if self.hass else None
        )
        return (
            not self.parent.stopping
            and allows_target(self.parent.config_entry, self.place_id)
            and source is not None
            and source.state != "unavailable"
            and not source.attributes.get("restored")
            and self.record.get("quality") != "stale"
        )

    @property
    def native_value(self):
        return self.record.get("value") if self.available and self.record.get("valid") else None

    @property
    def extra_state_attributes(self):
        from .status import status_attributes

        status = (
            "available"
            if self.record.get("valid")
            else "source_stale"
            if self.record.get("quality") == "stale"
            else "missing"
            if self.available
            else "unavailable"
        )
        return {
            key: value for key, value in self.record.items() if key not in ("value", "valid")
        } | status_attributes(status, language=self.hass.config.language if self.hass else "en")
