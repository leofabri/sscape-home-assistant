"""Loopback-only HTTP -> actual client -> HA coordinator -> registered entities."""

from datetime import timedelta

import pytest
import pytest_socket
from aiohttp import web
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
