"""A station reading is not a measurement at the monitored address."""

import math
from datetime import UTC, datetime, timedelta

from .api import _time

SPECS = {
    "air_temperature": ("Cel", -100, 65, 60),
    "relative_humidity": ("%", 0, 100, 60),
    "wind_speed": ("m/s", 0, 150, 20),
    "wind_gust": ("m/s", 0, 150, 20),
}


def valid_reading(context, variable):
    if not isinstance(context, dict) or variable not in SPECS:
        return None
    rows = context.get("readings")
    if not isinstance(rows, list):
        return None
    unit, lower, upper, minutes = SPECS[variable]
    now = datetime.now(UTC)
    for row in rows[:12]:
        if not isinstance(row, dict) or not isinstance(row.get("observation"), dict):
            continue
        obs = row["observation"]
        if obs.get("variable") != variable:
            continue
        value, stamp = obs.get("value"), _time(obs.get("observation_time"))
        distance = row.get("distance_km")
        if (
            obs.get("kind") != "surface_observation"
            or obs.get("unit") != unit
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or not lower <= value <= upper
            or not stamp
            or not -timedelta(seconds=60) <= now - stamp <= timedelta(minutes=minutes)
            or not isinstance(obs.get("quality"), dict)
            or obs["quality"].get("status") != "valid"
            or not obs.get("attribution")
            or not obs.get("license_url")
            or not obs.get("source_id")
            or type(distance) not in (int, float)
            or not math.isfinite(distance)
            or not 0 <= distance <= 50
            or row.get("scope") not in ("nearby", "regional")
        ):
            return None
        return row
    return None
