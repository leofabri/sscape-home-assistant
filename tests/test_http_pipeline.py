"""Loopback-only HTTP -> actual client -> HA coordinator -> registered entities."""

import asyncio
from datetime import timedelta
from unittest.mock import patch

import pytest
import pytest_socket
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST, STATE_UNAVAILABLE
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.const import DOMAIN


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_http_to_home_assistant(hass, aiohttp_server, payload, socket_enabled):
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    requests = 0
    status = 200

    async def states(request):
        nonlocal requests
        requests += 1
        assert request.headers["X-API-Key"] == "fictional-key"
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_get("/api/v1/integrations/places/states", states)

    async def catalog(request):
        return web.json_response(
            {
                "contract_version": "home-assistant.v2",
                "account_id": "account-1",
                "places": [{"id": "place-1", "name": "Test place"}],
            }
        )

    app.router.add_get("/api/v1/integrations/places", catalog)
    server = await aiohttp_server(app)
    host = str(server.make_url("/")).rstrip("/")
    identity = f"{host}|account-1"
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sScape",
        unique_id=identity,
        options={"places": ["place-1"], "follow_all_places": False, "weather_enabled": False},
        data={
            CONF_HOST: host,
            CONF_API_KEY: "fictional-key",
            "account_id": "account-1",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{identity}|place-1|risk")
    assert hass.states.get(entity_id).state == "moderate"
    assert requests == 1

    payload["states"][0]["state"]["briefing"]["expires_at"] = (
        dt_util.utcnow() - timedelta(seconds=1)
    ).isoformat()
    await entry.runtime_data.async_refresh()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    status = 401
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == STATE_UNAVAILABLE
    assert any(
        flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress()
    )
    assert requests == 3


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_real_http_push_reauth_reload_preserves_entities(
    hass, aiohttp_server, payload, socket_enabled
):
    """Keep an actual HTTP SSE connection open throughout key rotation."""
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    accepted_key = "old-key"
    release = asyncio.Event()
    connections = []

    async def catalog(request):
        return web.json_response(
            {
                "contract_version": "home-assistant.v2",
                "account_id": "account-1",
                "capabilities": ["places.stream.v1"],
                "places": [{"id": "place-1", "name": "Test place"}],
            }
        )

    async def states(request):
        if request.headers.get("X-API-Key") != accepted_key:
            return web.Response(status=401)
        return web.json_response(payload)

    async def stream(request):
        assert request.headers["X-API-Key"] == accepted_key
        assert request.query["place_ids"] == "place-1"
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        connections.append(request.transport)
        await response.write(b"event: ready\ndata: {}\n\n")
        await release.wait()
        return response

    app = web.Application()
    app.router.add_get("/api/v1/integrations/places", catalog)
    app.router.add_get("/api/v1/integrations/places/states", states)
    app.router.add_get("/api/v1/integrations/places/stream", stream)
    server = await aiohttp_server(app)
    host = str(server.make_url("/")).rstrip("/")
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sScape",
        unique_id=f"{host}|account-1",
        options={"places": ["place-1"], "weather_enabled": False},
        data={CONF_HOST: host, CONF_API_KEY: accepted_key, "account_id": "account-1"},
    )
    entry.add_to_hass(hass)

    async def connected():
        parent = entry.runtime_data
        if parent.stream_connected:
            return
        ready = asyncio.Event()
        remove = parent.async_add_listener(lambda: ready.set() if parent.stream_connected else None)
        try:
            await ready.wait()
        finally:
            remove()

    try:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await asyncio.wait_for(connected(), 2)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        before = {item.entity_id: item.device_id for item in registry.entities.values()}
        parent = entry.runtime_data
        accepted_key = "new-key"
        await parent.async_refresh()
        await hass.async_block_till_done()
        flow = next(iter(hass.config_entries.flow.async_progress()))
        with patch.object(
            hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
        ) as reload:
            result = await hass.config_entries.flow.async_configure(
                flow["flow_id"], {CONF_API_KEY: accepted_key}
            )
            await hass.async_block_till_done()
        assert result["reason"] == "reauth_successful"
        assert reload.await_count == 1
        assert entry.state is ConfigEntryState.LOADED
        await asyncio.wait_for(connected(), 2)
        assert parent._stream_task.done()
        assert len(connections) == 2
        assert connections[0].is_closing()
        assert not connections[1].is_closing()
        assert {item.entity_id: item.device_id for item in registry.entities.values()} == before
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
    finally:
        release.set()
