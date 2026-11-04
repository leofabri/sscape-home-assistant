"""Validated model output, never reinterpreted as a surface observation."""

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .api import _time


def number(value, *, nonnegative=False):
    if type(value) not in (int, float) or not math.isfinite(value) or (nonnegative and value < 0):
        return None
    return value


def condition(code, is_day=None):
    if type(code) is not int:
        return None
    if code == 0:
        return ("sunny" if is_day else "clear-night") if type(is_day) is bool else None
    if code in (1, 2):
        return "partlycloudy"
    if code == 3:
        return "cloudy"
    if code in (45, 48):
        return "fog"
    if code in (51, 53, 55, 56, 57, 61, 63, 66, 67, 80, 81):
        return "rainy"
    if code in (65, 82):
        return "pouring"
    if code in (71, 73, 75, 77, 85, 86):
        return "snowy"
    if code in (95, 96, 99):
        return "lightning-rainy"
    return None


@dataclass(frozen=True)
class WeatherSnapshot:
    raw: dict[str, Any]

    @classmethod
    def parse(cls, value):
        return cls(value if isinstance(value, dict) else {})

    @property
    def current(self):
        value = self.raw.get("current")
        return value if isinstance(value, dict) else {}

    def _current_time_valid(self):
        stamp = _time(self.current.get("time"))
        return bool(
            stamp and -timedelta(seconds=60) <= datetime.now(UTC) - stamp < timedelta(minutes=90)
        )

    def is_fresh(self):
        now = datetime.now(UTC)
        stamp = _time(self.raw.get("fetched_at"))
        sources = self.raw.get("sources")
        details = self.raw.get("source_details")
        attributed = (
            {
                item.get("id")
                for item in details
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and isinstance(item.get("attribution"), str)
                and item.get("attribution")
                and isinstance(item.get("license_url"), str)
                and item.get("license_url")
            }
            if isinstance(details, list)
            else set()
        )
        return bool(
            self.raw.get("kind") == "model_forecast"
            and self.raw.get("state") in ("available", "partial")
            and isinstance(sources, list)
            and sources
            and all(isinstance(v, str) and v for v in sources)
            and set(sources).issubset(attributed)
            and stamp
            and -timedelta(seconds=60) <= now - stamp <= timedelta(hours=1)
        )

    def current_number(self, key):
        if not self.is_fresh() or not self._current_time_valid():
            return None
        return number(self.current.get(key), nonnegative=key != "temperature_c")

    @property
    def condition(self):
        if not self.is_fresh() or not self._current_time_valid():
            return None
        return condition(self.current.get("weather_code"), self.current.get("is_day"))

    def hourly(self):
        if not self.is_fresh():
            return []
        rows = self.raw.get("hours")
        if not isinstance(rows, list):
            return []
        result = []
        for row in rows[:48]:
            if not isinstance(row, dict):
                continue
            stamp = _time(row.get("time"))
            if not stamp or stamp <= datetime.now(UTC):
                continue
            item = {"datetime": row["time"]}
            for source, target in (
                ("temperature_c", "native_temperature"),
                ("wind_kmh", "native_wind_speed"),
                ("precipitation_mm", "native_precipitation"),
            ):
                value = number(row.get(source), nonnegative=source != "temperature_c")
                if value is not None:
                    item[target] = value
            if (mapped := condition(row.get("weather_code"), row.get("is_day"))) is not None:
                item["condition"] = mapped
            gust_start = _time(row.get("gust_period_start"))
            period_start = _time(row.get("period_start"))
            gust = number(row.get("gust_kmh"), nonnegative=True)
            if (
                gust is not None
                and gust_start == period_start
                and gust_start
                and stamp - gust_start == timedelta(hours=1)
            ):
                item["native_wind_gust_speed"] = gust
            if len(item) > 1:
                result.append(item)
        return result
