"""Two real HA WebSocket users on one instance; no network or real phone data."""

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.mobile_location import (
    DATA,
    MobileLinkError,
    MobileLinks,
    _gps_fix,
    mobile_report_signal,
    setup_mobile_location,
)


async def test_two_home_assistant_users_get_only_their_phone_and_alert(hass, hass_ws_client):
    async def user(name):
        account = await hass.auth.async_create_user(name, group_ids=[GROUP_ID_USER])
        refresh = await hass.auth.async_create_refresh_token(account, "http://example.test")
        access = hass.auth.async_create_access_token(refresh)
        return account, await hass_ws_client(hass, access_token=access)

    owner_a, client_a = await user("Fictional A")
    owner_b, client_b = await user("Fictional B")
    phone_a = MockConfigEntry(domain="mobile_app", title="Phone A", data={"user_id": owner_a.id})
    phone_b = MockConfigEntry(domain="mobile_app", title="Phone B", data={"user_id": owner_b.id})
    for phone in (phone_a, phone_b):
        phone.add_to_hass(hass)
    await setup_mobile_location(hass)
    manager = hass.data[DATA]
    messages = []
    registry = er.async_get(hass)
    for unique, phone in (("a", phone_a), ("b", phone_b)):
        registry.async_get_or_create("notify", "mobile_app", unique, config_entry=phone)
    hass.services.async_register("notify", "send_message", lambda call: messages.append(call.data))

    async def command(client, name, **args):
        await client.send_json_auto_id({"type": f"sscape/mobile_location/{name}", **args})
        return await client.receive_json()

    calls = []
    code_a, code_b = "A" * 43, "B" * 43

    async def fake_request(method, path, *, payload=None, token=None):
        calls.append((method, path, payload, token))
        if path == "link" and method == "POST":
            return {
                "token": ("X" if payload["code"] == code_a else "Y") * 43,
                "use_in_home_assistant": True,
                "use_in_sscape": True,
            }
        if path == "link":
            return {}
        now = dt_util.utcnow()
        expires = (now + timedelta(minutes=3)).isoformat()
        return {
            "briefing": {
                "headline": "Forecast A" if token == "X" * 43 else "Forecast B",
                "observed_at": now.isoformat(),
                "expires_at": expires,
                "latitude": 999,
                "recommended_action": "Check sScape",
            },
            "notice": {"key": "storm:example", "expires_at": expires},
        }

    with (
        patch.object(manager, "request", side_effect=fake_request),
        patch.object(manager.store, "async_save", AsyncMock()),
    ):
        options_a = (await command(client_a, "status"))["result"]["choices"]
        assert [entry["id"] for entry in options_a] == [phone_a.entry_id]
        assert not (await command(client_a, "link", device=phone_b.entry_id, code=code_a))[
            "success"
        ]
        assert not calls
        assert (await command(client_a, "link", device=phone_a.entry_id, code=code_a))["success"]
        assert (await command(client_b, "link", device=phone_b.entry_id, code=code_b))["success"]
        assert not (await command(client_a, "status", user_id=owner_b.id))["success"]
        async_dispatcher_send(
            hass,
            mobile_report_signal(phone_a.entry_id),
            {"gps": [41, 10], "gps_accuracy": 40},
        )
        await hass.async_block_till_done()
        result_a = (await command(client_a, "status"))["result"]["briefing"]
        assert result_a["headline"] == "Forecast A" and "latitude" not in result_a
        assert (await command(client_b, "status"))["result"]["briefing"] is None
        assert len(messages) == 1 and messages[0]["title"] == "Forecast A"
        assert messages[0]["entity_id"] == registry.async_get_entity_id("notify", "mobile_app", "a")
        assert not hass.states.async_all("sscape")  # no shared/recorded GPS entities
        assert (await command(client_a, "unlink"))["success"]
        assert (await command(client_b, "status"))["result"]["linked"]
        assert calls[-1] == ("DELETE", "link", None, "X" * 43)
        assert (await command(client_b, "unlink"))["success"]


def test_only_real_finite_gps_reports_are_eligible():
    now = dt_util.utcnow()
    assert _gps_fix({"location_name": "home"}, now) is None
    assert _gps_fix({"gps": [float("nan"), 13], "gps_accuracy": 12}, now) is None
    assert _gps_fix({"gps": [45, 13], "gps_accuracy": 700}, now) is None
    assert _gps_fix({"gps": [0, 0], "gps_accuracy": 15}, now)["observed_at"] == now.isoformat()


async def test_disconnect_failure_persists_pause_before_contacting_cloud(hass):
    import pytest

    manager = MobileLinks(hass)
    manager.links = {
        "owner-a": {"device": "device-a", "token": "A" * 43},
        "owner-b": {"device": "device-b", "token": "B" * 43},
    }
    saved = AsyncMock()

    async def unavailable(*args, **kwargs):
        assert saved.call_args.args[0]["links"]["owner-a"]["paused"] is True
        raise MobileLinkError("server_unavailable")

    with (
        patch.object(manager.store, "async_save", saved),
        patch.object(manager, "request", side_effect=unavailable),
        pytest.raises(MobileLinkError),
    ):
        await manager.unlink("owner-a")
    assert manager.links["owner-a"]["paused"] and not manager.links["owner-b"].get("paused")
