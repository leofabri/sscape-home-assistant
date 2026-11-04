"""Useful timing from hourly forecasts, with honest gaps and source periods."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import PlaceCatalog, parse_snapshot
from custom_components.sscape.forecast import WeatherSnapshot
from custom_components.sscape.planning import make_outlook


@pytest.fixture
def planning_data():
    now = datetime.now(UTC).replace(minute=30, second=0, microsecond=0)
    start = now.replace(minute=0)
    raw = {
        "kind": "model_forecast",
        "state": "available",
        "fetched_at": datetime.now(UTC).isoformat(),
        "sources": ["noaa-nws"],
        "source_details": [
            {
                "id": "noaa-nws",
                "attribution": "NOAA/NWS",
                "license_url": "https://www.weather.gov/disclaimer",
            }
        ],
        "hours": [
            {
                "time": (start + timedelta(hours=i + 1)).isoformat(),
                "period_start": (start + timedelta(hours=i)).isoformat(),
                "gust_period_start": (start + timedelta(hours=i)).isoformat(),
                "temperature_c": 12 - i,
                "precipitation_mm": 0 if i not in (2, 3, 5) else 1.5,
                "gust_kmh": 50 if i == 3 else 20,
            }
            for i in range(25)
        ],
    }
    return raw, now


def test_timing_windows_original_units_and_attribution(planning_data):
    raw, now = planning_data
    result = make_outlook(WeatherSnapshot.parse(raw), now=now)
    assert result["available"] and result["data_kind"] == "model_forecast"
    assert result["includes_partial_current_hour"]
    assert result["precipitation"]["status"] == "expected"
    assert result["precipitation"]["complete"]
    windows = result["precipitation"]["windows"]
    assert len(windows) == 2
    assert windows[0]["start"] == raw["hours"][2]["period_start"]
    assert windows[0]["end"] == raw["hours"][3]["time"]
    assert windows[0]["amount_mm"] == 3
    assert result["precipitation"]["first_window"] == windows[0]
    assert result["wind_gust"]["peak_kmh"] == 50
    assert result["wind_gust"]["period_end"] == raw["hours"][3]["time"]
    assert result["temperature"]["minimum_c"] == 6
    assert result["temperature"]["time"] == raw["hours"][6]["time"]
    assert result["source_details"] == raw["source_details"]


@pytest.mark.parametrize("change", ["missing", "negative", "malformed", "wrong_period", "conflict"])
def test_gap_cannot_become_dry_or_exact_first_rain(planning_data, change):
    raw, now = planning_data
    if change == "missing":
        raw["hours"].pop(0)
    elif change == "negative":
        raw["hours"][0]["precipitation_mm"] = -1
    elif change == "malformed":
        raw["hours"][0]["precipitation_mm"] = "0"
    elif change == "wrong_period":
        raw["hours"][0]["period_start"] = (now - timedelta(hours=3)).isoformat()
    else:
        row = deepcopy(raw["hours"][0])
        row["precipitation_mm"] = 99
        raw["hours"].append(row)
    rain = make_outlook(WeatherSnapshot.parse(raw), now=now)["precipitation"]
    assert rain["status"] == "expected"  # positive future samples still exist
    assert not rain["complete"]
    assert rain["first_window"] is None  # earlier missing period could also contain rain
    invalid = raw["hours"][0].get("precipitation_mm")
    for row in raw["hours"]:
        row["precipitation_mm"] = 0
    if change in ("negative", "malformed"):
        raw["hours"][0]["precipitation_mm"] = invalid
    # A duplicate which became identical is no longer a conflict.
    if change == "conflict":
        raw["hours"][-1]["precipitation_mm"] = 99
    rain = make_outlook(WeatherSnapshot.parse(raw), now=now)["precipitation"]
    assert rain["status"] == "unknown"


def test_dry_only_with_complete_coverage_and_missing_stays_missing(planning_data):
    raw, now = planning_data
    for row in raw["hours"]:
        row["precipitation_mm"] = 0
    result = make_outlook(WeatherSnapshot.parse(raw), now=now)
    assert result["precipitation"]["status"] == "not_expected"
    assert result["precipitation"]["first_window"] is None
    raw["hours"][0]["precipitation_mm"] = None
    raw["hours"][2]["temperature_c"] = None
    result = make_outlook(WeatherSnapshot.parse(raw), now=now)
    assert result["precipitation"]["status"] == "unknown"
    assert result["temperature"]["minimum_c"] is None


def test_three_hour_gust_is_not_an_hourly_peak(planning_data):
    raw, now = planning_data
    raw["hours"][0]["gust_period_start"] = (now - timedelta(hours=3)).isoformat()
    gust = make_outlook(WeatherSnapshot.parse(raw), now=now)["wind_gust"]
    assert not gust["complete"]
    assert gust["peak_kmh"] is None and gust["period_end"] is None


def test_old_unattributed_unknown_and_identical_duplicates(planning_data):
    raw, now = planning_data
    raw["hours"].append(deepcopy(raw["hours"][0]))
    assert make_outlook(WeatherSnapshot.parse(raw), now=now)["precipitation"]["complete"]
    raw["fetched_at"] = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    assert make_outlook(WeatherSnapshot.parse(raw), now=now) == {
        "available": False,
        "reason": "forecast_unavailable",
    }
    raw["fetched_at"] = datetime.now(UTC).isoformat()
    raw["source_details"] = []
    assert not make_outlook(WeatherSnapshot.parse(raw), now=now)["available"]


def test_boundaries_use_utc_and_no_sub_hour_precision(planning_data):
    raw, now = planning_data
    raw["hours"].reverse()
    now = now.replace(minute=0)
    result = make_outlook(WeatherSnapshot.parse(raw), now=now, hours=6)
    assert not result["includes_partial_current_hour"]
    assert result["period_end"] == (now + timedelta(hours=6)).isoformat()
    with pytest.raises(ValueError):
        make_outlook(WeatherSnapshot.parse(raw), hours=25)


def test_real_api_declared_start_is_next_complete_hour_not_current_hour(planning_data):
    raw, now = planning_data
    declared = now.replace(minute=0) + timedelta(hours=1)
    raw["start"] = declared.isoformat()
    raw["hours"] = [row for row in raw["hours"] if _parse(row["period_start"]) >= declared]
    result = make_outlook(WeatherSnapshot.parse(raw), now=now)
    assert result["period_start"] == declared.isoformat()
    assert result["coverage_starts_after_now"]
    assert not result["includes_partial_current_hour"]
    assert result["precipitation"]["complete"]
    assert result["wind_gust"]["complete"]
    assert result["temperature"]["complete"]
    raw["start"] = (declared + timedelta(hours=5)).isoformat()
    assert make_outlook(WeatherSnapshot.parse(raw), now=now)["reason"] == "forecast_not_yet_valid"


def test_invalid_dates_and_non_object_rows_are_not_silently_repaired(planning_data):
    raw, now = planning_data
    raw["hours"].extend([None, "bad row"])
    assert make_outlook(WeatherSnapshot.parse(raw), now=now)["precipitation"]["complete"]
    with pytest.raises(ValueError, match="timezone-aware"):
        make_outlook(WeatherSnapshot.parse(raw), now=now.replace(tzinfo=None))
    raw["start"] = now.isoformat()  # not an hourly boundary
    assert make_outlook(WeatherSnapshot.parse(raw), now=now)["reason"] == "invalid_forecast_period"


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.fixture
async def planning_integration(hass, payload, planning_data):
    raw, _ = planning_data
    for key, name in (("place-2", "Second place"), ("excluded-place", "Excluded place")):
        state = deepcopy(payload["states"][0])
        state["place"].update(id=key, name=name)
        payload["states"].append(state)
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1", "place-2"], "weather_enabled": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(
                return_value=PlaceCatalog(
                    "account-1",
                    {item["place"]["id"]: item["place"]["name"] for item in payload["states"]},
                    True,
                )
            ),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_weather",
            AsyncMock(return_value=raw),
        ) as weather,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, weather


def _id(hass, entry, key):
    return er.async_get(hass).async_get_entity_id(
        "sensor", "sscape", f"{entry.unique_id}|place-1|{key}"
    )


async def test_native_planning_sensors_use_shared_forecast(
    hass, planning_integration, planning_data
):
    entry, weather = planning_integration
    raw, _ = planning_data
    rain = _id(hass, entry, "forecast_precipitation_outlook")
    assert hass.states.get(rain).state == "expected"
    assert hass.states.get(rain).attributes["data_kind"] == "model_forecast"
    assert weather.await_count == 2  # one request per selected place, none for excluded
    registry = er.async_get(hass)
    for key in ("next_rain_window", "gust_peak_time", "temperature_minimum_time"):
        target = _id(hass, entry, "forecast_" + key)
        assert registry.async_get(target).disabled_by is er.RegistryEntryDisabler.INTEGRATION
        registry.async_update_entity(target, disabled_by=None)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert weather.await_count == 4
    assert (
        hass.states.get(_id(hass, entry, "forecast_next_rain_window")).state
        == raw["hours"][2]["period_start"]
    )
    assert (
        hass.states.get(_id(hass, entry, "forecast_gust_peak_time")).state
        == raw["hours"][3]["time"]
    )
    assert (
        hass.states.get(_id(hass, entry, "forecast_temperature_minimum_time")).state
        == raw["hours"][24]["time"]
    )


async def test_outlook_service_is_cached_scoped_and_outage_safe(hass, planning_integration):
    entry, weather = planning_integration
    device = er.async_get(hass).async_get(_id(hass, entry, "risk")).device_id

    async def read(**extra):
        return await hass.services.async_call(
            "sscape",
            "get_outlook",
            {"device_id": device, **extra},
            blocking=True,
            return_response=True,
        )

    before = weather.await_count
    result = await read()
    assert result["precipitation"]["status"] == "expected"
    assert result["wind_gust"]["peak_kmh"] == 50
    assert weather.await_count == before
    await hass.auth.async_create_user("Test owner")
    user = await hass.auth.async_create_user("Test reader", group_ids=["system-read-only"])
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "sscape",
            "get_outlook",
            {"device_id": device},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "sscape",
            "get_outlook",
            {"device_id": "excluded-device"},
            blocking=True,
            return_response=True,
        )
    excluded = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("sscape", f"{entry.unique_id}|excluded-place")},
        name="Previously imported excluded place",
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "sscape",
            "get_outlook",
            {"device_id": excluded.id},
            blocking=True,
            return_response=True,
        )
    entry.runtime_data.weather_coordinators["place-1"].async_set_update_error(
        UpdateFailed("Test outage")
    )
    assert await read() == {"available": False, "reason": "forecast_unavailable"}
    assert (
        hass.states.get(_id(hass, entry, "forecast_precipitation_outlook")).state == "unavailable"
    )


async def test_account_dashboard_uses_selected_places_and_conditional_timings(
    hass, planning_integration
):
    entry, _ = planning_integration
    device = er.async_get(hass).async_get(_id(hass, entry, "risk")).device_id
    result = await hass.services.async_call(
        "sscape",
        "get_dashboard",
        {"device_id": device, "all_selected_places": True},
        blocking=True,
        return_response=True,
    )
    assert not result["writes_performed"]
    assert "excluded-place" not in result["yaml"] and "Excluded place" not in result["yaml"]
    assert "fictional-key" not in result["yaml"] and "account-1" not in result["yaml"]
    views = yaml.safe_load(result["yaml"])["views"]
    assert len(views) == 3  # overview plus two selected places
    assert len(views[0]["cards"][0]["entities"]) == 2
    cards = next(view["cards"] for view in views if view["title"] == "Test place")
    eta = _id(hass, entry, "eta")
    conditional = next(
        card for card in cards if card["type"] == "conditional" and card["card"]["entity"] == eta
    )
    assert {rule["state_not"] for rule in conditional["conditions"]} == {"unknown", "unavailable"}
    assert cards[-1]["tap_action"]["url_path"] == "https://example.com/map?place=place-1"
    # Risk/outage state is always present, unlike inapplicable timing.
    assert any(
        card.get("entity") == _id(hass, entry, "risk")
        for group in cards
        if group["type"] == "grid"
        for card in group["cards"]
    )


async def test_incomplete_forecast_is_not_dry_and_does_not_hide_risk(
    hass, planning_integration, planning_data
):
    entry, _ = planning_integration
    raw, _ = planning_data
    for row in raw["hours"]:
        row["precipitation_mm"] = 0
    raw["hours"][0]["precipitation_mm"] = None
    entry.runtime_data.weather_coordinators["place-1"].async_set_updated_data(
        WeatherSnapshot.parse(raw)
    )
    await hass.async_block_till_done()
    assert (
        hass.states.get(_id(hass, entry, "forecast_precipitation_outlook")).state
        == "data_incomplete"
    )
    assert hass.states.get(_id(hass, entry, "risk")).state == "moderate"


async def test_initial_forecast_is_owned_by_coordinator_not_one_entity(hass, planning_integration):
    entry, _ = planning_integration
    from custom_components.sscape.weather import SScapeWeatherCoordinator, start_weather_refresh

    coordinator = SScapeWeatherCoordinator(hass, entry.runtime_data, "another-place")
    started = asyncio.Event()

    async def pending():
        started.set()
        await asyncio.Event().wait()

    with patch.object(coordinator, "async_refresh", pending):
        start_weather_refresh(hass, coordinator)
        await asyncio.wait_for(started.wait(), 2)
        assert not coordinator._initial_refresh_task.done()
        await coordinator.async_shutdown()
        assert coordinator._initial_refresh_task.done()
