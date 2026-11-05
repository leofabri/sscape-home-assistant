"""Explain missing values without confusing them with loss of connectivity."""

MESSAGES = {
    "available": ("Dato disponibile", "Value available"),
    "unavailable": ("Collegamento a sScape non disponibile", "sScape connection unavailable"),
    "stale": (
        "L’analisi è scaduta: attendo un aggiornamento",
        "Assessment expired; waiting for an update",
    ),
    "missing": (
        "Questa informazione non è fornita dai dati disponibili",
        "This information is not supplied by the available data",
    ),
    "not_applicable": (
        "Nessun evento pertinente nei dati disponibili",
        "No applicable event in the available data",
    ),
    "no_approach": (
        "Non c’è una stima di arrivo da mostrare in questa fase",
        "No arrival estimate applies in this storm phase",
    ),
    "range_only": (
        "La traiettoria consente un intervallo, non un minuto preciso",
        "The track supports a time window, not a precise minute",
    ),
    "source_stale": (
        "Le osservazioni di questa fonte sono in ritardo",
        "Observations from this source are delayed",
    ),
    "coverage_unknown": (
        "La copertura non è verificata: non è un cessato allarme",
        "Coverage is unverified; this is not an all-clear",
    ),
    "forecast_missing": (
        "Previsione incompleta per questo indicatore",
        "Forecast incomplete for this indicator",
    ),
    "no_rain_window": (
        "Non è prevista una finestra di precipitazioni nel periodo coperto",
        "No precipitation window is forecast in the covered period",
    ),
}


def status_attributes(status, *, reason=None, language="en"):
    return {
        "data_status": status,
        "status_reason": reason or status,
        "status_message": MESSAGES.get(reason or status, MESSAGES[status])[
            0 if language.startswith("it") else 1
        ],
    }


def value_status(place, key, value, *, connected=True, language="en"):
    if not connected or place is None:
        return status_attributes("unavailable", language=language)
    if not place.is_fresh():
        return status_attributes("stale", language=language)
    b = place.briefing
    if key == "data_quality" and value == "unknown":
        return status_attributes("coverage_unknown", language=language)
    if key == "data_quality" and value == "stale":
        return status_attributes("source_stale", language=language)
    domain = {
        "lightning_count": "lightning",
        "lightning_distance": "lightning",
        "lightning_detected": "lightning",
        "official_warning": "official_warnings",
        "official_count": "official_warnings",
        "radar_updated": "radar",
    }.get(key)
    quality = b.get("data_quality")
    feed = quality.get(domain) if isinstance(quality, dict) and domain else None
    if isinstance(feed, dict) and feed.get("state") == "stale":
        return status_attributes("source_stale", language=language)
    if value is not None:
        return status_attributes("available", language=language)
    if key in ("eta", "eta_min", "eta_max", "storm_distance"):
        if b.get("situation_phase") in ("quiet", "watch", "impact"):
            return status_attributes("not_applicable", reason="no_approach", language=language)
        if key == "eta" and place.arrival_bound("min_minutes") is not None:
            return status_attributes("not_applicable", reason="range_only", language=language)
    count_field = (
        "official_alert_count"
        if key.startswith("official_warning")
        else "earthquakes_24h"
        if key.startswith("latest_earthquake")
        else None
    )
    if count_field and place.metric(count_field) == 0:
        return status_attributes("not_applicable", language=language)
    if isinstance(feed, dict) and feed.get("coverage", "unknown") == "unknown":
        return status_attributes("coverage_unknown", language=language)
    return status_attributes("missing", language=language)
