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
    assert hass.states.get(entity).state == "unavailable"
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
        {
            "places": ["place-1"],
            "update_interval": 120,
            "weather_enabled": False,
            "configure_more": step,
        },
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
                "notify_service": "",
                "episode_kinds": ["storm"],
            },
        )
        assert result["errors"]["base"] == "invalid_notify_service"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {"episode_notifications": False, "notify_service": "", "episode_kinds": ["storm"]},
        )
    else:
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "assist_enabled": True,
                "assist_allow_anonymous": False,
                "aliases": "Test place = home, garden",
            },
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
        assert hass.states.get(ids[0]).attributes["data_kind"] == "model_forecast"
        raw["hours"][0].pop("snowfall_cm")
        raw["hours"][0].pop("visibility_m")
        await config.runtime_data.weather_coordinators["place-1"].async_refresh()
        await hass.async_block_till_done()
        assert all(hass.states.get(entity).state == "unavailable" for entity in ids)
