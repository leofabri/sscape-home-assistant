"""Current place-scoped evidence, without inventing IDs or impact assessments."""

from datetime import UTC, datetime, timedelta

from .api import _text, _time
from .forecast import number


def hazards_for(place, *, now=None):
    now = now or datetime.now(UTC)
    if not place.is_fresh(now):
        return {"available": False, "reason": "assessment_unavailable"}
    b = place.briefing
    warnings, earthquakes, thermal = [], [], []
    expiry = _time(b.get("official_alert_expires"))
    if (place.metric("official_alert_count") or 0) > 0 and (expiry is None or expiry > now):
        warnings.append(
            {
                "id": _text(b.get("official_alert_id"), 256),
                "source": _text(b.get("official_alert_source"), 128),
                "sender": _text(b.get("official_alert_sender"), 255),
                "area": _text(b.get("official_alert_area"), 500),
                "headline": _text(b.get("official_alert_headline"), 500),
                "type": _text(b.get("official_alert_type"), 80),
                "severity": _text(b.get("official_alert_severity"), 40),
                "starts_at": b.get("official_alert_onset")
                if _time(b.get("official_alert_onset"))
                else None,
                "expires_at": expiry.isoformat() if expiry else None,
                "covers_point": b.get("official_alert_covers_point")
                if type(b.get("official_alert_covers_point")) is bool
                else None,
                "scope": "warning_area_intersects_monitored_radius",
                "official": True,
            }
        )
    for row in (
        (b.get("recent_earthquakes_24h") or [])[:30]
        if isinstance(b.get("recent_earthquakes_24h", []), list)
        else []
    ):
        if not isinstance(row, dict):
            continue
        stamp, magnitude = _time(row.get("occurred_at")), number(row.get("magnitude"))
        distance = number(row.get("distance_km"), nonnegative=True)
        if (
            not stamp
            or not -timedelta(seconds=60) <= now - stamp <= timedelta(hours=24)
            or magnitude is None
            or distance is None
        ):
            continue
        earthquakes.append(
            {
                "id": _text(row.get("id"), 256),
                "occurred_at": stamp.isoformat(),
                "magnitude": magnitude,
                "distance_km": distance,
                "depth_km": number(row.get("depth_km"), nonnegative=True),
                "area": _text(row.get("area"), 255),
                "kind": "earthquake_catalogue_event",
                "damage_confirmed": False,
            }
        )
    for row in (
        (b.get("environmental_signals") or [])[:30]
        if isinstance(b.get("environmental_signals", []), list)
        else []
    ):
        if not isinstance(row, dict) or row.get("kind") != "fire":
            continue
        stamp = _time(row.get("observed_at"))
        distance = number(row.get("distance_km"), nonnegative=True)
        if (
            not stamp
            or not -timedelta(seconds=60) <= now - stamp <= timedelta(hours=24)
            or distance is None
        ):
            continue
        thermal.append(
            {
                "id": _text(row.get("id"), 256),
                "source": _text(row.get("source"), 128),
                "observed_at": stamp.isoformat(),
                "distance_km": distance,
                "frp_mw": number(row.get("frp_mw"), nonnegative=True),
                "kind": "thermal_detection",
                "confirmed_fire": False,
            }
        )
    earthquakes.sort(key=lambda item: item["occurred_at"], reverse=True)
    thermal.sort(key=lambda item: item["observed_at"], reverse=True)
    return {
        "available": True,
        "official_warnings": warnings,
        "earthquakes": earthquakes,
        "thermal_detections": thermal,
        "official_warning_count": place.metric("official_alert_count"),
        "earthquake_count": place.metric("earthquakes_24h"),
        "catalogues_complete": False,
        "assessment_time": b.get("updated_at"),
    }
