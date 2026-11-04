"""Contract, transport and confidentiality checks without a live sScape account."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientConnectionError

from custom_components.sscape.api import (
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    ProtocolError,
    RateLimited,
    SScapeClient,
    _retry_after,
    normalize_host,
    parse_snapshot,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://sscape.leofabri.com",
        "https://user:pass@example.com",
        "https://example.com/?key=abc",
        "https://example.com/#key",
        "https://example.com/api/v1",
        "file:///tmp/example",
        "https://",
        "https://example.com:99999",
        "http://169.254.169.254",
        "http://0.0.0.0",
    ],
)
def test_reject_unsafe_hosts(url):
    with pytest.raises(ValueError):
        normalize_host(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://sscape.leofabri.com/", "https://sscape.leofabri.com"),
        ("http://192.168.50.20:8081", "http://192.168.50.20:8081"),
        ("http://homeassistant.local:8081", "http://homeassistant.local:8081"),
        ("https://EXAMPLE.COM/", "https://example.com"),
        ("http://127.0.0.1:8081", "http://127.0.0.1:8081"),
    ],
)
def test_normalize_hosts(url, expected):
    assert normalize_host(url) == expected


def test_account_and_freshness(payload):
    data = parse_snapshot(payload)
    assert data.account_id == "account-1"
    place = data.places["place-1"]
    assert place.is_fresh()
    assert place.risk_level == "moderate"
    assert place.eta_minutes == 18
    assert place.updated_at.tzinfo is not None


@pytest.mark.parametrize("change", ["expired", "future", "missing_time", "missing_state"])
def test_missing_or_stale_never_becomes_safe(payload, change):
    envelope = payload["states"][0]
    briefing = envelope["state"]["briefing"]
    if change == "expired":
        briefing["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    elif change == "future":
        briefing["updated_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    elif change == "missing_time":
        briefing.pop("updated_at")
    else:
        envelope["live_state_available"] = False
        envelope.pop("state")
    assert not parse_snapshot(payload).places["place-1"].is_fresh()


@pytest.mark.parametrize("value", [None, -1, True, "18", float("nan")])
def test_invalid_eta_is_not_zero(payload, value):
    payload["states"][0]["state"]["briefing"]["approach_eta_minutes"] = value
    assert parse_snapshot(payload).places["place-1"].eta_minutes is None


def test_no_cross_account_or_duplicate_place(payload):
    other = {"place": {"id": "place-2", "user_id": "account-2", "name": "Other"}}
    with pytest.raises(ProtocolError):
        parse_snapshot({"states": [payload["states"][0], other]})
    with pytest.raises(ProtocolError):
        parse_snapshot({"states": payload["states"] * 2})


@pytest.mark.parametrize("body", [{}, {"states": None}, {"states": [None]}, {"states": [{}]}])
def test_malformed_payload(body):
    with pytest.raises(ProtocolError):
        parse_snapshot(body)


def mock_session(status, payload=None, headers=None):
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    response.content.read = AsyncMock(side_effect=[__import__("json").dumps(payload).encode(), b""])
    context = AsyncMock()
    context.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = context
    return session


async def test_client_one_request_no_redirects(payload):
    session = mock_session(200, payload)
    client = SScapeClient("https://example.com", "fictional-key", session, "it")
    snapshot = await client.async_get_snapshot()
    assert len(snapshot.places) == 1
    session.get.assert_called_once()
    args, kwargs = session.get.call_args
    assert args[0] == "https://example.com/api/v1/integrations/places/states"
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"]["X-API-Key"] == "fictional-key"
    assert kwargs["headers"]["Accept-Language"] == "it"


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, AuthenticationError),
        (403, PermissionDenied),
        (302, CannotConnect),
        (500, CannotConnect),
        (429, RateLimited),
    ],
)
async def test_safe_http_errors(status, exception):
    session = mock_session(status, {"error": "Do not echo private server content"})
    with pytest.raises(exception) as caught:
        await SScapeClient("https://example.com", "fictional-key", session).async_get_snapshot()
    assert "fictional-key" not in str(caught.value)
    assert "private server" not in str(caught.value)


async def test_retry_after():
    with pytest.raises(RateLimited) as caught:
        await SScapeClient(
            "https://example.com", "key", mock_session(429, headers={"Retry-After": "240"})
        ).async_get_snapshot()
    assert caught.value.retry_after == 240


@pytest.mark.parametrize("value", [None, "invalid", "-1", "nan", "9999999"])
def test_retry_after_is_bounded(value):
    assert 60 <= _retry_after(value) <= 86400


def test_retry_after_http_date():
    assert 200 < _retry_after(format_datetime(datetime.now(UTC) + timedelta(minutes=4))) <= 240


@pytest.mark.parametrize("exception", [TimeoutError(), ClientConnectionError("private host info")])
async def test_transport_failure_is_safe(exception):
    session = mock_session(200)
    session.get.side_effect = exception
    with pytest.raises(CannotConnect) as caught:
        await SScapeClient("https://example.com", "key", session).async_get_snapshot()
    assert "private host" not in str(caught.value)


@pytest.mark.parametrize("raw", [b"not-json", b"\xff", b"x" * (2 * 1024 * 1024 + 1)])
async def test_invalid_or_oversized_response(raw):
    session = mock_session(200)
    session.get.return_value.__aenter__.return_value.content.read.side_effect = [raw, b""]
    with pytest.raises(ProtocolError):
        await SScapeClient("https://example.com", "key", session).async_get_snapshot()


def test_current_briefing_not_older_persisted_state(payload):
    row = payload["states"][0]
    row["current_briefing"] = dict(row["state"]["briefing"], risk_level="high")
    assert parse_snapshot(payload).places["place-1"].risk_level == "high"


def test_new_place_current_briefing_without_persisted_transition(payload):
    row = payload["states"][0]
    row["current_briefing"] = row.pop("state")["briefing"]
    row["live_state_available"] = False
    place = parse_snapshot(payload).places["place-1"]
    assert place.is_fresh()
    assert place.risk_level == "moderate"


@pytest.mark.parametrize("value", [None, "", "unknown", True])
def test_unrecognized_risk_not_low(payload, value):
    payload["states"][0]["state"]["briefing"]["risk_level"] = value
    assert parse_snapshot(payload).places["place-1"].risk_level is None


@pytest.mark.parametrize("value", ["bad-time", "2026-11-04T12:00:00", 123])
def test_bad_timestamps_unavailable(payload, value):
    payload["states"][0]["state"]["briefing"]["updated_at"] = value
    assert not parse_snapshot(payload).places["place-1"].is_fresh()


@pytest.mark.parametrize(
    "body",
    [
        {"states": [{}] * 1001},
        {"states": [{"place": {"id": "bad|id", "name": "Test", "user_id": "account"}}]},
    ],
)
def test_unbounded_and_ambiguous_identity_rejected(body):
    with pytest.raises(ProtocolError):
        parse_snapshot(body)
