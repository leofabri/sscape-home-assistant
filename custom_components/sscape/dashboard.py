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


def _place_cards(hass, entry, parent, place, *, include_optional=False):
    entities = _entities(hass, entry, place, include_optional=include_optional)
    cards = []
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
    tiles = [
        {"type": "tile", "entity": entities[key]}
        for key in (
            "risk",
            "phase",
            "data_quality",
            "forecast_precipitation_outlook",
            "observed_temperature",
            "observed_gust",
        )
        if key in entities
    ]
    if tiles:
        cards.append({"type": "grid", "columns": 2, "square": False, "cards": tiles})
    # Optional timings disappear when inapplicable; risk and data-quality tiles
    # above never disappear during outages.
    for key in (
        "eta",
        "forecast_next_rain_window",
        "forecast_gust_peak_time",
        "forecast_temperature_minimum_time",
    ):
        if entity := entities.get(key):
            cards.append(_known_tile(entity))
    detail_tiles = [
        _known_tile(entity)
        for key in (
            "official_warning_details",
            "official_warning_expires",
            "latest_earthquake_magnitude",
            "latest_earthquake_distance",
            "latest_earthquake_time",
            "nearest_thermal_detection",
            "forecast_snowfall_cm",
            "forecast_visibility_minimum_m",
            "local_temperature",
            "local_humidity",
            "local_pressure",
            "local_wind_speed",
            "local_wind_gust",
            "local_rain_rate",
            "local_rain_counter",
        )
        if (entity := entities.get(key))
    ]
    if detail_tiles:
        cards.append({"type": "grid", "columns": 2, "square": False, "cards": detail_tiles})
    cards.append(
        {
            "type": "button",
            "name": "Apri questo luogo sulla mappa"
            if hass.config.language.startswith("it")
            else "Open this place on the map",
            "icon": "mdi:map-marker-outline",
            "tap_action": {"action": "url", "url_path": place_map_url(parent, place)},
        }
    )
    return cards


def build_dashboard(hass, entry, parent, place, *, all_places=False, include_optional=False):
    if not all_places:
        return {
            "title": "sScape",
            "views": [
                {
                    "title": place.name,
                    "path": "sscape",
                    "cards": _place_cards(
                        hass, entry, parent, place, include_optional=include_optional
                    ),
                }
            ],
        }
    allowed = {key for key in parent.data.places if allows_target(entry, key)}
    places = sorted(
        (parent.data.places[key] for key in allowed),
        key=lambda item: (item.name.casefold(), item.place_id),
    )
    overview, risks, views = [], [], []
    for index, item in enumerate(places, start=1):
        entities = _entities(hass, entry, item)
        if risk := entities.get("risk"):
            risks.append({"entity": risk, "name": item.name})
        if weather := entities.get("forecast"):
            overview.append(
                {
                    "type": "weather-forecast",
                    "entity": weather,
                    "name": item.name,
                    "show_forecast": False,
                }
            )
        views.append(
            {
                "title": item.name,
                "path": f"sscape-{index}",
                "cards": _place_cards(hass, entry, parent, item, include_optional=include_optional),
            }
        )
    if risks:
        overview.insert(
            0,
            {
                "type": "entities",
                "title": "Rischio nei tuoi luoghi"
                if hass.config.language.startswith("it")
                else "Risk at your places",
                "show_header_toggle": False,
                "entities": risks,
            },
        )
    return {
        "title": "sScape",
        "views": [
            {
                "title": "Luoghi" if hass.config.language.startswith("it") else "Places",
                "path": "sscape",
                "cards": overview,
            },
            *views,
        ],
    }
