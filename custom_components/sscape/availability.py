"""A local diagnostic remains readable when meteorological entities go offline."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from .entity import SScapeEntity
from .targets import allows_target

LABELS = {
    "ready": ("Collegamento attivo", "Connection active"),
    "disconnected": (
        "Non riesco a contattare sScape. Riproverò automaticamente.",
        "Cannot reach sScape. Will retry automatically.",
    ),
    "assessment_expired": (
        "L’analisi è scaduta. Non usarla come valutazione attuale.",
        "The assessment has expired. Do not use it as a current assessment.",
    ),
    "assessment_missing": (
        "sScape risponde, ma non restituisce un’analisi per questo luogo.",
        "sScape responds but provides no assessment for this target.",
    ),
    "reauthentication_required": (
        "Aggiorna la chiave di accesso nelle impostazioni sScape.",
        "Update the access key in sScape settings.",
    ),
    "rate_limited": (
        "Limite di richieste raggiunto. Attendo il tempo indicato dal server.",
        "Request limit reached. Waiting for the server's retry interval.",
    ),
}


class SScapeAvailability(SScapeEntity, SensorEntity):
    _attr_translation_key = "connection_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(LABELS)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-check-outline"

    def __init__(self, parent, place):
        super().__init__(parent, place, "connection_status")

    @property
    def available(self):
        return not self.coordinator.stopping and allows_target(
            self.coordinator.config_entry, self.place_id
        )

    @property
    def native_value(self):
        reason = self.coordinator.failure_reason
        if reason in ("authentication_failed", "access_denied"):
            return "reauthentication_required"
        if reason == "rate_limited":
            return "rate_limited"
        if not self.coordinator.last_update_success:
            return "disconnected"
        if not self.place:
            return "assessment_missing"
        return "ready" if self.place.is_fresh() else "assessment_expired"

    @property
    def extra_state_attributes(self):
        return {
            "status_message": LABELS[self.native_value][
                0 if self.hass.config.language.startswith("it") else 1
            ],
            "scope": "connection_and_assessment_availability_not_weather_safety",
        }
