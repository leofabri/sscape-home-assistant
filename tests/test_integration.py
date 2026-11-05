"""Use real Home Assistant config flows, coordinator and entity platform."""

from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_API_KEY, CONF_HOST, STATE_UNAVAILABLE
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import (
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    PlaceCatalog,
    RateLimited,
    parse_snapshot,
)
from custom_components.sscape.const import DOMAIN
from custom_components.sscape.diagnostics import async_get_config_entry_diagnostics


@pytest.fixture
def client(payload):
    with patch(
        "custom_components.sscape.api.SScapeClient.async_get_snapshot",
        new=AsyncMock(return_value=parse_snapshot(payload)),
    ) as mock:

        async def catalog():
            if isinstance(mock.side_effect, Exception):
                raise mock.side_effect
            data = mock.return_value
            return PlaceCatalog(
                data.account_id or "account-1",
                {key: value.name for key, value in data.places.items()},
                False,
            )

        with patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog", side_effect=catalog
        ):
            yield mock


@pytest.fixture
def entry(hass):
    config = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        options={"follow_all_places": True, "weather_enabled": False},
        data={
            CONF_HOST: "https://example.com",
            CONF_API_KEY: "fictional-key",
            "account_id": "account-1",
        },
    )
    config.add_to_hass(hass)
    return config


async def test_user_flow(hass, client):
    with patch("custom_components.sscape.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "https://example.com",
                CONF_API_KEY: "fictional-key",
            },
        )
        assert result["step_id"] == "places"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"places": ["place-1"], "follow_all_places": False}
        )
        assert result["step_id"] == "preferences"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "review"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == "create_entry"
        assert result["data"]["account_id"] == "account-1"
        assert result["result"].unique_id == "https://example.com|account-1"


async def test_duplicate_account_not_key(hass, entry, client):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_HOST: "https://example.com",
            CONF_API_KEY: "a-different-fictional-key",
        },
    )
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (AuthenticationError(), "invalid_auth"),
        (CannotConnect(), "cannot_connect"),
        (PermissionDenied(), "access_denied"),
    ],
)
async def test_config_errors(hass, client, error, code):
    client.side_effect = error
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={
            CONF_HOST: "https://example.com",
            CONF_API_KEY: "fictional-key",
        },
    )
    assert result["errors"]["base"] == code


@pytest.mark.parametrize(
    ("host", "key", "code"),
    [
        ("http://example.com", "key", "invalid_host"),
        ("https://example.com", "bad\nkey", "invalid_auth"),
    ],
)
async def test_invalid_config_not_sent(hass, client, host, key, code):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_HOST: host, CONF_API_KEY: key},
    )
    assert result["errors"]["base"] == code
    client.assert_not_called()


async def test_first_link_requires_place_identity(hass, client):
    client.return_value = parse_snapshot({"states": []})
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_USER},
        data={CONF_HOST: "https://example.com", CONF_API_KEY: "key"},
    )
    assert result["errors"]["base"] == "no_places"


async def test_reauth_rejects_different_account_then_preserves_identity(
    hass, entry, client, payload
):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["step_id"] == "reauth_confirm"
    payload["states"][0]["place"]["user_id"] = "account-2"
    client.return_value = parse_snapshot(payload)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_API_KEY: "another-key"},
    )
    assert result["errors"]["base"] == "wrong_account"
    assert entry.data[CONF_API_KEY] == "fictional-key"
    payload["states"][0]["place"]["user_id"] = "account-1"
    client.return_value = parse_snapshot(payload)
    with patch("custom_components.sscape.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_API_KEY: "replacement-key"},
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "replacement-key"
    assert entry.unique_id == "https://example.com|account-1"


def state_for(hass, entry, key):
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}|place-1|{key}")
    assert entity_id is not None
    return hass.states.get(entity_id)


async def test_real_setup_refresh_failure_recovery_unload(hass, entry, client, payload):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    risk = state_for(hass, entry, "risk")
    assert risk.state == "moderate"
    assert risk.attributes["recommended_action"].startswith("Check outdoor")
    assert state_for(hass, entry, "eta").state == "18"
    assert client.call_count == 1  # one shared request, not one per sensor

    client.side_effect = CannotConnect()
    await entry.runtime_data.async_refresh()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE

    client.side_effect = None
    new = deepcopy(payload)
    new["states"][0]["state"]["briefing"]["risk_level"] = "high"
    client.return_value = parse_snapshot(new)
    await entry.runtime_data.async_refresh()
    assert state_for(hass, entry, "risk").state == "high"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE


async def test_expired_state_missing_eta_and_deleted_place(hass, entry, client, payload):
    expired = payload["states"][0]["state"]["briefing"]
    expired["expires_at"] = (dt_util.utcnow() - timedelta(seconds=1)).isoformat()
    expired.pop("approach_eta_minutes")
    client.return_value = parse_snapshot(payload)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE
    assert state_for(hass, entry, "eta").state == STATE_UNAVAILABLE
    client.return_value = parse_snapshot({"states": []})
    await entry.runtime_data.async_refresh()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE


async def test_options_selection_interval_and_reload(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    with patch(
        "homeassistant.config_entries.ConfigEntries.async_reload", return_value=True
    ) as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "saved_places"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "places": ["place-1"],
                "follow_all_places": False,
            },
        )
        second = await hass.config_entries.options.async_init(entry.entry_id)
        second = await hass.config_entries.options.async_configure(
            second["flow_id"], {"next_step_id": "advanced"}
        )
        result = await hass.config_entries.options.async_configure(
            second["flow_id"], {"update_interval": 180, "push_updates": True}
        )
        await hass.async_block_till_done()
    assert result["type"] == "create_entry"
    assert entry.options == {
        "places": ["place-1"],
        "update_interval": 180,
        "follow_all_places": False,
        "weather_enabled": False,
        "push_updates": True,
        "coordinate_only": False,
    }
    assert reload.await_count == 2  # Two independent saves, one reload each.
    assert all(call.args == (entry.entry_id,) for call in reload.await_args_list)


async def test_dynamic_place_discovery_and_rename(hass, entry, client, payload):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    second = deepcopy(payload["states"][0])
    second["place"]["id"] = "place-2"
    second["place"]["name"] = "Second test place"
    payload["states"].append(second)
    payload["states"][0]["place"]["name"] = "Renamed test place"
    client.return_value = parse_snapshot(payload)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}|place-2|risk")
    assert hass.states.get(entity_id).state == "moderate"
    first = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.unique_id}|place-1|risk")
    device = dr.async_get(hass).async_get(registry.async_get(first).device_id)
    assert device.name == "Renamed test place"


async def test_only_selected_places_created(hass, entry, client):
    hass.config_entries.async_update_entry(entry, options={"places": ["different-place"]})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not er.async_get(hass).entities


@pytest.mark.parametrize("error", [AuthenticationError(), PermissionDenied()])
async def test_auth_failure_starts_reauth_not_safe_state(hass, entry, client, error):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client.side_effect = error
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE
    assert any(
        flow["context"]["source"] == SOURCE_REAUTH
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_account_change_stops_updates(hass, entry, client, payload):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    payload["states"][0]["place"]["user_id"] = "account-2"
    client.return_value = parse_snapshot(payload)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert state_for(hass, entry, "risk").state == STATE_UNAVAILABLE
    assert entry.runtime_data.data.account_id == "account-1"  # foreign response was not stored


async def test_backoff_and_rate_limit(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client.side_effect = CannotConnect()
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.update_interval.total_seconds() == 240
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.update_interval.total_seconds() == 480
    client.side_effect = RateLimited(300)
    with pytest.raises(UpdateFailed) as caught:
        await entry.runtime_data._async_update_data()
    assert caught.value.retry_after == 300
    client.side_effect = None
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.update_interval.total_seconds() == 120


async def test_diagnostics_are_private(hass, entry, client):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    text = str(diagnostics)
    for forbidden in ("fictional-key", "account-1", "place-1", "Test place", "example.com"):
        assert forbidden not in text
    assert diagnostics["place_count"] == diagnostics["fresh_place_count"] == 1


async def test_diagnostics_before_setup_and_after_unload(hass, entry, client):
    value = await async_get_config_entry_diagnostics(hass, entry)
    assert not value["runtime_available"]
    assert value["config_entry_state"] == "not_loaded"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client.side_effect = PermissionDenied()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    value = await async_get_config_entry_diagnostics(hass, entry)
    assert value["failure_reason"] == "access_denied"
    assert value["reauthentication_required"]
    client.side_effect = CannotConnect()
    await entry.runtime_data.async_refresh()
    assert (await async_get_config_entry_diagnostics(hass, entry))[
        "failure_reason"
    ] == "cannot_connect"
    client.side_effect = None
    await entry.runtime_data.async_refresh()
    assert (await async_get_config_entry_diagnostics(hass, entry))["failure_reason"] is None
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not (await async_get_config_entry_diagnostics(hass, entry))["runtime_available"]
