"""Compact snapshots retain uncertainty, source and observation-time semantics."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.sscape.api import ProtocolError, SScapeClient, parse_snapshot


def test_explicit_zero_with_unknown_coverage_is_not_all_clear(payload):
    raw = payload["states"][0]["state"]["briefing"]
    raw.update(
        official_alert_count=0,
        data_quality={"official_warnings": {"state": "unknown", "coverage": "unknown"}},
    )
    place = parse_snapshot(payload).places["place-1"]
    assert place.metric("official_alert_count") == 0
    assert place.signal("official_warning") is None
    raw["official_alert_count"] = 1
    assert place.signal("official_warning") is True


def test_uncertain_arrival_is_a_range_not_a_precise_eta(payload):
    raw = payload["states"][0]["state"]["briefing"]
    raw["time_window"] = {"reliability": "variable", "min_minutes": 15, "max_minutes": 30}
    place = parse_snapshot(payload).places["place-1"]
    assert place.eta_minutes is None
    assert place.arrival_bound("min_minutes") == 15
    assert place.arrival_bound("max_minutes") == 30
    raw["time_window"]["reliability"] = "unreliable"
    assert place.arrival_bound("min_minutes") is None


@pytest.fixture
def reading(payload):
    item = {
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
            "observation_time": datetime.now(UTC).isoformat(),
        },
    }
    payload["states"][0]["state"]["briefing"]["observation_context"] = {"readings": [item]}
    return item


@pytest.mark.parametrize("change", ["old", "suspect", "unit", "missing", "unattributed", "future"])
def test_unusable_station_measurements_do_not_drive_automations(payload, reading, change):
    obs = reading["observation"]
    if change == "old":
        obs["observation_time"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    if change == "future":
        obs["observation_time"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    if change == "suspect":
        obs["quality"]["status"] = "suspect"
    if change == "unit":
        obs["unit"] = "mph"
    if change == "missing":
        obs["value"] = None
    if change == "unattributed":
        obs.pop("attribution")
    assert parse_snapshot(payload).places["place-1"].reading("wind_gust") is None


def test_station_observation_keeps_distance_and_original_time(payload, reading):
    assert parse_snapshot(payload).places["place-1"].reading("wind_gust") == reading


def test_lightning_distance_matches_the_twenty_minute_window(payload):
    raw = payload["states"][0]["state"]["briefing"]
    raw.update(
        data_quality={}, nearest_lightning_distance_km=5, nearest_lightning_distance_20m_km=25
    )
    place = parse_snapshot(payload).places["place-1"]
    assert place.lightning_distance == 25
    raw["nearest_lightning_distance_20m_km"] = None
    assert place.lightning_distance is None


async def test_snapshot_capability_pagination_and_owner_validation(payload):
    client = SScapeClient("https://example.com", "key", AsyncMock())
    catalogue = {
        "contract_version": "home-assistant.v2",
        "account_id": "account-1",
        "places": [],
        "capabilities": ["automation.snapshot.v1"],
    }
    second = deepcopy(payload)
    second["states"][0]["place"]["id"] = "place-2"
    first = dict(
        payload, contract_version="automation.snapshot.v1", has_more=True, next_cursor="place-1"
    )
    second.update(contract_version="automation.snapshot.v1", has_more=False)
    with patch.object(
        client, "_async_get", AsyncMock(side_effect=[catalogue, first, second])
    ) as get:
        await client.async_get_catalog()
        result = await client.async_get_snapshot(["place-1", "place-2"])
        assert set(result.places) == {"place-1", "place-2"}
        assert get.call_args_list[1].args[0].endswith("/snapshot")
        assert get.call_args_list[2].kwargs["params"]["cursor"] == "place-1"
    second["states"][0]["place"]["user_id"] = "another-owner"
    with patch.object(client, "_async_get", AsyncMock(side_effect=[first, second])):
        with pytest.raises(ProtocolError):
            await client.async_get_snapshot(["place-1", "place-2"])


async def test_incremental_snapshot_does_not_rejuvenate_observation_time(payload):
    client = SScapeClient("https://example.com", "key", AsyncMock())
    client._capabilities = {"automation.snapshot.v1"}
    row = payload["states"][0]
    row["current_briefing"] = deepcopy(row["state"]["briefing"])
    observed_at = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
    row["current_briefing"]["data_quality"] = {
        "radar": {"state": "stale", "observation_time": observed_at}
    }
    first = dict(
        payload,
        contract_version="automation.snapshot.v1",
        account_id="account-1",
        has_more=False,
        revision="test-revision",
    )
    now = datetime.now(UTC)
    unchanged = {
        "contract_version": "automation.snapshot.v1",
        "account_id": "account-1",
        "revision": "test-revision",
        "not_modified": True,
        "has_more": False,
        "assessment_times": {
            "place-1": {
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=20)).isoformat(),
            }
        },
    }
    with patch.object(client, "_async_get", AsyncMock(side_effect=[first, unchanged])) as get:
        await client.async_get_snapshot(["place-1"])
        result = await client.async_get_snapshot(["place-1"])
        assert get.call_args.kwargs["params"]["revision"] == "test-revision"
        place = result.places["place-1"]
        assert place.is_fresh()
        assert place.briefing["data_quality"]["radar"]["observation_time"] == observed_at
        assert place.briefing["data_quality"]["radar"]["state"] == "stale"
