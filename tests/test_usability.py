"""User-facing state semantics, not-applicable values and calm defaults."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import PlaceCatalog, PlaceSnapshot, parse_snapshot
from custom_components.sscape.status import value_status


def test_no_storm_eta_is_not_a_connection_failure(payload):
    b = payload["states"][0]["state"]["briefing"]
    b.update(situation_phase="quiet", approach_eta_minutes=None)
    place = parse_snapshot(payload).places["place-1"]
    assert value_status(place, "eta", None)["data_status"] == "not_applicable"
    assert value_status(place, "eta", None)["status_message"]
    assert value_status(place, "eta", None, connected=False)["data_status"] == "unavailable"


def test_missing_detail_does_not_claim_no_warning(payload):
    b = payload["states"][0]["state"]["briefing"]
    place = parse_snapshot(payload).places["place-1"]
    b["official_alert_count"] = 0
    assert value_status(place, "official_warning_details", None)["data_status"] == "not_applicable"
    b["official_alert_count"] = 1
    assert value_status(place, "official_warning_details", None)["data_status"] == "missing"
    assert value_status(place, "official_warning_details", "Warning")["data_status"] == "available"


def test_expired_assessment_and_real_zero_are_distinct(payload):
    place = parse_snapshot(payload).places["place-1"]
    assert value_status(place, "lightning_count", 0)["data_status"] == "available"
    place.briefing["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert value_status(place, "eta", None)["data_status"] == "stale"


@pytest.mark.parametrize("language", ["en", "it"])
def test_source_staleness_does_not_become_a_zero(payload, language):
    place = parse_snapshot(payload).places["place-1"]
    place.briefing["data_quality"] = {"lightning": {"state": "stale"}}
    status = value_status(place, "lightning_detected", None, language=language)
    assert status["data_status"] == "source_stale"


async def test_initial_home_position_has_no_manual_coordinates_or_unselected_fetch(hass, payload):
    async def point(client, value, owner=None):
        client.account_id = "account-1"
        assert (
            value["latitude"] == hass.config.latitude
            and value["longitude"] == hass.config.longitude
        )
        return PlaceSnapshot(
            value["id"], value["name"], payload["states"][0]["state"]["briefing"], True
        )

    with (
        patch("custom_components.sscape.api.SScapeClient.async_get_point", point),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog", AsyncMock()
        ) as catalog,
        patch("custom_components.sscape.async_setup_entry", AsyncMock(return_value=True)),
    ):
        flow = await hass.config_entries.flow.async_init(
            "sscape",
            context={"source": SOURCE_USER},
            data={
                "host": "https://example.com",
                "api_key": "fictional-key",
                "location_mode": "home",
            },
        )
        assert flow["step_id"] == "coordinate_target"
        assert "latitude" not in {key.schema for key in flow["data_schema"].schema}
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"name": "Home", "radius_km": 25}
        )
        assert result["step_id"] == "preferences"
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
        assert result["step_id"] == "review"
        result = await hass.config_entries.flow.async_configure(flow["flow_id"], {})
        assert (
            result["options"]["coordinate_only"]
            and result["options"]["coordinate_targets"][0]["origin"] == "home_assistant"
        )
        catalog.assert_not_called()


async def test_pending_assist_exposure_is_one_time_and_skips_foreign_targets(hass, payload):
    from homeassistant.components.homeassistant.exposed_entities import (
        async_expose_entity,
        async_should_expose,
    )
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={
            "places": ["place-1"],
            "weather_enabled": False,
            "assist_enabled": True,
            "assist_exposure_pending": ["place-1", "foreign-target"],
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Home"}, False)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        risk = er.async_get(hass).async_get_entity_id(
            "sensor", "sscape", entry.unique_id + "|place-1|risk"
        )
        assert async_should_expose(hass, "conversation", risk)
        assert "assist_exposure_pending" not in entry.options
        async_expose_entity(hass, "conversation", risk, False)
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert not async_should_expose(hass, "conversation", risk)


async def test_dashboard_scope_mapping_does_not_mix_accounts_with_equal_place_names(hass, payload):
    from copy import deepcopy

    from homeassistant.helpers import entity_registry as er

    entries = []
    bodies = {}
    for index in (1, 2):
        account = f"account-{index}"
        place = f"place-{index}"
        body = deepcopy(payload)
        body["states"][0]["place"].update(id=place, user_id=account, name="Same name")
        bodies[account] = body
        entry = MockConfigEntry(
            domain="sscape",
            version=2,
            title="sScape",
            unique_id="https://example.com|" + account,
            data={"host": "https://example.com", "api_key": "fictional-key", "account_id": account},
            options={"places": [place], "weather_enabled": False},
        )
        entry.add_to_hass(hass)
        entries.append(entry)

    async def catalog(client):
        rows = bodies[client.account_id]["states"]
        return PlaceCatalog(
            client.account_id, {row["place"]["id"]: row["place"]["name"] for row in rows}, False
        )

    async def snapshot(client, selected):
        return parse_snapshot(bodies[client.account_id])

    with (
        patch("custom_components.sscape.api.SScapeClient.async_get_catalog", catalog),
        patch("custom_components.sscape.api.SScapeClient.async_get_snapshot", snapshot),
    ):
        for entry in entries:
            # Initial integration setup can load the other registered entry too.
            if entry.state != ConfigEntryState.LOADED:
                assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert all(entry.state == ConfigEntryState.LOADED for entry in entries)
        registry = er.async_get(hass)
        ids = [
            registry.async_get_entity_id(
                "sensor", "sscape", entry.unique_id + f"|place-{index}|risk"
            )
            for index, entry in enumerate(entries, start=1)
        ]
        devices = [registry.async_get(key).device_id for key in ids]
        result = await hass.services.async_call(
            "sscape",
            "get_dashboard",
            {"device_id": devices[0], "all_selected_places": True},
            blocking=True,
            return_response=True,
        )
        assert result["target_device_ids"] == [devices[0]]
        assert result["view_targets"] == {"sscape-1": devices[0]}
        assert ids[1] not in str(result["config"])
