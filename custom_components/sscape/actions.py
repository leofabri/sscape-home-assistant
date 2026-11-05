"""Admin-only queries and explicit notification/Assist tests, never device control."""

from time import monotonic
from urllib.parse import quote

import voluptuous as vol
import yaml
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service

from .const import DOMAIN
from .dashboard import DETAIL_GROUPS, build_dashboard, place_map_url
from .hazards import hazards_for
from .notifications import async_notify, notification_choices, target_options
from .planning import make_outlook
from .presentation import target_summary
from .targets import allows_target
from .weather import forecast_available


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
        if not forecast_available(parent, weather, place.place_id):
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
                            "summary": target_summary(hass, parent, place),
                            "dashboard_profile": config.options.get(
                                "dashboard_profile", "essential"
                            ),
                            "assist_enabled": config.options.get("assist_enabled", False),
                        }
                    )
        return {
            "targets": result,
            "writes_performed": False,
            "notification_targets": notification_choices(hass),
        }

    test_times = {}

    async def test_notification(call):
        target = call.data["notification_target"]
        key = (call.context.user_id, target)
        if monotonic() - test_times.get(key, -1000) < 30:
            raise ServiceValidationError("Wait 30 seconds before another test")
        test_times[key] = monotonic()
        while len(test_times) > 128:
            test_times.pop(next(iter(test_times)))
        try:
            options = target_options(hass, target)
            await async_notify(
                hass,
                options,
                {
                    "title": "sScape · Test",
                    "message": "Notifica di prova sScape. Nessuna allerta reale."
                    if hass.config.language.startswith("it")
                    else "sScape test notification. This is not a real warning.",
                },
            )
        except (TimeoutError, HomeAssistantError):
            raise ServiceValidationError(
                "Notification request failed; check the destination"
            ) from None
        return {"request_accepted": True, "receipt_verified": False}

    async def test_assist(call):
        _, parent, place = selected_place(call.data["device_id"])
        if getattr(parent, "assist_manager", None) is None:
            return {
                "available": False,
                "reason": "assist_disabled",
                "hardware_delivery_verified": False,
            }
        from homeassistant.components import conversation

        phrase = (
            "sscape situazione a "
            if hass.config.language.startswith("it")
            else "sscape briefing at "
        ) + place.name
        reply = await conversation.async_converse(
            hass,
            phrase,
            None,
            call.context,
            language=hass.config.language,
            agent_id=conversation.HOME_ASSISTANT_AGENT,
        )
        speech = reply.response.as_dict().get("speech", {}).get("plain", {}).get("speech", "")
        return {
            "available": True,
            "phrase": phrase,
            "speech": str(speech)[:1000],
            "hardware_delivery_verified": False,
        }

    async def dashboard(call):
        entry, parent, place = selected_place(call.data["device_id"])
        value = build_dashboard(
            hass,
            entry,
            parent,
            place,
            all_places=call.data["all_selected_places"],
            include_optional=call.data["include_optional"],
            profile=call.data.get("profile"),
            detail_groups=call.data.get("detail_groups"),
        )
        items = (
            sorted(
                (p for p in parent.data.places.values() if allows_target(entry, p.place_id)),
                key=lambda p: (p.name.casefold(), p.place_id),
            )
            if call.data["all_selected_places"]
            else [place]
        )
        view_targets = {}
        for index, item in enumerate(items, start=1):
            prefix = f"{entry.unique_id}|{item.place_id}|"
            device_id = next(
                (
                    e.device_id
                    for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
                    if e.unique_id.startswith(prefix) and e.device_id
                ),
                None,
            )
            if device_id:
                view_targets[
                    f"sscape-{index}" if call.data["all_selected_places"] else "sscape"
                ] = device_id
        return {
            "yaml": yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
            "uses_native_cards_only": True,
            "writes_performed": False,
            "config": value,
            "view_targets": view_targets,
            "target_device_ids": list(dict.fromkeys(view_targets.values())),
        }

    for name, handler, fields in (
        ("get_briefing", briefing, {}),
        ("get_hazards", hazards, {}),
        ("get_setup", setup_info, {}),
        ("test_assist", test_assist, {}),
        ("test_notification", test_notification, {vol.Required("notification_target"): str}),
        (
            "get_dashboard",
            dashboard,
            {
                vol.Optional("all_selected_places", default=False): bool,
                vol.Optional("include_optional", default=False): bool,
                vol.Optional("profile"): vol.In(["essential", "detailed"]),
                vol.Optional("detail_groups"): [vol.In(DETAIL_GROUPS)],
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
                fields
                if name in ("get_setup", "test_notification")
                else {vol.Required("device_id"): str, **fields}
            ),
            supports_response=SupportsResponse.ONLY,
        )
