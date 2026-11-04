"""Hourly model timing, not new forecasts or estimates of exact event onset."""

from datetime import UTC, datetime, timedelta

from .api import _time
from .forecast import number

HOUR = timedelta(hours=1)


def make_outlook(snapshot, *, hours=6, now=None):
    """Keep gaps unknown; only comparable one-hour gust maxima enter a peak."""
    if type(hours) is not int or not 1 <= hours <= 24:
        raise ValueError("Choose a horizon from 1 to 24 hours")
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("A timezone-aware reference time is required")
    now = now.astimezone(UTC)
    if snapshot is None or not snapshot.is_fresh():
        return {"available": False, "reason": "forecast_unavailable"}
    raw = snapshot.raw
    start = now.replace(minute=0, second=0, microsecond=0)
    declared_start = _time(raw.get("start"))
    if declared_start:
        if declared_start != declared_start.replace(minute=0, second=0, microsecond=0):
            return {"available": False, "reason": "invalid_forecast_period"}
        if declared_start > now + HOUR:
            return {"available": False, "reason": "forecast_not_yet_valid"}
        # The sScape API starts at the NEXT complete forecast hour. It does
        # not contain the partly elapsed current hour; never fabricate it.
        start = max(start, declared_start)
    requested_end = max(now, start) + timedelta(hours=hours)
    end = requested_end.replace(minute=0, second=0, microsecond=0)
    if end < requested_end:
        end += HOUR
    slots = [start + HOUR * i for i in range(1, int((end - start) / HOUR) + 1)]
    indexed, conflicts = {}, set()
    rows = raw.get("hours")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        stamp, beginning = _time(row.get("time")), _time(row.get("period_start"))
        if stamp not in slots or beginning != stamp - HOUR or stamp in conflicts:
            continue
        if stamp in indexed and row != indexed[stamp]:
            indexed.pop(stamp)
            conflicts.add(stamp)
        else:
            indexed[stamp] = row

    samples = [indexed.get(stamp, {}) for stamp in slots]
    amounts = [number(row.get("precipitation_mm"), nonnegative=True) for row in samples]
    rain_complete = all(value is not None for value in amounts)
    windows = []
    first_index = None
    for index, (stamp, amount) in enumerate(zip(slots, amounts, strict=True)):
        if amount is None or amount <= 0:
            continue
        if first_index is None:
            first_index = index
        beginning = (stamp - HOUR).isoformat()
        if windows and windows[-1]["end"] == beginning:
            windows[-1]["end"] = stamp.isoformat()
            windows[-1]["amount_mm"] += amount
        else:
            windows.append({"start": beginning, "end": stamp.isoformat(), "amount_mm": amount})
    first_window = (
        windows[0]
        if first_index is not None and all(value is not None for value in amounts[:first_index])
        else None
    )
    gusts = [
        number(row.get("gust_kmh"), nonnegative=True)
        if _time(row.get("gust_period_start")) == stamp - HOUR
        else None
        for stamp, row in zip(slots, samples, strict=True)
    ]
    gust_complete = all(value is not None for value in gusts)
    gust_index = max(range(len(gusts)), key=gusts.__getitem__) if gust_complete else None
    temperatures = [number(row.get("temperature_c")) for row in samples]
    temperature_complete = all(value is not None for value in temperatures)
    cold_index = (
        min(range(len(temperatures)), key=temperatures.__getitem__)
        if temperature_complete
        else None
    )
    snow = [number(row.get("snowfall_cm"), nonnegative=True) for row in samples]
    snow_complete = all(value is not None for value in snow)
    visibility = [number(row.get("visibility_m"), nonnegative=True) for row in samples]
    visibility_complete = all(value is not None for value in visibility)
    visibility_index = (
        min(range(len(visibility)), key=visibility.__getitem__) if visibility_complete else None
    )
    return {
        "available": True,
        "data_kind": "model_forecast",
        "horizon_hours": hours,
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "includes_partial_current_hour": now > start,
        "coverage_starts_after_now": start > now,
        "temporal_resolution": "hourly",
        "precipitation": {
            "status": "expected" if windows else "not_expected" if rain_complete else "unknown",
            "complete": rain_complete,
            "first_window": first_window,
            "windows": windows,
            "amount_period": "whole_forecast_hours_not_remaining_rainfall",
        },
        "wind_gust": {
            "complete": gust_complete,
            "peak_kmh": gusts[gust_index] if gust_index is not None else None,
            "period_start": (slots[gust_index] - HOUR).isoformat()
            if gust_index is not None
            else None,
            "period_end": slots[gust_index].isoformat() if gust_index is not None else None,
        },
        "temperature": {
            "complete": temperature_complete,
            "minimum_c": temperatures[cold_index] if cold_index is not None else None,
            "time": slots[cold_index].isoformat() if cold_index is not None else None,
            "sampling": "hourly_air_temperature_not_ground_frost",
        },
        "snowfall": {
            "complete": snow_complete,
            "total_cm": sum(snow) if snow_complete else None,
            "amount_period": "whole_forecast_hours_not_remaining_snowfall",
        },
        "visibility": {
            "complete": visibility_complete,
            "minimum_m": visibility[visibility_index] if visibility_index is not None else None,
            "time": slots[visibility_index].isoformat() if visibility_index is not None else None,
        },
        "model": raw.get("model"),
        "fetched_at": raw.get("fetched_at"),
        "sources": raw.get("sources"),
        "source_details": raw.get("source_details"),
    }
