"""Read-only HA actions. They never create dashboards or operate devices."""

from urllib.parse import quote

import voluptuous as vol
import yaml
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN


def setup_actions(hass):
    if hass.services.has_service(DOMAIN, "get_dashboard"):
        return

    def selected_place(device_id):
        device = dr.async_get(hass).async_get(device_id)
        if device:
            current_entry_id = getattr(device, "config_entry_id", None)
            entry_ids = {current_entry_id} if current_entry_id else device.config_entries
            for entry in hass.config_entries.async_entries(DOMAIN):
                parent = getattr(entry, "runtime_data", None)
                if entry.entry_id not in entry_ids or parent is None:
                    continue
                prefix = entry.unique_id + "|"
                for domain, identifier in device.identifiers:
                    if domain == DOMAIN and identifier.startswith(prefix):
                        place_id = identifier[len(prefix) :]
                        if place := parent.data.places.get(place_id):
                            return entry, parent, place
        raise ServiceValidationError("Select an active sScape device for an imported place")

    async def briefing(call):
        _, _, place = selected_place(call.data["device_id"])
        if not place.is_fresh():
            return {"available": False, "reason": "assessment_unavailable"}
        return {
            "available": True,
            "risk_level": place.risk_level,
            **place.attributes,
            "assessment_time": place.briefing.get("updated_at"),
            "data_quality": place.briefing.get("data_quality_status", "unknown"),
            "time_window": place.briefing.get("time_window"),
        }

    async def dashboard(call):
        entry, parent, place = selected_place(call.data["device_id"])
        prefix = f"{entry.unique_id}|{place.place_id}|"
        entities = {
            item.unique_id[len(prefix) :]: item.entity_id
            for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
            if item.unique_id.startswith(prefix) and item.disabled_by is None
        }
        cards = []
        if weather := entities.get("forecast"):
            cards.append(
                {
                    "type": "weather-forecast",
                    "entity": weather,
                    "show_forecast": True,
                    "forecast_type": "hourly",
                }
            )
        tiles = [
            {"type": "tile", "entity": entities[key]}
            for key in (
                "risk",
                "phase",
                "eta",
                "data_quality",
                "observed_temperature",
                "observed_gust",
            )
            if key in entities
        ]
        if tiles:
            cards.append({"type": "grid", "columns": 2, "square": False, "cards": tiles})
        cards.append(
            {
                "type": "button",
                "name": "sScape",
                "icon": "mdi:open-in-new",
                "tap_action": {"action": "url", "url_path": parent.client.host + "/dashboard"},
            }
        )
        storm = place.briefing.get("nearest_storm")
        if isinstance(storm, dict) and isinstance(storm.get("id"), str) and storm["id"]:
            cards.append(
                {
                    "type": "button",
                    "name": "Dettagli del temporale"
                    if hass.config.language.startswith("it")
                    else "Storm dossier",
                    "icon": "mdi:weather-lightning",
                    "tap_action": {
                        "action": "url",
                        "url_path": parent.client.host + "/storms/" + quote(storm["id"], safe=""),
                    },
                }
            )
        value = {
            "title": "sScape",
            "views": [{"title": place.name, "path": "sscape", "cards": cards}],
        }
        return {
            "yaml": yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
            "uses_native_cards_only": True,
            "writes_performed": False,
        }

    for name, handler in (("get_briefing", briefing), ("get_dashboard", dashboard)):
        async_register_admin_service(
            hass,
            DOMAIN,
            name,
            handler,
            schema=vol.Schema({vol.Required("device_id"): str}),
            supports_response=SupportsResponse.ONLY,
        )
