"""Real config-entry reloads with push running, not mocked lifecycle methods."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import CONF_API_KEY
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import AuthenticationError, PlaceCatalog, parse_snapshot


@pytest.fixture
def entry(hass):
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "old-key", "account_id": "account-1"},
        options={"follow_all_places": True, "weather_enabled": False},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def client(payload):
    async def updates(self, ids):
        yield "ready"
        await asyncio.Event().wait()

    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(
                return_value=PlaceCatalog(
                    "account-1", {"place-1": "Test place"}, False, ("places.stream.v1",)
                )
            ),
        ),
        patch("custom_components.sscape.api.SScapeClient.async_updates", updates),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ) as snapshot,
    ):
        yield snapshot


@pytest.mark.parametrize("key", ["new-key", "old-key"])
async def test_reauth_loaded_push_entry_reloads_only_once(hass, entry, client, key):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    parent = entry.runtime_data
    assert parent.stream_connected
    registry = er.async_get(hass)
    before = {item.entity_id: item.device_id for item in registry.entities.values()}
    client.side_effect = AuthenticationError()
    await parent.async_refresh()
    await hass.async_block_till_done()
    flow = next(
        flow
        for flow in hass.config_entries.flow.async_progress()
        if flow["context"]["source"] == SOURCE_REAUTH
    )
    client.side_effect = None
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_API_KEY: key}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert reload.await_count == 1
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_API_KEY] == key
    assert entry.runtime_data is not parent
    assert parent._stream_task.done()
    assert entry.runtime_data.stream_connected
    assert {item.entity_id: item.device_id for item in registry.entities.values()} == before


async def test_stream_stops_before_platform_unload_and_cannot_restart(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    parent = entry.runtime_data
    stream_task = parent._stream_task
    unload_platforms = hass.config_entries.async_unload_platforms

    async def checked_unload(config, platforms):
        assert stream_task.done(), "Push must stop before entity platforms are removed"
        parent.start_stream()
        assert parent._stream_task is stream_task
        return await unload_platforms(config, platforms)

    with patch.object(hass.config_entries, "async_unload_platforms", checked_unload):
        assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert not parent.stream_connected


async def test_late_discovery_during_unload_does_not_create_devices(hass, entry, client, payload):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    parent = entry.runtime_data
    registry = dr.async_get(hass)
    before = {device.id for device in dr.async_entries_for_config_entry(registry, entry.entry_id)}
    new = deepcopy(payload["states"][0])
    new["place"]["id"] = "late-place"
    payload["states"].append(new)
    unload_platforms = hass.config_entries.async_unload_platforms

    async def checked_unload(config, platforms):
        # Model a request which completed after unloading started.
        parent.async_set_updated_data(parse_snapshot(payload))
        await hass.async_block_till_done()
        assert {
            device.id for device in dr.async_entries_for_config_entry(registry, entry.entry_id)
        } == before
        return await unload_platforms(config, platforms)

    with patch.object(hass.config_entries, "async_unload_platforms", checked_unload):
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_repeated_push_reload_keeps_entity_ids_and_stops_old_streams(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    before = {item.entity_id: item.device_id for item in registry.entities.values()}
    for _ in range(3):
        old = entry.runtime_data
        assert old.stream_connected
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert old._stream_task.done()
        assert not old.stream_connected
        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data.stream_connected
        assert {item.entity_id: item.device_id for item in registry.entities.values()} == before
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.parametrize("auth_failure", [False, True])
async def test_late_http_after_entry_removed_cannot_create_entities_or_reauth(
    hass, entry, client, payload, auth_failure
):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    parent = entry.runtime_data
    started, release = asyncio.Event(), asyncio.Event()
    new = deepcopy(payload["states"][0])
    new["place"]["id"] = "late-place"
    payload["states"].append(new)

    async def delayed(ids):
        started.set()
        await release.wait()
        if auth_failure:
            raise AuthenticationError()
        return parse_snapshot(payload)

    client.side_effect = delayed
    refresh = hass.async_create_task(parent.async_refresh())
    await asyncio.wait_for(started.wait(), 2)
    assert not (await hass.config_entries.async_remove(entry.entry_id))["require_restart"]
    release.set()
    await refresh  # HA treats a shutdown cancellation as a completed refresh.
    await hass.async_block_till_done()
    assert not hass.config_entries.flow.async_progress()
    assert not dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert not er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


async def test_unload_one_account_keeps_other_account_and_actions(hass, entry, client):
    second = MockConfigEntry(
        domain="sscape",
        version=2,
        title="Second server",
        unique_id="https://second.example.com|account-1",
        data={**entry.data, "host": "https://second.example.com"},
        options=dict(entry.options),
    )
    second.add_to_hass(hass)
    for config in (entry, second):
        if config.state is ConfigEntryState.NOT_LOADED:
            assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert second.runtime_data.stream_connected
    assert hass.services.has_service("sscape", "get_briefing")
    assert await hass.config_entries.async_unload(second.entry_id)
    assert not hass.services.has_service("sscape", "get_briefing")


async def test_reauth_after_failed_initial_setup_reloads_once(hass, entry, client):
    client.side_effect = AuthenticationError()
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flow = next(iter(hass.config_entries.flow.async_progress()))
    client.side_effect = None
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        result = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {CONF_API_KEY: "replacement-key"}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert reload.await_count == 1
    assert entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize("auth_failure", [False, True])
async def test_late_weather_after_removal_does_not_start_reauth(hass, entry, client, auth_failure):
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "weather_enabled": True}
    )
    forecast = {
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
    }
    with patch(
        "custom_components.sscape.api.SScapeClient.async_get_weather",
        AsyncMock(return_value=forecast),
    ) as weather:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data.weather_coordinators["place-1"]
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(ids):
            started.set()
            await release.wait()
            if auth_failure:
                raise AuthenticationError()
            return forecast

        weather.side_effect = delayed
        refresh = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(started.wait(), 2)
        assert not (await hass.config_entries.async_remove(entry.entry_id))["require_restart"]
        release.set()
        await refresh
        await hass.async_block_till_done()
        assert not hass.config_entries.flow.async_progress()
        with pytest.raises(asyncio.CancelledError):
            await coordinator._async_update_data()
        assert weather.await_count == 2  # no request after shutdown
