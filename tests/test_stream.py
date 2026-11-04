"""Loopback SSE and coordinator push: scoped, reconciled and cancellable."""

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import pytest_socket
from aiohttp import web
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import (
    AuthenticationError,
    PermissionDenied,
    RateLimited,
    SScapeClient,
)
from custom_components.sscape.coordinator import SScapeCoordinator


@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_real_sse_uses_header_key_and_only_selected_places(aiohttp_server, socket_enabled):
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)

    async def stream(request):
        assert request.headers["X-API-Key"] == "fictional-key"
        assert dict(request.query) == {"place_ids": "place-1"}
        return web.Response(
            text="event: ready\ndata: {}\n\nevent: refresh\ndata: {}\n\n",
            content_type="text/event-stream",
        )

    app = web.Application()
    app.router.add_get("/api/v1/integrations/places/stream", stream)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SScapeClient(str(server.make_url("/")).rstrip("/"), "fictional-key", session)
        assert [event async for event in client.async_updates(["place-1"])] == ["ready", "refresh"]


@pytest.mark.parametrize(
    ("status", "error"), [(401, AuthenticationError), (403, PermissionDenied), (429, RateLimited)]
)
@pytest.mark.allow_hosts(["127.0.0.1"])
async def test_stream_auth_and_rate_limit(aiohttp_server, socket_enabled, status, error):
    pytest_socket.socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)

    async def stream(request):
        return web.Response(status=status, headers={"Retry-After": "120"})

    app = web.Application()
    app.router.add_get("/api/v1/integrations/places/stream", stream)
    server = await aiohttp_server(app)
    async with aiohttp.ClientSession() as session:
        client = SScapeClient(str(server.make_url("/")).rstrip("/"), "fictional-key", session)
        with pytest.raises(error) as caught:
            async for _ in client.async_updates(["place-1"]):
                pass
        if status == 429:
            assert caught.value.retry_after == 120


async def test_push_requests_reconciliation_and_stops_cleanly(hass):
    entry = MockConfigEntry(
        domain="sscape", data={"account_id": "account-1"}, options={"places": ["place-1"]}
    )
    client = SScapeClient("https://example.com", "fictional-key", AsyncMock())
    parent = SScapeCoordinator(hass, entry, client)
    reconciled = asyncio.Event()

    async def events(ids):
        assert ids == ["place-1"]
        yield "ready"
        yield "refresh"
        await asyncio.Event().wait()

    count = 0

    async def refresh():
        nonlocal count
        count += 1
        if count == 2:
            reconciled.set()

    with (
        patch.object(client, "async_updates", events),
        patch.object(parent, "async_request_refresh", refresh),
    ):
        task = hass.async_create_background_task(parent._stream_loop(), "test push")
        await asyncio.wait_for(reconciled.wait(), 2)
        assert parent.stream_connected and parent.last_push_at is not None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not parent.stream_connected
