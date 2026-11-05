"""Small read-only summaries from cached, source-faithful integration data."""

from .api import _time
from .planning import make_outlook
from .status import value_status
from .weather import forecast_available


def target_summary(hass, parent, place):
    italian = hass.config.language.startswith("it")
    status = value_status(
        place,
        "risk",
        place.risk_level,
        connected=parent.last_update_success,
        language=hass.config.language,
    )
    valid = status["data_status"] == "available"
    coverage = place.briefing.get("data_quality_status", "unknown")
    labels = {
        "unknown": ("Copertura da verificare", "Coverage unverified"),
        "partial": ("Copertura parziale", "Partial coverage"),
        "stale": ("Osservazioni in ritardo", "Observations delayed"),
    }
    quality = place.briefing.get("data_quality")
    clocks = (
        [_time(feed.get("observation_time")) for feed in quality.values() if isinstance(feed, dict)]
        if isinstance(quality, dict)
        else []
    )
    clocks = [clock for clock in clocks if clock is not None]
    result = {
        "headline": place.headline if valid else status["status_message"],
        "risk_level": place.risk_level if valid else None,
        "data_status": status["data_status"],
        "coverage_label": labels.get(coverage, labels["unknown"])[0 if italian else 1],
        "assessment_time": place.updated_at.isoformat() if place.updated_at else None,
        "latest_observation_time": max(clocks).isoformat() if clocks else None,
        "next": [],
        "forecast_available": False,
        "source_labels": [],
    }
    weather = parent.weather_coordinators.get(place.place_id)
    if not forecast_available(parent, weather, place.place_id):
        return result
    plan = make_outlook(weather.data, hours=6)
    if not plan["available"]:
        return result
    result.update(
        forecast_available=True,
        forecast_period_start=plan["period_start"],
        forecast_period_end=plan["period_end"],
    )
    if plan["precipitation"]["status"] == "not_expected":
        result["next"].append(
            {
                "label": "Precipitazioni" if italian else "Precipitation",
                "text": "Non previste nella finestra coperta"
                if italian
                else "Not forecast in the covered window",
            }
        )
    elif window := plan["precipitation"]["first_window"]:
        result["next"].append(
            {
                "label": "Precipitazioni previste" if italian else "Forecast precipitation",
                "start": window["start"],
                "end": window["end"],
                "amount_mm": window["amount_mm"],
            }
        )
    else:
        result["next"].append(
            {
                "label": "Precipitazioni" if italian else "Precipitation",
                "text": "Previsione incompleta" if italian else "Incomplete forecast",
            }
        )
    if plan["wind_gust"]["complete"]:
        result["next"].append(
            {
                "label": "Raffica massima prevista" if italian else "Forecast peak gust",
                "value": plan["wind_gust"]["peak_kmh"],
                "unit": "km/h",
                "start": plan["wind_gust"]["period_start"],
                "end": plan["wind_gust"]["period_end"],
            }
        )
    if plan["temperature"]["complete"]:
        result["next"].append(
            {
                "label": "Minima oraria prevista" if italian else "Forecast hourly minimum",
                "value": plan["temperature"]["minimum_c"],
                "unit": "°C",
                "time": plan["temperature"]["time"],
            }
        )
    result["source_labels"] = [
        row.get("name") or row["id"]
        for row in weather.data.raw.get("source_details", [])
        if isinstance(row, dict)
        and row.get("id")
        and row.get("attribution")
        and row.get("license_url")
    ][:6]
    return result
