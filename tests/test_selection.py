"""Opt-in place selection and server-side data minimization."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_API_KEY, CONF_HOST

from custom_components.sscape.api import (
    PlaceCatalog,
    ProtocolError,
    ServerUpgradeRequired,
    SScapeClient,
)


@pytest.fixture
def catalog():
    return PlaceCatalog("account-1", {"place-1": "Home", "place-2": "Garden"}, True)


async def test_new_setup_requires_an_explicit_place_choice(hass, catalog):
    with (
        patch.object(SScapeClient, "async_get_catalog", AsyncMock(return_value=catalog)),
        patch.object(SScapeClient, "async_get_snapshot", AsyncMock()) as snapshots,
        patch("custom_components.sscape.async_setup_entry", return_value=True),
    ):
        flow = await hass.config_entries.flow.async_init(
            "sscape",
            context={"source": SOURCE_USER},
            data={
                CONF_HOST: "https://example.com",
                CONF_API_KEY: "fictional-key",
            },
        )
        assert flow["step_id"] == "places"
        invalid = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                "places": [],
                "follow_all_places": False,
            },
        )
        assert invalid["errors"]["base"] == "select_places"
        done = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                "places": ["place-1"],
                "follow_all_places": False,
            },
        )
        assert done["type"] == "create_entry"
        assert done["options"]["places"] == ["place-1"]
        assert done["options"]["follow_all_places"] is False
        snapshots.assert_not_called()  # no unselected assessment/coordinates downloaded in setup


async def test_selected_snapshot_requests_are_filtered_and_fail_closed(payload):
    client = SScapeClient("https://example.com", "key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock(return_value=payload)) as get:
        await client.async_get_snapshot(["place-1"])
        assert get.call_args.kwargs["params"] == {"place_ids": "place-1"}
        with pytest.raises(ProtocolError):
            await client.async_get_snapshot(["different-place"])


async def test_empty_selection_does_not_fetch_everything():
    client = SScapeClient("https://example.com", "key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock()) as get:
        result = await client.async_get_snapshot([])
        assert result.places == {}
        get.assert_not_called()


@pytest.mark.parametrize("place_id", ["bad,id", "bad?query", "bad#fragment", "bad id", "x" * 257])
async def test_catalogue_rejects_ambiguous_or_oversized_identifiers(place_id):
    client = SScapeClient("https://example.com", "key", AsyncMock())
    body = {
        "contract_version": "home-assistant.v2",
        "account_id": "account-1",
        "places": [{"id": place_id, "name": "Home"}],
    }
    with patch.object(client, "_async_get", AsyncMock(return_value=body)):
        with pytest.raises(ProtocolError):
            await client.async_get_catalog()


async def test_catalogue_requires_updated_server():
    client = SScapeClient("https://example.com", "key", AsyncMock())
    with patch.object(client, "_async_get", AsyncMock(return_value={"places": []})):
        with pytest.raises(ServerUpgradeRequired):
            await client.async_get_catalog()
