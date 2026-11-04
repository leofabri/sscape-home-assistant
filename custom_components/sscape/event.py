"""Momentary, deduplicated changes; never replay an outage as an all-clear."""

from homeassistant.components.event import EventEntity

from .entity import SScapeEntity, async_watch_places

EVENTS = [
    "risk_increased",
    "risk_decreased",
    "storm_approaching",
    "storm_moving_away",
    "official_warning_changed",
    "data_degraded",
    "data_recovered",
]
RANK = {"low": 0, "moderate": 1, "high": 2, "severe": 3}


async def async_setup_entry(hass, entry, async_add_entities):
    async_watch_places(
        hass, entry, async_add_entities, lambda parent, place: [SScapeChanges(parent, place)]
    )


class SScapeChanges(SScapeEntity, EventEntity):
    _attr_translation_key = "conditions_changed"
    _attr_event_types = EVENTS
    _attr_icon = "mdi:bell-outline"

    def __init__(self, parent, place):
        super().__init__(parent, place, "conditions_changed")
        self._previous = None

    def _fingerprint(self):
        if not self.available or not self.place:
            return None
        b = self.place.briefing
        return (
            self.place.risk_level,
            b.get("situation_phase"),
            self.place.signal("official_warning"),
            b.get("official_alert_severity"),
            b.get("data_quality_status"),
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._previous = self._fingerprint()

    def _handle_coordinator_update(self):
        current, previous = self._fingerprint(), self._previous
        self._previous = current
        event = None
        if current and previous and current != previous:
            risk, phase, warning, severity, quality = current
            old_risk, old_phase, old_warning, old_severity, old_quality = previous
            if risk in RANK and old_risk in RANK and RANK[risk] > RANK[old_risk]:
                event = "risk_increased"
            elif (
                risk in RANK
                and old_risk in RANK
                and RANK[risk] < RANK[old_risk]
                and quality not in ("stale", "unknown")
            ):
                event = "risk_decreased"
            elif phase == "approach" and old_phase != phase:
                event = "storm_approaching"
            elif old_phase == "approach" and (self.place.briefing.get("nearest_storm") or {}).get(
                "moving_away_from_place"
            ):
                event = "storm_moving_away"
            elif (
                warning is not None
                and old_warning is not None
                and (warning, severity) != (old_warning, old_severity)
            ):
                event = "official_warning_changed"
            elif quality == "stale" and old_quality != "stale":
                event = "data_degraded"
            elif old_quality == "stale" and quality == "partial":
                event = "data_recovered"
        if event:
            self._trigger_event(
                event,
                {
                    "risk_level": current[0],
                    "situation_phase": current[1],
                    "assessment_time": self.place.briefing.get("updated_at"),
                    "data_quality": current[4],
                },
            )
        super()._handle_coordinator_update()
