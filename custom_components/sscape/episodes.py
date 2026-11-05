"""Opt-in notification grouping by exact IDs, never by spatial similarity."""

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from time import monotonic

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .dashboard import place_map_url
from .hazards import hazards_for
from .notifications import async_notify, target_options
from .targets import allows_target

_LOGGER = logging.getLogger(__name__)


def collect_episodes(places, kinds):
    episodes = {}

    def add(kind, source, native_id, place, details):
        if not isinstance(native_id, str) or not native_id or len(native_id) > 512:
            return
        key = json.dumps([kind, source, native_id], separators=(",", ":"))
        episode = episodes.setdefault(
            key,
            {
                "kind": kind,
                "source": source,
                "native_id": native_id,
                "places": {},
                "details": details,
            },
        )
        episode["places"][place.place_id] = {
            "name": place.name,
            "risk_level": place.risk_level,
            "phase": place.briefing.get("situation_phase"),
            "eta_minutes": place.eta_minutes,
            "time_window": {
                "min_minutes": place.arrival_bound("min_minutes"),
                "max_minutes": place.arrival_bound("max_minutes"),
                "reliability": (place.briefing.get("time_window") or {}).get("reliability")
                if isinstance(place.briefing.get("time_window"), dict)
                else None,
            },
            "details": details,
        }

    for place in places:
        if not place.is_fresh():
            continue
        b, storm = place.briefing, place.briefing.get("nearest_storm")
        quality = b.get("data_quality")
        radar = quality.get("radar") if isinstance(quality, dict) else {}
        radar = radar if isinstance(radar, dict) else {}
        if (
            "storm" in kinds
            and isinstance(storm, dict)
            and b.get("situation_phase") in ("approach", "impact")
            and radar.get("state") != "stale"
        ):
            add("storm", "sscape_tracking", storm.get("id"), place, {})
        evidence = hazards_for(place)
        for kind, collection in (
            ("official", "official_warnings"),
            ("earthquake", "earthquakes"),
            ("thermal", "thermal_detections"),
        ):
            if kind in kinds:
                for item in evidence.get(collection, []):
                    add(kind, item.get("source", "sscape_catalogue"), item.get("id"), place, item)
    return episodes


def _fingerprint(episode):
    # Countdown/observation clocks alone never retrigger a notification.
    value = {
        "kind": episode["kind"],
        "places": {
            key: {name: row.get(name) for name in ("name", "risk_level", "phase", "details")}
            for key, row in episode["places"].items()
        },
        "details": episode["details"] if episode["kind"] == "official" else {},
    }
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class EpisodeNotifications:
    def __init__(self, hass, parent):
        self.hass, self.parent, self.entry = hass, parent, parent.config_entry
        self._task, self.failure_reason = None, None
        self._pending, self._stopping, self._failed_at = {}, False, 0
        # Baseline prevents replay when starting, reconnecting or reloading HA.
        self._seen = {key: _fingerprint(value) for key, value in self._collect().items()}
        self._unsub = parent.async_add_listener(self._updated)
        self.entry.async_on_unload(self.async_shutdown)

    def _collect(self):
        return collect_episodes(
            (
                place
                for place in self.parent.data.places.values()
                if allows_target(self.entry, place.place_id)
            ),
            self.entry.options.get("episode_kinds", ["storm", "official"]),
        )

    @callback
    def _updated(self):
        if (
            self._stopping
            or self.parent.stopping
            or not self.parent.last_update_success
            or (self.failure_reason and monotonic() - self._failed_at < 60)
        ):
            return
        for key, episode in self._collect().items():
            fingerprint = _fingerprint(episode)
            if self._seen.get(key) == fingerprint:
                continue
            if len(self._pending) >= 64 and key not in self._pending:
                self.failure_reason, self._failed_at = "notification_queue_full", monotonic()
                break
            self._seen[key], self._pending[key] = fingerprint, episode
        while len(self._seen) > 256:
            self._seen.pop(next(iter(self._seen)))
        if self._pending and (self._task is None or self._task.done()):
            self._task = self.entry.async_create_background_task(
                self.hass, self._deliver(), "sscape episode notification"
            )

    def _payload(self, key, episode):
        italian = self.hass.config.language.startswith("it")
        titles = {
            "storm": ("Temporale in avvicinamento", "Approaching storm"),
            "official": ("Allerta ufficiale aggiornata", "Official warning updated"),
            "earthquake": ("Evento sismico recente", "Recent earthquake"),
            "thermal": ("Rilevamento termico satellitare", "Satellite thermal detection"),
        }
        lines = []
        for row in episode["places"].values():
            eta, interval = row.get("eta_minutes"), row.get("time_window") or {}
            details = row.get("details", {})
            if episode["kind"] == "storm" and row.get("phase") == "impact":
                detail = "Possibile interessamento in corso" if italian else "Possible impact now"
            elif (
                episode["kind"] == "storm"
                and interval.get("reliability") in ("stable", "variable")
                and interval.get("min_minutes") is not None
                and interval.get("max_minutes") is not None
                and interval["min_minutes"] <= interval["max_minutes"]
            ):
                detail = f"{interval['min_minutes']}–{interval['max_minutes']} min"
            elif episode["kind"] == "storm" and eta is not None:
                detail = f"{eta} min"
            elif episode["kind"] == "official":
                detail = details.get("headline") or details.get("type") or ""
                if details.get("covers_point") is False:
                    detail += " · nelle vicinanze" if italian else " · nearby area"
                elif details.get("covers_point") is not True:
                    detail += " · nell’area monitorata" if italian else " · in the monitored area"
            elif episode["kind"] == "earthquake":
                detail = f"M{details['magnitude']} · {details['distance_km']:g} km"
            else:
                detail = ("Anomalia termica" if italian else "Thermal detection") + (
                    f" · {details['distance_km']:g} km"
                    if details.get("distance_km") is not None
                    else ""
                )
            lines.append(f"{row['name']}: {detail}".rstrip(": "))
        if episode["kind"] == "thermal":
            lines.append(
                "Non conferma un incendio sul luogo."
                if italian
                else "Not confirmation of a fire at the place."
            )
        title = "sScape · " + titles[episode["kind"]][0 if italian else 1]
        if episode["kind"] == "storm" and any(
            row.get("phase") == "impact" for row in episode["places"].values()
        ):
            title = "sScape · " + (
                "Temporale sull’area monitorata" if italian else "Storm over the monitored area"
            )
        tag = (
            "sscape-" + hashlib.sha256((self.entry.unique_id + "|" + key).encode()).hexdigest()[:24]
        )
        place = self.parent.data.places.get(next(iter(episode["places"])))
        return {
            "title": title,
            "message": "\n".join(lines)[:3500],
            "data": {"tag": tag, "group": "sscape", "url": place_map_url(self.parent, place)},
        }

    async def _deliver(self):
        while self._pending and not self._stopping:
            key = next(iter(self._pending))
            episode = self._pending.pop(key)
            target = (
                "entity:" + self.entry.options["notify_entity_id"]
                if self.entry.options.get("notify_entity_id")
                else "service:" + self.entry.options.get("notify_service", "")
            )
            try:
                target_options(self.hass, target)
            except HomeAssistantError:
                self.failure_reason, self._failed_at = (
                    "notification_target_unavailable",
                    monotonic(),
                )
                self._seen.pop(key, None)
                continue
            for attempt in range(3):
                current = self._collect().get(key)
                if current is None or not self.parent.last_update_success or self.parent.stopping:
                    break
                episode = current
                try:
                    await async_notify(self.hass, self.entry.options, self._payload(key, episode))
                    self.failure_reason = None
                    break
                except (HomeAssistantError, TimeoutError):
                    self.failure_reason, self._failed_at = (
                        "notification_delivery_failed",
                        monotonic(),
                    )
                    if attempt < 2:
                        await asyncio.sleep(attempt + 1)
                    else:
                        self._seen.pop(key, None)
                        _LOGGER.warning("sScape episode notification delivery failed")

    async def async_shutdown(self):
        self._stopping = True
        self._pending.clear()
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._task is not None and self._task is not asyncio.current_task():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task


def setup_episode_notifications(hass, parent):
    if parent.config_entry.options.get("episode_notifications", False):
        parent.episode_notifications = EpisodeNotifications(hass, parent)
