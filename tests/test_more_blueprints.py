"""Validate the importable automations and execute threshold semantics in HA."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


def expanded(name, inputs):
    path = Path(__file__).resolve().parents[1] / "blueprints/automation/sscape" / name
    blueprint = Blueprint(
        yaml_util.load_yaml(str(path)), expected_domain="automation", schema=BLUEPRINT_SCHEMA
    )
    values = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": "sscape/" + name, "input": inputs}}
    )
    values.validate()
    return values.async_substitute()


async def test_threshold_blueprint_no_duplicates_or_unavailable_crossing(hass, freezer):
    notify = AsyncMock()
    hass.services.async_register("test", "notify", notify)
    config = expanded(
        "measurement_threshold.yaml",
        {
            "numeric_sensor": "sensor.test_temperature",
            "threshold": 0,
            "direction": "below",
            "confirmation_minutes": 1,
            "actions": [{"action": "test.notify"}],
        },
    )
    hass.states.async_set("sensor.test_temperature", "5")
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()

    async def settle():
        await hass.async_block_till_done()
        freezer.move_to(dt_util.utcnow() + timedelta(minutes=2))
        async_fire_time_changed(hass, dt_util.utcnow())
        await hass.async_block_till_done()

    hass.states.async_set("sensor.test_temperature", "-2")
    await settle()
    assert notify.await_count == 1
    hass.states.async_set("sensor.test_temperature", "-3")
    await settle()
    assert notify.await_count == 1
    hass.states.async_set("sensor.test_temperature", "unavailable")
    hass.states.async_set("sensor.test_temperature", "-4")
    await settle()
    assert notify.await_count == 1


async def test_precipitation_blueprint_ignores_unknown_restore_and_attribute_changes(hass):
    notify = AsyncMock()
    hass.services.async_register("test", "notify", notify)
    config = expanded(
        "forecast_precipitation.yaml",
        {
            "outlook_sensor": "sensor.test_outlook",
            "confirmation_minutes": 0,
            "actions": [{"action": "test.notify"}],
        },
    )
    hass.states.async_set("sensor.test_outlook", "expected")
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    assert notify.await_count == 0
    for state in ("unavailable", "expected", "data_incomplete", "expected"):
        hass.states.async_set("sensor.test_outlook", state)
        await hass.async_block_till_done()
    assert notify.await_count == 0
    hass.states.async_set("sensor.test_outlook", "not_expected")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.test_outlook", "expected")
    await hass.async_block_till_done()
    assert notify.await_count == 1
    hass.states.async_set("sensor.test_outlook", "expected", {"new_forecast": True})
    await hass.async_block_till_done()
    assert notify.await_count == 1


async def test_event_blueprint_ignores_attribute_refresh_and_outage_restore(hass):
    notify = AsyncMock()
    hass.services.async_register("test", "notify", notify)
    config = expanded(
        "situation_change.yaml",
        {"event_entity": "event.test_changes", "actions": [{"action": "test.notify"}]},
    )
    hass.states.async_set(
        "event.test_changes", "2026-11-04T10:00:00Z", {"event_type": "risk_increased"}
    )
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    hass.states.async_set(
        "event.test_changes", "2026-11-04T10:01:00Z", {"event_type": "risk_increased"}
    )
    await hass.async_block_till_done()
    assert notify.await_count == 1
    hass.states.async_set(
        "event.test_changes",
        "2026-11-04T10:01:00Z",
        {"event_type": "risk_increased", "risk_level": "high"},
    )
    await hass.async_block_till_done()
    hass.states.async_set("event.test_changes", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set(
        "event.test_changes", "2026-11-04T10:01:00Z", {"event_type": "risk_increased"}
    )
    await hass.async_block_till_done()
    assert notify.await_count == 1
