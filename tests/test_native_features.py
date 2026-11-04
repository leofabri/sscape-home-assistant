"""Actual HA entities, read-only actions, repairs and event lifecycle."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from homeassistant.core import Context
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import PlaceCatalog, parse_snapshot


@pytest.fixture
async def integration(hass, payload):
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "weather_enabled": False},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Test place"}, True)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(side_effect=lambda _: parse_snapshot(payload)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry


def entity_id(hass, entry, key, domain="sensor"):
    return er.async_get(hass).async_get_entity_id(
        domain, "sscape", f"{entry.unique_id}|place-1|{key}"
    )


async def test_events_no_startup_duplicate_or_outage_all_clear(hass, payload, integration):
    entry = integration
    event = entity_id(hass, entry, "conditions_changed", "event")
    assert hass.states.get(event).state == "unknown"
    raw = payload["states"][0]["state"]["briefing"]
    raw["risk_level"] = "high"
    entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    first = hass.states.get(event)
    assert first.attributes["event_type"] == "risk_increased"
    raw["summary"] = "An attribute-only update"
    entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    assert hass.states.get(event).state == first.state
    raw["expires_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    assert hass.states.get(event).state == "unavailable"
    raw.update(
        risk_level="low",
        situation_phase="quiet",
        nearest_storm=None,
        expires_at=(datetime.now(UTC) + timedelta(minutes=20)).isoformat(),
    )
    entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    assert hass.states.get(event).state == first.state
    assert hass.states.get(event).attributes["event_type"] == "risk_increased"


async def test_dashboard_and_briefing_actions_are_read_only_and_scope_checked(hass, integration):
    risk = er.async_get(hass).async_get(entity_id(hass, integration, "risk"))
    result = await hass.services.async_call(
        "sscape",
        "get_dashboard",
        {"device_id": risk.device_id},
        blocking=True,
        return_response=True,
    )
    parsed = yaml.safe_load(result["yaml"])
    cards = parsed["views"][0]["cards"]
    assert all(
        card["type"] in ("weather-forecast", "grid", "button", "conditional") for card in cards
    )
    assert entity_id(hass, integration, "risk") in result["yaml"]
    assert "fictional-key" not in result["yaml"] and "account-1" not in result["yaml"]
    assert result["writes_performed"] is False
    briefing = await hass.services.async_call(
        "sscape", "get_briefing", {"device_id": risk.device_id}, blocking=True, return_response=True
    )
    assert briefing["available"] and briefing["risk_level"] == "moderate"
    integration.runtime_data.async_set_update_error(UpdateFailed("Synthetic outage"))
    offline = await hass.services.async_call(
        "sscape", "get_briefing", {"device_id": risk.device_id}, blocking=True, return_response=True
    )
    assert offline == {"available": False, "reason": "assessment_unavailable"}
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "sscape",
            "get_briefing",
            {"device_id": "excluded-device"},
            blocking=True,
            return_response=True,
        )
    await hass.auth.async_create_user("Owner test")
    user = await hass.auth.async_create_user("Read-only test", group_ids=["system-read-only"])
    assert not user.is_admin
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "sscape",
            "get_dashboard",
            {"device_id": risk.device_id},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )


async def test_station_sensor_converts_wind_keeps_scope_and_time(hass, payload, integration):
    stamp = datetime.now(UTC).isoformat()
    payload["states"][0]["state"]["briefing"]["observation_context"] = {
        "readings": [
            {
                "scope": "regional",
                "distance_km": 22,
                "observation": {
                    "kind": "surface_observation",
                    "variable": "wind_gust",
                    "value": 10,
                    "unit": "m/s",
                    "source_id": "test-source",
                    "attribution": "Test data",
                    "license_url": "https://example.com/license",
                    "station": {"name": "Public station"},
                    "quality": {"status": "valid"},
                    "observation_time": stamp,
                },
            }
        ]
    }
    target = entity_id(hass, integration, "observed_gust")
    registry = er.async_get(hass)
    assert registry.async_get(target).disabled_by == er.RegistryEntryDisabler.INTEGRATION
    registry.async_update_entity(target, disabled_by=None)
    await hass.config_entries.async_reload(integration.entry_id)
    await hass.async_block_till_done()
    value = hass.states.get(target)
    assert float(value.state) == 36
    assert value.attributes["distance_km"] == 22 and value.attributes["scope"] == "regional"
    assert value.attributes["observation_time"] == stamp


async def test_delayed_data_repairs_do_not_conflate_unknown_with_failure(
    hass, payload, integration
):
    coordinator = integration.runtime_data
    raw = payload["states"][0]["state"]["briefing"]
    raw["data_quality_status"] = "unknown"
    coordinator._update_repairs(parse_snapshot(payload))
    issue = ("sscape", integration.entry_id + "_data_delayed")
    assert issue not in ir.async_get(hass).issues
    raw["data_quality_status"] = "stale"
    coordinator._stale_since = datetime.now(UTC) - timedelta(minutes=6)
    coordinator._update_repairs(parse_snapshot(payload))
    assert issue in ir.async_get(hass).issues
    raw["data_quality_status"] = "partial"
    coordinator._update_repairs(parse_snapshot(payload))
    assert issue not in ir.async_get(hass).issues


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ({"risk_level": "high"}, {"risk_level": "moderate"}, "risk_decreased"),
        ({"situation_phase": "watch"}, {"situation_phase": "approach"}, "storm_approaching"),
        (
            {"situation_phase": "approach"},
            {"situation_phase": "watch", "nearest_storm": {"moving_away_from_place": True}},
            "storm_moving_away",
        ),
        ({"official_alert_count": 0}, {"official_alert_count": 1}, "official_warning_changed"),
        ({"data_quality_status": "partial"}, {"data_quality_status": "stale"}, "data_degraded"),
        ({"data_quality_status": "stale"}, {"data_quality_status": "partial"}, "data_recovered"),
    ],
)
async def test_native_event_types(hass, payload, integration, before, after, expected):
    raw = payload["states"][0]["state"]["briefing"]
    raw.update(before)
    integration.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    raw.update(after)
    integration.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id(hass, integration, "conditions_changed", "event"))
    assert state.attributes["event_type"] == expected
