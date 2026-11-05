"""Native flows, local-only instruments and scoped dashboard setup."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import (
    AccountSnapshot,
    PermissionDenied,
    PlaceCatalog,
    PlaceSnapshot,
    RateLimited,
    parse_snapshot,
)


@pytest.fixture
def config(hass):
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "weather_enabled": False, "update_interval": 120},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def provider(payload):
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Test place"}, False)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(side_effect=lambda _: parse_snapshot(payload)),
        ) as snapshot,
    ):
        yield snapshot


async def test_coordinate_first_setup_never_calls_places_catalog(hass, payload):
    async def geo(client, point, owner=None):
        client.account_id = "account-1"
        return PlaceSnapshot(
            point["id"], point["name"], payload["states"][0]["state"]["briefing"], True
        )

    with (
        patch("custom_components.sscape.api.SScapeClient.async_get_point", geo),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog", AsyncMock()
        ) as catalog,
        patch("custom_components.sscape.async_setup_entry", AsyncMock(return_value=True)),
    ):
        result = await hass.config_entries.flow.async_init(
            "sscape",
            context={"source": SOURCE_USER},
            data={
                "host": "https://example.com",
                "api_key": "fictional-key",
                "use_coordinates": True,
            },
        )
        assert result["step_id"] == "coordinate_target"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "name": "Local target",
                "latitude": 45,
                "longitude": 9,
                "radius_km": 25,
                "enabled": True,
            },
        )
        assert result["step_id"] == "preferences"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] == "create_entry" and result["options"]["coordinate_only"]
        assert result["options"]["places"] == []
        catalog.assert_not_called()


async def test_point_runtime_cache_and_scope_failure(hass, config, provider, payload):
    point = {
        "id": "point-local",
        "name": "Local target",
        "latitude": 45,
        "longitude": 9,
        "radius_km": 25,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        config,
        options=dict(config.options)
        | {"coordinate_only": True, "follow_all_places": True, "coordinate_targets": [point]},
    )
    provider.side_effect = None
    provider.return_value = AccountSnapshot("account-1", {})
    with patch(
        "custom_components.sscape.api.SScapeClient.async_get_point",
        AsyncMock(
            return_value=PlaceSnapshot(
                point["id"], point["name"], payload["states"][0]["state"]["briefing"], True
            )
        ),
    ) as geo:
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        assert provider.call_args.args == ([],)
        assert set(config.runtime_data.data.places) == {"point-local"}
        await config.runtime_data.async_refresh()
        assert geo.await_count == 1
        config.runtime_data._point_cache.clear()
        geo.side_effect = PermissionDenied()
        await config.runtime_data.async_refresh()
        assert not config.runtime_data.last_update_success
        assert config.runtime_data.failure_reason == "access_denied"


async def test_local_sensor_survives_cloud_outage_no_upload_and_unloads(hass, config, provider):
    hass.states.async_set("sensor.outside", "32", {"unit_of_measurement": "°F"})
    hass.config_entries.async_update_entry(
        config,
        options=dict(config.options)
        | {"local_sensors": {"place-1": {"temperature": "sensor.outside"}}},
    )
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity = registry.async_get_entity_id(
        "sensor", "sscape", config.unique_id + "|place-1|local_temperature"
    )
    assert hass.states.get(entity).state == "0.0"
    config.runtime_data.async_set_update_error(Exception("offline"))
    hass.states.async_set("sensor.outside", "50", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    state = hass.states.get(entity)
    assert state.state == "10.0" and state.attributes["uploaded"] is False
    assert state.attributes["observation_time"] is None
    hass.states.async_set("sensor.outside", "unknown", {"unit_of_measurement": "°F"})
    await hass.async_block_till_done()
    assert hass.states.get(entity).state == "unknown"
    assert await hass.config_entries.async_unload(config.entry_id)


async def test_coordinate_rate_limit_preserves_server_retry_after(hass, config, provider, payload):
    point = {
        "id": "point-limit",
        "name": "Local target",
        "latitude": 45,
        "longitude": 9,
        "radius_km": 25,
        "enabled": True,
    }
    hass.config_entries.async_update_entry(
        config, options=dict(config.options) | {"coordinate_targets": [point]}
    )
    with patch(
        "custom_components.sscape.api.SScapeClient.async_get_point",
        AsyncMock(
            return_value=PlaceSnapshot(
                point["id"], point["name"], payload["states"][0]["state"]["briefing"], True
            )
        ),
    ) as geo:
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        parent = config.runtime_data
        parent._point_cache.clear()
        geo.side_effect = RateLimited(900)
        from homeassistant.helpers.update_coordinator import UpdateFailed

        with pytest.raises(UpdateFailed) as raised:
            await parent._async_update_data()
        assert raised.value.retry_after == 900
        assert parent.failure_reason == "rate_limited" and not parent._point_cache


@pytest.mark.parametrize("step", ["coordinate", "local_sensors", "notifications", "assist"])
async def test_advanced_options_preserve_other_choices(hass, config, provider, step):
    hass.config_entries.async_update_entry(
        config, options=dict(config.options) | {"custom_preserved": True}
    )
    flow = await hass.config_entries.options.async_init(config.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"next_step_id": step},
    )
    assert result["step_id"] == step
    if step == "coordinate":
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"target": "new"}
        )
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "name": "Mountain",
                "latitude": 46,
                "longitude": 10,
                "radius_km": 25,
                "enabled": False,
            },
        )
        assert not result["data"]["coordinate_targets"][0]["enabled"]
    elif step == "local_sensors":
        hass.states.async_set("sensor.outside", "12", {"unit_of_measurement": "°C"})
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"target": "place-1"}
        )
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"temperature": "sensor.outside", "confirm_outdoor": False}
        )
        assert result["errors"]["base"] == "outdoor_required"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"temperature": "sensor.outside", "confirm_outdoor": True}
        )
        assert result["data"]["local_sensors"]["place-1"]["temperature"] == "sensor.outside"
    elif step == "notifications":
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "episode_notifications": True,
                "notification_target": "",
                "episode_kinds": ["storm"],
            },
        )
        assert result["errors"]["base"] == "invalid_notify_service"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"episode_notifications": False, "notification_target": "", "episode_kinds": ["storm"]},
        )
    else:
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "assist_enabled": True,
                "assist_allow_anonymous": False,
                "configure_aliases": True,
            },
        )
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"target": "place-1"}
        )
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"place_aliases": ["home", "garden"]}
        )
        assert result["data"]["assist_aliases"] == {"place-1": ["home", "garden"]}
    assert result["type"] == "create_entry" and result["data"]["custom_preserved"] is True


async def test_setup_preview_scoped_no_writes_and_respects_user_disabled(hass, config, provider):
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    user = await hass.auth.async_create_user("Admin", group_ids=["system-admin"])
    from homeassistant.core import Context

    ctx = Context(user_id=user.id)
    response = await hass.services.async_call(
        "sscape", "get_setup", {}, blocking=True, return_response=True, context=ctx
    )
    assert len(response["targets"]) == 1 and not response["writes_performed"]
    target = response["targets"][0]
    assert target["entities"]["official_warning_details"]["disabled_by"] == "integration"
    before = {
        item.id for item in dr.async_entries_for_config_entry(dr.async_get(hass), config.entry_id)
    }
    registry = er.async_get(hass)
    warning = target["entities"]["official_warning_details"]["entity_id"]
    registry.async_update_entity(warning, disabled_by=er.RegistryEntryDisabler.USER)
    preview = await hass.services.async_call(
        "sscape",
        "get_dashboard",
        {"device_id": target["device_id"], "include_optional": True},
        blocking=True,
        return_response=True,
        context=ctx,
    )
    assert not preview["writes_performed"] and warning not in str(preview["config"])
    assert {
        item.id for item in dr.async_entries_for_config_entry(dr.async_get(hass), config.entry_id)
    } == before
    assert registry.async_get(warning).disabled_by == er.RegistryEntryDisabler.USER


async def test_detailed_entities_and_hazards_action_current_only(hass, config, provider, payload):
    now = datetime.now(UTC)
    b = payload["states"][0]["state"]["briefing"]
    b.update(
        official_alert_count=1,
        official_alert_id="test-warning",
        official_alert_source="test-authority",
        official_alert_headline="Wind warning",
        official_alert_covers_point=False,
        official_alert_expires=(now + timedelta(hours=2)).isoformat(),
        recent_earthquakes_24h=[
            {
                "id": "test-quake",
                "magnitude": 4.1,
                "distance_km": 20,
                "depth_km": 8,
                "occurred_at": now.isoformat(),
                "area": "Test area",
            }
        ],
        environmental_signals=[
            {
                "id": "test-thermal",
                "kind": "fire",
                "source": "test-satellite",
                "observed_at": now.isoformat(),
                "distance_km": 12,
                "frp_mw": 5,
            }
        ],
    )
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    expected = {
        "official_warning_details": "Wind warning",
        "latest_earthquake_magnitude": "4.1",
        "latest_earthquake_distance": "20",
        "nearest_thermal_detection": "12",
    }
    keys = [*expected, "official_warning_expires", "latest_earthquake_time"]
    ids = {
        key: registry.async_get_entity_id("sensor", "sscape", config.unique_id + "|place-1|" + key)
        for key in keys
    }
    for entity in ids.values():
        registry.async_update_entity(entity, disabled_by=None)
    assert await hass.config_entries.async_reload(config.entry_id)
    await hass.async_block_till_done()
    for key, value in expected.items():
        assert hass.states.get(ids[key]).state == value
    assert hass.states.get(ids["official_warning_details"]).attributes["covers_point"] is False
    assert (
        hass.states.get(ids["official_warning_details"]).attributes["scope"]
        == "warning_area_intersects_monitored_radius"
    )
    result = await hass.services.async_call(
        "sscape",
        "get_hazards",
        {"device_id": registry.async_get(ids["official_warning_details"]).device_id},
        blocking=True,
        return_response=True,
    )
    assert result["available"] and result["earthquakes"][0]["area"] == "Test area"
    assert not result["thermal_detections"][0]["confirmed_fire"]
    b["expires_at"] = (now - timedelta(seconds=1)).isoformat()
    config.runtime_data.async_set_updated_data(parse_snapshot(payload))
    await hass.async_block_till_done()
    for entity in ids.values():
        assert hass.states.get(entity).state == "unavailable"


async def test_winter_entities_use_complete_forecast_windows(hass, config, provider):
    now = datetime.now(UTC)
    start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    raw = {
        "kind": "model_forecast",
        "state": "available",
        "fetched_at": now.isoformat(),
        "sources": ["test-model"],
        "source_details": [
            {
                "id": "test-model",
                "attribution": "Fictional model data",
                "license_url": "https://example.com/license",
            }
        ],
        "start": start.isoformat(),
        "hours": [
            {
                "time": (start + timedelta(hours=i + 1)).isoformat(),
                "period_start": (start + timedelta(hours=i)).isoformat(),
                "temperature_c": -2,
                "snowfall_cm": 0.5,
                "visibility_m": 500 if i == 3 else 1000,
            }
            for i in range(24)
        ],
    }
    hass.config_entries.async_update_entry(
        config, options=dict(config.options) | {"weather_enabled": True}
    )
    with patch(
        "custom_components.sscape.api.SScapeClient.async_get_weather", AsyncMock(return_value=raw)
    ):
        assert await hass.config_entries.async_setup(config.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)
        ids = [
            registry.async_get_entity_id(
                "sensor", "sscape", config.unique_id + "|place-1|forecast_" + key
            )
            for key in ("snowfall_cm", "visibility_minimum_m")
        ]
        for entity in ids:
            registry.async_update_entity(entity, disabled_by=None)
        assert await hass.config_entries.async_reload(config.entry_id)
        await hass.async_block_till_done()
        assert float(hass.states.get(ids[0]).state) == 12
        assert float(hass.states.get(ids[1]).state) == 500
        from homeassistant.helpers.update_coordinator import UpdateFailed

        from custom_components.sscape.presentation import target_summary

        config.runtime_data.async_set_update_error(UpdateFailed("Synthetic assessment outage"))
        await hass.async_block_till_done()
        assert float(hass.states.get(ids[0]).state) == 12
        risk = registry.async_get_entity_id("sensor", "sscape", config.unique_id + "|place-1|risk")
        assert hass.states.get(risk).state == "unavailable"
        parent = config.runtime_data
        assert target_summary(hass, parent, parent.data.places["place-1"])["forecast_available"]
        config.runtime_data.failure_reason = "authentication_failed"
        config.runtime_data.async_update_listeners()
        await hass.async_block_till_done()
        assert hass.states.get(ids[0]).state == "unavailable"
        assert not target_summary(hass, parent, parent.data.places["place-1"])["forecast_available"]
        await config.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(ids[0]).attributes["data_kind"] == "model_forecast"
        raw["hours"][0].pop("snowfall_cm")
        raw["hours"][0].pop("visibility_m")
        await config.runtime_data.weather_coordinators["place-1"].async_refresh()
        await hass.async_block_till_done()
        assert all(hass.states.get(entity).state == "unknown" for entity in ids)
        assert all(hass.states.get(entity).attributes["data_status"] == "missing" for entity in ids)


async def test_dashboard_has_three_clear_sections_and_opt_in_details(hass, config, provider):
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    risk = registry.async_get_entity_id("sensor", "sscape", config.unique_id + "|place-1|risk")
    device = registry.async_get(risk).device_id
    essential = await hass.services.async_call(
        "sscape",
        "get_dashboard",
        {"device_id": device, "profile": "essential", "include_optional": True},
        blocking=True,
        return_response=True,
    )
    cards = essential["config"]["views"][0]["cards"]
    assert [c["heading"] for c in cards if c["type"] == "heading"][:3] == [
        "Now",
        "Next hours",
        "Data and coverage",
    ]
    assert "latest_earthquake" not in str(cards)
    from homeassistant.helpers.template import Template

    summary = next(card for card in cards if card["type"] == "markdown")
    assert "storm is approaching" in Template(summary["content"], hass).async_render()
    assert essential["view_targets"] == {"sscape": device}
    assert essential["target_device_ids"] == [device]
    detailed = await hass.services.async_call(
        "sscape",
        "get_dashboard",
        {
            "device_id": device,
            "profile": "detailed",
            "detail_groups": ["official"],
            "include_optional": True,
        },
        blocking=True,
        return_response=True,
    )
    assert "primary_warning" in str(detailed["config"])
    assert "latest_earthquake" not in str(detailed["config"])


async def test_single_guided_visit_and_notification_receipt_is_not_assumed(hass, config, provider):
    sent = AsyncMock()
    hass.services.async_register("notify", "mobile_app_test_phone", sent)
    flow = await hass.config_entries.options.async_init(config.entry_id)
    assert flow["type"] == "menu"
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "preferences"}
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"configure_notifications": True, "configure_assist": True}
    )
    assert result["step_id"] == "notifications"
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "episode_notifications": True,
            "notification_target": "service:notify.mobile_app_test_phone",
        },
    )
    assert result["step_id"] == "notification_test"
    sent.assert_not_called()
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "send"}
    )
    assert sent.await_count == 1 and result["step_id"] == "notification_test"
    assert not config.options.get("episode_notifications", False)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "received"}
    )
    assert result["step_id"] == "assist"
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"assist_enabled": True, "configure_aliases": True}
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"target": "place-1"}
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"place_aliases": ["home", "garden"]}
    )
    assert result["step_id"] == "review"
    result = await hass.config_entries.options.async_configure(flow["flow_id"], {})
    assert (
        result["type"] == "create_entry"
        and result["data"]["notification_receipt_confirmed"] is True
    )
    assert result["data"]["assist_aliases"]["place-1"] == ["home", "garden"]


async def test_missing_event_entities_are_unknown_with_a_reason(hass, config, provider, payload):
    b = payload["states"][0]["state"]["briefing"]
    b.update(
        situation_phase="quiet",
        approach_eta_minutes=None,
        official_alert_count=0,
        earthquakes_24h=0,
    )
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    keys = ["eta", "official_warning_details", "latest_earthquake_magnitude"]
    ids = {
        key: registry.async_get_entity_id("sensor", "sscape", config.unique_id + "|place-1|" + key)
        for key in keys
    }
    for entity in ids.values():
        registry.async_update_entity(entity, disabled_by=None)
    assert await hass.config_entries.async_reload(config.entry_id)
    await hass.async_block_till_done()
    for entity in ids.values():
        assert hass.states.get(entity).state == "unknown"
        assert hass.states.get(entity).attributes["data_status"] == "not_applicable"
    from homeassistant.helpers.update_coordinator import UpdateFailed

    config.runtime_data.async_set_update_error(UpdateFailed("Synthetic cloud failure"))
    await hass.async_block_till_done()
    for entity in ids.values():
        assert hass.states.get(entity).state == "unavailable"
    connection = registry.async_get_entity_id(
        "sensor", "sscape", config.unique_id + "|place-1|connection_status"
    )
    assert hass.states.get(connection).state == "disconnected"
    assert hass.states.get(connection).attributes["status_message"]


async def test_named_notify_target_unknown_last_send_and_explicit_test_only(hass, config, provider):
    sent = AsyncMock()
    hass.services.async_register("notify", "send_message", sent)
    hass.states.async_set("notify.test_phone", "unknown", {"friendly_name": "Test phone"})
    assert await hass.config_entries.async_setup(config.entry_id)
    await hass.async_block_till_done()
    sent.assert_not_called()
    from custom_components.sscape.notifications import notification_choices

    assert {"value": "entity:notify.test_phone", "label": "Test phone"} in notification_choices(
        hass
    )
    result = await hass.services.async_call(
        "sscape",
        "test_notification",
        {"notification_target": "entity:notify.test_phone"},
        blocking=True,
        return_response=True,
    )
    assert result == {"request_accepted": True, "receipt_verified": False}
    assert sent.await_count == 1 and sent.call_args.args[0].data["entity_id"] == "notify.test_phone"
    from homeassistant.exceptions import ServiceValidationError, Unauthorized

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "sscape",
            "test_notification",
            {"notification_target": "entity:notify.test_phone"},
            blocking=True,
            return_response=True,
        )
    await hass.auth.async_create_user("Owner test")
    user = await hass.auth.async_create_user("Read-only test", group_ids=["system-read-only"])
    from homeassistant.core import Context

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            "sscape",
            "test_notification",
            {"notification_target": "entity:notify.test_phone"},
            blocking=True,
            return_response=True,
            context=Context(user_id=user.id),
        )
    assert sent.await_count == 1


async def test_guided_notification_failure_retry_limit_and_skip(hass, config, provider):
    sent = AsyncMock(side_effect=RuntimeError("Fictional destination failure"))
    hass.services.async_register("notify", "mobile_app_test_phone", sent)
    flow = await hass.config_entries.options.async_init(config.entry_id)
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"next_step_id": "notifications"}
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {"episode_notifications": True, "notification_target": ""},
    )
    assert result["errors"] == {"base": "invalid_notify_service"}
    await hass.config_entries.options.async_configure(
        flow["flow_id"],
        {
            "episode_notifications": True,
            "notification_target": "service:notify.mobile_app_test_phone",
        },
    )
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "received"}
    )
    assert result["errors"] == {"base": "test_not_sent"}
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "send"}
    )
    assert result["errors"] == {"base": "notification_test_failed"}
    assert "Fictional destination failure" not in str(result)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "send"}
    )
    assert result["errors"] == {"base": "test_rate_limited"} and sent.await_count == 1
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"test_action": "skip"}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["notification_receipt_confirmed"] is False


async def test_notification_timeout_is_bounded(hass, monkeypatch):
    import asyncio

    from custom_components.sscape.notifications import async_notify

    original_timeout = asyncio.timeout
    monkeypatch.setattr(
        "custom_components.sscape.notifications.asyncio.timeout", lambda _: original_timeout(0.001)
    )
    blocker = asyncio.Event()

    async def blocked_destination(call):
        await blocker.wait()

    hass.services.async_register("notify", "mobile_app_test_phone", blocked_destination)
    with pytest.raises(TimeoutError):
        await async_notify(
            hass,
            {"notify_service": "notify.mobile_app_test_phone"},
            {"title": "Test", "message": "Fictional message"},
        )


async def test_alias_chips_reject_other_places_names(hass, config, provider):
    hass.config_entries.async_update_entry(
        config,
        options=dict(config.options)
        | {"places": ["place-1"], "assist_aliases": {"other": ["office"]}},
    )
    flow = await hass.config_entries.options.async_init(config.entry_id)
    await hass.config_entries.options.async_configure(flow["flow_id"], {"next_step_id": "assist"})
    await hass.config_entries.options.async_configure(
        flow["flow_id"], {"assist_enabled": True, "configure_aliases": True}
    )
    await hass.config_entries.options.async_configure(flow["flow_id"], {"target": "place-1"})
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"place_aliases": ["OFFICE"]}
    )
    assert result["errors"] == {"base": "invalid_aliases"}
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {"place_aliases": ["garden", "garden"]}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["assist_aliases"]["place-1"] == ["garden"]
