"""Model forecasts remain separate from actual surface observations."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import CannotConnect, PlaceCatalog, parse_snapshot
from custom_components.sscape.forecast import WeatherSnapshot


@pytest.fixture
def forecast_payload():
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    stamp = now + timedelta(hours=2)
    return {
        "kind": "model_forecast",
        "state": "available",
        "fetched_at": datetime.now(UTC).isoformat(),
        "sources": ["noaa-nws"],
        "source_details": [
            {
                "id": "noaa-nws",
                "attribution": "NOAA/NWS data; processed by sScape",
                "license_url": "https://www.weather.gov/disclaimer",
            }
        ],
        "model": "gfs_seamless",
        "current": {
            "time": now.isoformat(),
            "temperature_c": 0,
            "wind_kmh": 0,
            "weather_code": 0,
            "is_day": False,
        },
        "hours": [
            {
                "time": stamp.isoformat(),
                "period_start": (stamp - timedelta(hours=1)).isoformat(),
                "temperature_c": 4,
                "wind_kmh": 10,
                "gust_kmh": 30,
                "gust_period_start": (stamp - timedelta(hours=3)).isoformat(),
                "precipitation_mm": 0,
                "weather_code": 61,
            }
        ],
    }


def test_forecast_preserves_units_event_time_and_zero(forecast_payload):
    data = WeatherSnapshot.parse(forecast_payload)
    assert data.is_fresh()
    assert data.current_number("temperature_c") == 0
    assert data.current_number("wind_kmh") == 0
    assert data.condition == "clear-night"
    rows = data.hourly()
    assert rows[0]["datetime"] == forecast_payload["hours"][0]["time"]
    assert rows[0]["native_precipitation"] == 0
    assert "native_wind_gust_speed" not in rows[0]  # a 3h maximum is not a 1h maximum


def test_missing_values_not_calm_or_sunny(forecast_payload):
    forecast_payload["current"].pop("weather_code")
    forecast_payload["current"]["temperature_c"] = None
    forecast_payload["hours"][0]["precipitation_mm"] = None
    data = WeatherSnapshot.parse(forecast_payload)
    assert data.current_number("temperature_c") is None
    assert data.condition is None
    assert "native_precipitation" not in data.hourly()[0]


@pytest.mark.parametrize("change", ["old", "future", "unavailable", "wrong_kind", "no_sources"])
def test_unusable_model_data_unavailable(forecast_payload, change):
    if change == "old":
        forecast_payload["fetched_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    elif change == "future":
        forecast_payload["fetched_at"] = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    elif change == "unavailable":
        forecast_payload["state"] = "unavailable"
    elif change == "wrong_kind":
        forecast_payload["kind"] = "observation"
    else:
        forecast_payload["sources"] = []
    assert not WeatherSnapshot.parse(forecast_payload).is_fresh()


def test_old_current_estimate_does_not_hide_valid_future_hours(forecast_payload):
    forecast_payload["current"]["time"] = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    data = WeatherSnapshot.parse(forecast_payload)
    assert data.current_number("temperature_c") is None
    assert data.condition is None
    assert data.hourly()


async def test_native_weather_entity_and_forecast_action(hass, payload, forecast_payload):
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "follow_all_places": False, "weather_enabled": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Home"}, True)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_weather",
            AsyncMock(return_value=forecast_payload),
        ) as weather,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        entity_id = er.async_get(hass).async_get_entity_id(
            "weather", "sscape", f"{entry.unique_id}|place-1|forecast"
        )
        state = hass.states.get(entity_id)
        assert state.state == "clear-night"
        assert state.attributes["temperature"] == 0
        assert state.attributes["wind_speed"] == 0
        assert state.attributes["data_kind"] == "model_forecast"
        result = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": entity_id, "type": "hourly"},
            blocking=True,
            return_response=True,
        )
        assert result[entity_id]["forecast"][0]["precipitation"] == 0
        weather.assert_awaited_once_with("place-1")
        weather.side_effect = CannotConnect()
        await entry.runtime_data.weather_coordinators["place-1"].async_refresh()
        assert hass.states.get(entity_id).state == "unavailable"
        risk_id = er.async_get(hass).async_get_entity_id(
            "sensor", "sscape", f"{entry.unique_id}|place-1|risk"
        )
        assert hass.states.get(risk_id).state == "moderate"


async def test_optional_numeric_outlook_uses_the_same_forecast_request(
    hass, payload, forecast_payload
):
    forecast_payload.update(minimum_temperature_c=0, precipitation_mm=None, peak_gust_kmh=50)
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "weather_enabled": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Home"}, True)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_weather",
            AsyncMock(return_value=forecast_payload),
        ) as weather,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        target = registry.async_get_entity_id(
            "sensor", "sscape", f"{entry.unique_id}|place-1|forecast_minimum_temperature_c"
        )
        registry.async_update_entity(target, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert weather.await_count == 2  # one per setup, not one per entity
        value = hass.states.get(target)
        assert float(value.state) == 0
        assert value.attributes["data_kind"] == "model_forecast"
        assert value.attributes["sources"] == ["noaa-nws"]
