"""Local instruments never upload and never masquerade as model/station data."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sscape.local_sensors import local_reading, validate_binding


@pytest.mark.parametrize(
    "variable,value,unit,expected",
    [
        ("temperature", "32", "°F", 0),
        ("wind_speed", "10", "m/s", 36),
        ("wind_gust", "10", "kn", 18.52),
        ("pressure", "101300", "Pa", 1013),
        ("humidity", "0", "%", 0),
        ("rain_counter", "1", "in", 25.4),
        ("rain_rate", "1", "in/h", 25.4),
    ],
)
def test_units_and_explicit_report_time(hass, variable, value, unit, expected):
    hass.states.async_set("sensor.outside", value, {"unit_of_measurement": unit})
    validate_binding(hass, variable, "sensor.outside")
    reading = local_reading(hass, variable, "sensor.outside")
    assert reading["valid"] and reading["value"] == pytest.approx(expected, abs=0.01)
    assert reading["data_kind"] == "local_surface_observation"
    assert reading["observation_time"] is None
    assert reading["time_basis"] == "home_assistant_report_time"
    assert not reading["uploaded"]


@pytest.mark.parametrize("value", ["unavailable", "unknown", "nan", "inf", "not-a-number", "1000"])
def test_invalid_temperature_is_not_zero(hass, value):
    hass.states.async_set("sensor.outside", value, {"unit_of_measurement": "°C"})
    reading = local_reading(hass, "temperature", "sensor.outside")
    assert not reading["valid"] and reading["value"] is None


def test_original_observation_clock_and_freshness(hass):
    stamp = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    hass.states.async_set(
        "sensor.outside", "10", {"unit_of_measurement": "°C", "observation_time": stamp}
    )
    reading = local_reading(hass, "temperature", "sensor.outside")
    assert not reading["valid"] and reading["quality"] == "stale"
    assert reading["observation_time"] == stamp
    assert reading["value"] is None


def test_incompatible_units_and_counters_not_rain_rate(hass):
    hass.states.async_set("sensor.rain", "12", {"unit_of_measurement": "mm"})
    assert local_reading(hass, "rain_counter", "sensor.rain")["valid"]
    with pytest.raises(ValueError):
        validate_binding(hass, "rain_rate", "sensor.rain")
    assert not local_reading(hass, "rain_rate", "sensor.rain")["valid"]
