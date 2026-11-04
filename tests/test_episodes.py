"""Notifications group exact episodes, not nearby locations or matching text."""

from copy import deepcopy
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import PlaceCatalog, parse_snapshot
from custom_components.sscape.episodes import collect_episodes


def test_same_native_storm_is_grouped_different_native_storms_are_not(payload):
    b = payload["states"][0]["state"]["briefing"]
    b["nearest_storm"] = {"id": "storm-one"}
    second = deepcopy(payload["states"][0])
    second["place"].update(id="place-2", name="Second place")
    payload["states"].append(second)
    events = collect_episodes(parse_snapshot(payload).places.values(), ["storm"])
    assert len(events) == 1
    assert len(next(iter(events.values()))["places"]) == 2
    second["state"]["briefing"]["nearest_storm"]["id"] = "storm-two"
    assert len(collect_episodes(parse_snapshot(payload).places.values(), ["storm"])) == 2
    b["nearest_storm"].pop("id")
    assert len(collect_episodes(parse_snapshot(payload).places.values(), ["storm"])) == 1


async def test_notification_is_opt_in_grouped_not_replayed_or_all_clear(hass, payload):
    sent = AsyncMock()
    hass.services.async_register("notify", "fictional_phone", sent)
    payload["states"][0]["state"]["briefing"]["nearest_storm"] = {"id": "storm-one"}
    second = deepcopy(payload["states"][0])
    second["place"].update(id="place-2", name="Second place")
    payload["states"].append(second)
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={
            "places": ["place-1", "place-2"],
            "weather_enabled": False,
            "episode_notifications": True,
            "notify_service": "notify.fictional_phone",
        },
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(
                return_value=PlaceCatalog(
                    "account-1", {"place-1": "First", "place-2": "Second"}, False
                )
            ),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(side_effect=lambda _: parse_snapshot(payload)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        sent.assert_not_called()
        for row in payload["states"]:
            row["state"]["briefing"]["risk_level"] = "high"
        entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert sent.await_count == 1
        assert "Second place" in sent.call_args.args[0].data["message"]
        tag = sent.call_args.args[0].data["data"]["tag"]
        assert "account-1" not in tag and "storm-one" not in tag
        entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert sent.await_count == 1
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
        assert sent.await_count == 1
        for row in payload["states"]:
            row["state"]["briefing"]["situation_phase"] = "quiet"
        entry.runtime_data.async_set_updated_data(parse_snapshot(payload))
        await hass.async_block_till_done(wait_background_tasks=True)
        assert sent.await_count == 1
        assert er.async_get(hass).entities
