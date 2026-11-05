"""Friendly, validated HA notification destinations. No sScape network calls."""

import asyncio

from homeassistant.exceptions import HomeAssistantError


def notification_choices(hass):
    choices = []
    if hass.services.has_service("notify", "send_message"):
        for state in hass.states.async_all("notify"):
            if state.state != "unavailable":
                choices.append({"value": "entity:" + state.entity_id, "label": state.name})
    for name in hass.services.async_services().get("notify", {}):
        if name == "send_message":
            continue
        label = name.removeprefix("mobile_app_").replace("_", " ").strip()
        choices.append({"value": "service:notify." + name, "label": label[:1].upper() + label[1:]})
    return sorted(choices, key=lambda row: (row["label"].casefold(), row["value"]))


def target_options(hass, target):
    if target not in {row["value"] for row in notification_choices(hass)}:
        raise HomeAssistantError("Notification destination unavailable")
    kind, identifier = target.split(":", 1)
    return {
        "notify_entity_id": identifier if kind == "entity" else "",
        "notify_service": identifier if kind == "service" else "",
    }


async def async_notify(hass, options, payload):
    entity = options.get("notify_entity_id")
    service = options.get("notify_service", "")
    if entity:
        target_options(hass, "entity:" + entity)
        domain, service = "notify", "send_message"
        data = {"entity_id": entity, "message": payload["message"], "title": payload["title"]}
    else:
        target_options(hass, "service:" + service)
        domain, _, service = service.partition(".")
        data = payload
    async with asyncio.timeout(15):
        try:
            await hass.services.async_call(domain, service, data, blocking=True)
        except Exception:
            raise HomeAssistantError("Notification request failed") from None
