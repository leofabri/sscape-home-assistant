"""Coordinate targets use the authorized geographic API, not saved-place writes."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.sscape.api import PermissionDenied, ProtocolError, SScapeClient


@pytest.fixture
def point():
    return {
        "id": "point-fictional",
        "name": "Local target",
        "latitude": 45.1,
        "longitude": 9.2,
        "radius_km": 25,
        "enabled": True,
    }


@pytest.fixture
def geo_body(point):
    now = datetime.now(UTC)
    return {
        "briefing": {
            "place": {
                "user_id": "account-1",
                "latitude": point["latitude"],
                "longitude": point["longitude"],
            },
            "risk_level": "low",
            "updated_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=20)).isoformat(),
        },
        "forecast": {"kind": "model_forecast"},
    }


async def test_coordinate_api_preserves_identity_and_query(point, geo_body):
    client = SScapeClient("https://example.com", "fictional-key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock(return_value=geo_body)) as request:
        place = await client.async_get_point(point, "account-1")
        assert place.place_id == point["id"] and place.name == point["name"]
        assert place.is_fresh()
        assert request.call_args.args == ("/api/v1/integrations/briefing",)
        assert request.call_args.kwargs["params"] == {
            "latitude": "45.1",
            "longitude": "9.2",
            "radius_km": "25",
            "label": "Local target",
        }
        forecast = await client.async_get_point_weather(point, "account-1")
        assert forecast == geo_body["forecast"]
        assert request.call_args.kwargs["params"]["include_forecast"] == "true"


async def test_restricted_key_never_calls_coordinates(point):
    client = SScapeClient("https://example.com", "fictional-key", AsyncMock())
    client.places_restricted = True
    with patch.object(client, "_async_get", AsyncMock()) as request:
        with pytest.raises(PermissionDenied):
            await client.async_get_point(point, "account-1")
        request.assert_not_called()


@pytest.mark.parametrize("change", ["owner", "latitude", "longitude"])
async def test_coordinate_response_must_match_owner_and_target(point, geo_body, change):
    geo_body["briefing"]["place"]["user_id" if change == "owner" else change] = (
        "other-account" if change == "owner" else 0
    )
    client = SScapeClient("https://example.com", "fictional-key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock(return_value=geo_body)):
        with pytest.raises(ProtocolError):
            await client.async_get_point(point, "account-1")


@pytest.mark.parametrize(
    "field,value",
    [("latitude", 91), ("longitude", float("nan")), ("radius_km", 0), ("latitude", True)],
)
async def test_invalid_coordinate_never_sent(point, field, value):
    point[field] = value
    client = SScapeClient("https://example.com", "fictional-key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock()) as request:
        with pytest.raises(ValueError):
            await client.async_get_point(point, "account-1")
        request.assert_not_called()
