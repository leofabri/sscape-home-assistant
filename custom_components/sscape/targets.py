"""Explicit local coordinate targets; no server-side saved-place mutation."""

import math
import re


def validate_point(point):
    if not isinstance(point, dict):
        raise ValueError("Invalid coordinate target")
    for field, low, high in (
        ("latitude", -90, 90),
        ("longitude", -180, 180),
        ("radius_km", 1, 1000),
    ):
        value = point.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError("Invalid coordinate target")
    if (
        not isinstance(point.get("id"), str)
        or not re.fullmatch(r"point-[a-zA-Z0-9_-]+", point["id"])
        or len(point["id"]) > 80
    ):
        raise ValueError("Invalid coordinate identity")
    if (
        not isinstance(point.get("name"), str)
        or not point["name"].strip()
        or len(point["name"]) > 120
    ):
        raise ValueError("Invalid coordinate name")
    if type(point.get("enabled", True)) is not bool:
        raise ValueError("Invalid coordinate enabled state")
    return point


def points_for(entry):
    points = entry.options.get("coordinate_targets", [])
    if not isinstance(points, list) or len(points) > 10:
        raise ValueError("At most ten coordinate targets can be configured")
    result = {}
    seen = set()
    for raw in points:
        point = validate_point(raw)
        if point["id"] in seen:
            raise ValueError("Duplicate coordinate identity")
        seen.add(point["id"])
        if point.get("enabled", True):
            result[point["id"]] = point
    return result


def allows_target(entry, place_id):
    if place_id.startswith("point-"):
        return place_id in points_for(entry)
    if entry.options.get("coordinate_only", False):
        return False
    return (
        place_id in points_for(entry)
        or entry.options.get("follow_all_places", False)
        or place_id in entry.options.get("places", [])
    )
