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
from .dashboard import build_dashboard, place_map_url
from .hazards import hazards_for
from .planning import make_outlook
from .targets import allows_target


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
                if entry.entry_id not in entry_ids or parent is None or parent.stopping:
                    continue
                prefix = entry.unique_id + "|"
                for domain, identifier in device.identifiers:
                    if domain == DOMAIN and identifier.startswith(prefix):
                        place_id = identifier[len(prefix) :]
                        if not allows_target(entry, place_id):
                            continue
                        if place := parent.data.places.get(place_id):
                            return entry, parent, place
        raise ServiceValidationError("Select an active sScape device for an imported place")

    async def briefing(call):
        _, parent, place = selected_place(call.data["device_id"])
        if not parent.last_update_success or not place.is_fresh():
            return {"available": False, "reason": "assessment_unavailable"}
        storm = place.briefing.get("nearest_storm")
        storm_id = storm.get("id") if isinstance(storm, dict) else None
        return {
            "available": True,
            "risk_level": place.risk_level,
            **place.attributes,
            "assessment_time": place.briefing.get("updated_at"),
            "data_quality": place.briefing.get("data_quality_status", "unknown"),
            "time_window": place.briefing.get("time_window"),
            "map_url": place_map_url(parent, place),
            "storm_url": parent.client.host + "/storms/" + quote(storm_id, safe="")
            if isinstance(storm_id, str) and storm_id
            else None,
        }

    async def outlook(call):
        _, parent, place = selected_place(call.data["device_id"])
        weather = parent.weather_coordinators.get(place.place_id)
        if not parent.last_update_success or weather is None or not weather.last_update_success:
            return {"available": False, "reason": "forecast_unavailable"}
        return make_outlook(weather.data, hours=call.data["hours"])

    async def hazards(call):
        _, parent, place = selected_place(call.data["device_id"])
        if not parent.last_update_success:
            return {"available": False, "reason": "assessment_unavailable"}
        return hazards_for(place) | {"map_url": place_map_url(parent, place)}

    async def setup_info(call):
        result = []
        registry = er.async_get(hass)
        for config in hass.config_entries.async_entries(DOMAIN):
            parent = getattr(config, "runtime_data", None)
            if parent is None or parent.stopping:
                continue
            for place in parent.data.places.values():
                if not allows_target(config, place.place_id):
                    continue
                prefix = f"{config.unique_id}|{place.place_id}|"
                entities = {
                    item.unique_id[len(prefix) :]: {
                        "entity_id": item.entity_id,
                        "disabled": item.disabled_by is not None,
                        "disabled_by": item.disabled_by.value if item.disabled_by else None,
                        "name": item.name or item.original_name,
                    }
                    for item in er.async_entries_for_config_entry(registry, config.entry_id)
                    if item.unique_id.startswith(prefix)
                }
                device_id = next(
                    (
                        registry.async_get(row["entity_id"]).device_id
                        for row in entities.values()
                        if registry.async_get(row["entity_id"]).device_id
                    ),
                    None,
                )
                if device_id:
                    result.append(
                        {
                            "name": place.name,
                            "device_id": device_id,
                            "entities": entities,
                            "coordinate_target": place.place_id in parent.client.points,
                            "assessment_available": parent.last_update_success and place.is_fresh(),
                        }
                    )
        return {"targets": result, "writes_performed": False}

    async def dashboard(call):
        entry, parent, place = selected_place(call.data["device_id"])
        value = build_dashboard(
            hass,
            entry,
            parent,
            place,
            all_places=call.data["all_selected_places"],
            include_optional=call.data["include_optional"],
        )
        return {
            "yaml": yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
            "uses_native_cards_only": True,
            "writes_performed": False,
            "config": value,
        }

    for name, handler, fields in (
        ("get_briefing", briefing, {}),
        ("get_hazards", hazards, {}),
        ("get_setup", setup_info, {}),
        (
            "get_dashboard",
            dashboard,
            {
                vol.Optional("all_selected_places", default=False): bool,
                vol.Optional("include_optional", default=False): bool,
            },
        ),
        (
            "get_outlook",
            outlook,
            {vol.Optional("hours", default=6): vol.All(vol.Coerce(int), vol.Range(min=1, max=24))},
        ),
    ):
        async_register_admin_service(
            hass,
            DOMAIN,
            name,
            handler,
            schema=vol.Schema(
                {} if name == "get_setup" else {vol.Required("device_id"): str, **fields}
            ),
            supports_response=SupportsResponse.ONLY,
        )
