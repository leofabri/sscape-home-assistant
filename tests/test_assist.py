"""Real built-in Assist sentence recognition, exposure and authorization."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.components import conversation
from homeassistant.components.homeassistant.exposed_entities import async_expose_entity
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.api import PlaceCatalog, parse_snapshot
from custom_components.sscape.assist import SScapeAssist
from custom_components.sscape.forecast import WeatherSnapshot


async def test_assist_answers_selected_exposed_place_and_denies_unexposed(hass, payload):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    user = await hass.auth.async_create_user("Test owner")
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "weather_enabled": False, "assist_enabled": True},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Test place"}, False)),
        ),
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_snapshot",
            AsyncMock(return_value=parse_snapshot(payload)),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        risk = er.async_get(hass).async_get_entity_id(
            "sensor", "sscape", f"{entry.unique_id}|place-1|risk"
        )
        async_expose_entity(hass, "conversation", risk, True)
        result = await conversation.async_converse(
            hass, "sscape briefing at Test place", None, Context(user_id=user.id), language="en"
        )
        speech = result.response.as_dict()["speech"]["plain"]["speech"]
        assert "storm is approaching" in speech
        async_expose_entity(hass, "conversation", risk, False)
        result = await conversation.async_converse(
            hass, "sscape briefing at Test place", None, Context(user_id=user.id), language="en"
        )
        assert "storm is approaching" not in result.response.as_dict()["speech"]["plain"]["speech"]
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert len(entry.runtime_data.assist_manager.parents) == 1
        assert len(entry.runtime_data.assist_manager._unsubs) == 5
        result = await conversation.async_converse(
            hass, "sscape briefing at Test place", None, Context(), language="en"
        )
        assert "storm is approaching" not in result.response.as_dict()["speech"]["plain"]["speech"]


async def test_assist_forecast_hazards_aliases_freshness_and_missing_values(hass, payload):
    assert await async_setup_component(hass, "homeassistant", {})
    user = await hass.auth.async_create_user("Test owner")
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        unique_id="https://example.com|account-1",
        data={"account_id": "account-1"},
        options={"places": ["place-1"], "assist_aliases": {"place-1": ["casa"]}},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, key in (("sensor", "risk"), ("weather", "forecast")):
        entity = registry.async_get_or_create(
            domain, "sscape", entry.unique_id + "|place-1|" + key, config_entry=entry
        )
        async_expose_entity(hass, "conversation", entity.entity_id, True)
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
                "attribution": "Test model",
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
                "precipitation_mm": 1 if i in (2, 3) else 0,
            }
            for i in range(24)
        ],
    }
    weather = SimpleNamespace(last_update_success=True, data=WeatherSnapshot.parse(raw))
    parent = SimpleNamespace(
        config_entry=entry,
        stopping=False,
        last_update_success=True,
        data=parse_snapshot(payload),
        weather_coordinators={"place-1": weather},
    )
    manager = SScapeAssist(hass)
    manager.parents[entry.entry_id] = parent
    request = SimpleNamespace(language="it", context=Context(user_id=user.id))
    assert "Precipitazioni previste nella fascia" in await manager.answer(request, "casa", "rain")
    assert "-2 gradi Celsius" in await manager.answer(request, "casa", "cold")
    assert "12 centimetri" in await manager.answer(request, "casa", "snow")
    for row in raw["hours"]:
        row["precipitation_mm"] = 0
    assert "Non sono previste precipitazioni" in await manager.answer(request, "casa", "rain")
    raw["hours"][0].update(precipitation_mm=None, temperature_c=None, snowfall_cm=None)
    assert "mancano alcune ore" in await manager.answer(request, "casa", "rain")
    assert "non disponibile" in await manager.answer(request, "casa", "snow")
    assert "non disponibile" in await manager.answer(request, "casa", "cold")
    b = parent.data.places["place-1"].briefing
    b.update(
        official_alert_count=1,
        official_alert_headline="Allerta vento",
        official_alert_covers_point=False,
        official_alert_expires=(now + timedelta(hours=2)).isoformat(),
    )
    speech = await manager.answer(request, "casa", "alerts")
    assert "Allerta vento" in speech and "non comprende il punto" in speech
    b["official_alert_count"] = 0
    assert "Non ho un’allerta verificata" in await manager.answer(request, "casa", "alerts")
    weather.last_update_success = False
    assert "non disponibile" in await manager.answer(request, "casa", "rain")
    parent.last_update_success = False
    assert "non è al momento disponibile" in await manager.answer(request, "casa", "briefing")
    parent.stopping = True
    assert "Luogo non disponibile" in await manager.answer(request, "casa", "briefing")
