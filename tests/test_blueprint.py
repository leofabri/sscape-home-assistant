"""Validate and execute the published blueprint without real notifications."""

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.components.blueprint.schemas import BLUEPRINT_SCHEMA
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

ROOT = Path(__file__).resolve().parents[1]


def expanded():
    data = yaml_util.load_yaml(str(ROOT / "blueprints/automation/sscape/risk_change.yaml"))
    blueprint = Blueprint(data, expected_domain="automation", schema=BLUEPRINT_SCHEMA)
    inputs = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": "sscape/risk_change.yaml",
                "input": {
                    "risk_sensor": "sensor.test_risk",
                    "confirmation_minutes": 2,
                    "notify_worsening": [
                        {
                            "action": "test.notify",
                            "data": {"kind": "worse", "risk": "{{ sscape_risk }}"},
                        }
                    ],
                    "notify_cleared": [{"action": "test.notify", "data": {"kind": "cleared"}}],
                },
            }
        },
    )
    inputs.validate()
    return inputs.async_substitute()


async def test_real_blueprint_worsening_no_duplicates_no_false_clear(hass, freezer):
    notify = AsyncMock()
    hass.services.async_register("test", "notify", notify)
    hass.states.async_set("sensor.test_risk", "low")
    assert await async_setup_component(hass, "automation", {"automation": [expanded()]})
    await hass.async_block_till_done()
    hass.states.async_set("sensor.test_risk", "high", {"headline": "Test storm"})
    await hass.async_block_till_done()
    assert notify.await_count == 1
    assert notify.call_args.args[0].data["risk"] == "high"
    hass.states.async_set("sensor.test_risk", "high", {"headline": "Updated evidence"})
    await hass.async_block_till_done()
    assert notify.await_count == 1
    hass.states.async_set("sensor.test_risk", "unavailable")
    hass.states.async_set("sensor.test_risk", "low")
    await hass.async_block_till_done()
    freezer.move_to(dt_util.utcnow() + timedelta(minutes=3))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert notify.await_count == 1
    hass.states.async_set("sensor.test_risk", "moderate")
    await hass.async_block_till_done()
    assert notify.await_count == 2
    hass.states.async_set("sensor.test_risk", "low")
    await hass.async_block_till_done()
    freezer.move_to(dt_util.utcnow() + timedelta(minutes=3))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert notify.await_count == 3
    assert notify.call_args.args[0].data["kind"] == "cleared"
