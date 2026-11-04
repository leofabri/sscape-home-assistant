"""Authenticated outbound SSE; payloads only request a REST reconciliation."""

from aiohttp import ClientError, ClientTimeout

from .api import (
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    ProtocolError,
    RateLimited,
    _retry_after,
)


async def updates(client, place_ids):
    params = {"place_ids": ",".join(place_ids)} if place_ids is not None else None
    try:
        async with client._session.get(
            f"{client.host}/api/v1/integrations/places/stream",
            params=params,
            headers={"X-API-Key": client._api_key, "Accept": "text/event-stream"},
            timeout=ClientTimeout(total=100, sock_read=35),
            allow_redirects=False,
        ) as response:
            if response.status == 401:
                raise AuthenticationError("API key rejected")
            if response.status == 403:
                raise PermissionDenied("Place stream permission denied")
            if response.status == 429:
                raise RateLimited(_retry_after(response.headers.get("Retry-After")))
            if response.status == 204:
                yield "empty"
                return
            if response.status != 200 or response.content_type != "text/event-stream":
                raise CannotConnect("Place stream is unavailable")
            event = ""
            async for raw in response.content:
                if len(raw) > 16384:
                    raise ProtocolError("Oversized stream message")
                line = raw.decode("utf-8").strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif not line:
                    if event in ("ready", "refresh"):
                        yield event
                    event = ""
    except (ClientError, TimeoutError, UnicodeError, ValueError):
        raise CannotConnect("Place stream disconnected") from None
