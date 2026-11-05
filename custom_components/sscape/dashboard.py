"""Native dashboards: one-place detail or an explicitly scoped places overview."""

from urllib.parse import quote

from homeassistant.helpers import entity_registry as er

from .targets import allows_target


def place_map_url(parent, place):
    if point := parent.client.points.get(place.place_id):
        return (
            parent.client.host
            + "/map?lat="
            + str(point["latitude"])
            + "&lng="
            + str(point["longitude"])
            + "&zoom=12"
        )
    return parent.client.host + "/map?place=" + quote(place.place_id, safe="")


def _entities(hass, entry, place, *, include_optional=False):
    prefix = f"{entry.unique_id}|{place.place_id}|"
    return {
        item.unique_id[len(prefix) :]: item.entity_id
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.unique_id.startswith(prefix)
        and (
            item.disabled_by is None
            or (
                include_optional
                and item.disabled_by == er.RegistryEntryDisabler.INTEGRATION
                and not item.options.get("sscape", {}).get("place_excluded")
            )
        )
    }


def _known_tile(entity):
    return {
        "type": "conditional",
        "conditions": [
            {"condition": "state", "entity": entity, "state_not": "unknown"},
            {"condition": "state", "entity": entity, "state_not": "unavailable"},
        ],
        "card": {"type": "tile", "entity": entity},
    }


DETAIL_GROUPS = {
    "storm": (
        "phase",
        "eta_min",
        "eta_max",
        "storm_distance",
        "lightning_count",
        "lightning_distance",
    ),
    "official": ("official_warning_details", "official_warning_expires"),
    "earthquake": (
        "latest_earthquake_magnitude",
        "latest_earthquake_distance",
        "latest_earthquake_time",
    ),
    "thermal": ("nearest_thermal_detection",),
    "winter": (
        "forecast_snowfall_cm",
        "forecast_visibility_minimum_m",
        "forecast_minimum_temperature_c",
        "forecast_temperature_minimum_time",
    ),
    "observations": ("observed_temperature", "observed_humidity", "observed_wind", "observed_gust"),
    "local": (
        "local_temperature",
        "local_humidity",
        "local_pressure",
        "local_wind_speed",
        "local_wind_gust",
        "local_rain_rate",
        "local_rain_counter",
    ),
}
GROUP_TITLES = {
    "storm": ("Temporali e fulmini", "Storms and lightning"),
    "official": ("Avvisi ufficiali", "Official warnings"),
    "earthquake": ("Eventi sismici", "Earthquakes"),
    "thermal": ("Rilevamenti termici", "Thermal detections"),
    "winter": ("Neve e visibilità", "Snow and visibility"),
    "observations": ("Stazioni nelle vicinanze", "Nearby stations"),
    "local": ("La tua stazione", "Your local instruments"),
}


def _heading(title):
    return {"type": "heading", "heading": title, "heading_style": "title"}


def _summary_card(entity, language, availability_entity=None):
    unavailable = (
        "Analisi non disponibile: non è un cessato allarme."
        if language == 0
        else "Assessment unavailable; this is not an all-clear."
    )
    return {
        "type": "markdown",
        "content": (
            "{% if is_state('"
            + entity
            + "', 'unavailable') and states('"
            + availability_entity
            + "') not in ['unknown','unavailable','ready'] %}{{ state_attr('"
            + availability_entity
            + "', 'status_message') | e }}{% else %}"
            if availability_entity
            else ""
        )
        + "{% if states('"
        + entity
        + "') in ['unknown','unavailable'] %}"
        + "{{ state_attr('"
        + entity
        + "', 'status_message') | default('"
        + unavailable
        + "', true) | e }}"
        + "{% else %}{{ state_attr('"
        + entity
        + "', 'headline') | default('', true) | e }}"
        + "{% if states('"
        + entity
        + "') in ['high','severe'] %}\n\n{{ state_attr('"
        + entity
        + "', 'recommended_action') | default('', true) | e }}{% endif %}{% endif %}"
        + ("{% endif %}" if availability_entity else ""),
    }


def _place_cards(
    hass, entry, parent, place, *, include_optional=False, profile="essential", detail_groups=None
):
    entities = _entities(hass, entry, place, include_optional=include_optional)
    lang = 0 if hass.config.language.startswith("it") else 1
    cards = [_heading(("Adesso", "Now")[lang])]
    if risk := entities.get("risk"):
        cards.append(_summary_card(risk, lang, entities.get("connection_status")))
        now_tiles = [{"type": "tile", "entity": risk}]
        if eta := entities.get("eta"):
            now_tiles.append(_known_tile(eta))
        cards.append({"type": "grid", "columns": 2, "square": False, "cards": now_tiles})
    cards.append(_heading(("Prossime ore", "Next hours")[lang]))
    if weather := entities.get("forecast"):
        cards.append(
            {
                "type": "weather-forecast",
                "entity": weather,
                "name": place.name,
                "show_forecast": True,
                "forecast_type": "hourly",
            }
        )
    next_tiles = [
        _known_tile(entities[key])
        for key in (
            "forecast_precipitation_outlook",
            "forecast_next_rain_window",
            "forecast_peak_gust_kmh",
            "forecast_gust_peak_time",
        )
        if key in entities and (profile == "detailed" or key == "forecast_precipitation_outlook")
    ]
    if next_tiles:
        cards.append({"type": "grid", "columns": 2, "square": False, "cards": next_tiles})
    cards.append(_heading(("Dati e copertura", "Data and coverage")[lang]))
    data_tiles = [
        {"type": "tile", "entity": entities[key]}
        for key in ("connection_status", "updated", "data_quality")
        if key in entities
    ]
    if data_tiles:
        cards.append({"type": "grid", "columns": 2, "square": False, "cards": data_tiles})
    if profile == "detailed":
        groups = detail_groups if detail_groups is not None else list(DETAIL_GROUPS)
        for group in groups:
            tiles = [_known_tile(entities[key]) for key in DETAIL_GROUPS[group] if key in entities]
            if tiles:
                cards.extend(
                    [
                        _heading(GROUP_TITLES[group][lang]),
                        {"type": "grid", "columns": 2, "square": False, "cards": tiles},
                    ]
                )
    cards.append(
        {
            "type": "button",
            "name": ("Vedi evidenze sulla mappa", "Inspect evidence on the map")[lang],
            "icon": "mdi:map-marker-outline",
            "tap_action": {"action": "url", "url_path": place_map_url(parent, place)},
        }
    )
    return cards


def build_dashboard(
    hass,
    entry,
    parent,
    place,
    *,
    all_places=False,
    include_optional=False,
    profile=None,
    detail_groups=None,
):
    profile = profile or entry.options.get("dashboard_profile", "essential")
    if profile not in ("essential", "detailed") or (
        detail_groups is not None
        and (not isinstance(detail_groups, list) or not set(detail_groups).issubset(DETAIL_GROUPS))
    ):
        raise ValueError("Choose a supported dashboard profile and detail groups")

    def cards(item):
        return _place_cards(
            hass,
            entry,
            parent,
            item,
            include_optional=include_optional,
            profile=profile,
            detail_groups=detail_groups,
        )

    if not all_places:
        return {
            "title": "sScape",
            "views": [{"title": place.name, "path": "sscape", "cards": cards(place)}],
        }
    places = sorted(
        (p for p in parent.data.places.values() if allows_target(entry, p.place_id)),
        key=lambda p: (p.name.casefold(), p.place_id),
    )
    overview = []
    views = []
    for index, item in enumerate(places, start=1):
        entities = _entities(hass, entry, item)
        if risk := entities.get("risk"):
            overview.append(
                {
                    "type": "tile",
                    "entity": risk,
                    "name": item.name,
                    "state_content": ["state", "headline"],
                }
            )
        views.append({"title": item.name, "path": f"sscape-{index}", "cards": cards(item)})
    return {
        "title": "sScape",
        "views": [
            {
                "title": "Luoghi" if hass.config.language.startswith("it") else "Places",
                "path": "sscape",
                "cards": [{"type": "grid", "columns": 1, "square": False, "cards": overview}],
            },
            *views,
        ],
    }
