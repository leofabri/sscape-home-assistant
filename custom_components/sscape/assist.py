"""Built-in Assist sentences with place scope, exposure and HA read permissions."""

from homeassistant.auth.permissions.const import POLICY_READ
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from .api import _time
from .const import DOMAIN
from .hazards import hazards_for
from .planning import make_outlook
from .targets import allows_target

DATA_ASSIST = "sscape_assist"
SENTENCES = {
    "briefing": ["sscape (situazione|rischio) [a|per] {place}", "sscape briefing [at|for] {place}"],
    "alerts": ["sscape allerte [a|per] {place}", "sscape alerts [at|for] {place}"],
    "rain": ["sscape quando piove [a|per] {place}", "sscape rain forecast [at|for] {place}"],
    "cold": ["sscape ora più fredda [a|per] {place}", "sscape coldest hour [at|for] {place}"],
    "snow": ["sscape neve [a|per] {place}", "sscape snow forecast [at|for] {place}"],
}


def _normal(value):
    return " ".join(str(value).casefold().split())


def _clock(value):
    stamp = _time(value)
    return dt_util.as_local(stamp).strftime("%H:%M") if stamp else ""


class SScapeAssist:
    def __init__(self, hass):
        self.hass, self.parents, self._unsubs = hass, {}, []

    def add(self, parent):
        from homeassistant.components.conversation.agent_manager import get_agent_manager

        self.parents[parent.config_entry.entry_id] = parent
        if self._unsubs:
            return
        for kind, sentences in SENTENCES.items():

            async def answer(user_input, result, kind=kind):
                slot = result.entities.get("place")
                return await self.answer(user_input, slot.value if slot else "", kind)

            manager = get_agent_manager(self.hass)
            register = manager.register_trigger
            if hasattr(manager, "triggers_details"):  # HA 2026.2 API
                from homeassistant.components.conversation.trigger import TriggerDetails

                unsubscribe = register(TriggerDetails(sentences=sentences, callback=answer))
            else:
                unsubscribe = register(sentences, answer)
            self._unsubs.append(unsubscribe)

    def remove(self, parent):
        self.parents.pop(parent.config_entry.entry_id, None)
        if not self.parents:
            while self._unsubs:
                self._unsubs.pop()()

    async def _allowed(self, parent, place, user_input, kind):
        from homeassistant.components.homeassistant.exposed_entities import async_should_expose

        key, domain = (
            ("risk", "sensor") if kind in ("briefing", "alerts") else ("forecast", "weather")
        )
        entity = er.async_get(self.hass).async_get_entity_id(
            domain, DOMAIN, f"{parent.config_entry.unique_id}|{place.place_id}|{key}"
        )
        if not entity:
            return False
        try:
            if not async_should_expose(self.hass, "conversation", entity):
                return False
        except KeyError:
            return False
        user_id = user_input.context.user_id
        if not user_id:
            return parent.config_entry.options.get("assist_allow_anonymous", False)
        user = await self.hass.auth.async_get_user(user_id)
        return bool(user and user.is_active and user.permissions.check_entity(entity, POLICY_READ))

    async def answer(self, user_input, spoken_place, kind):
        italian = user_input.language.startswith("it")
        denied = (
            "Luogo non disponibile per questa richiesta."
            if italian
            else "This place is not available for this request."
        )
        candidates = []
        for parent in self.parents.values():
            if parent.stopping:
                continue
            entry = parent.config_entry
            for place in parent.data.places.values():
                aliases = [
                    place.name,
                    *entry.options.get("assist_aliases", {}).get(place.place_id, []),
                ]
                if _normal(spoken_place) not in {
                    _normal(name) for name in aliases
                } or not allows_target(entry, place.place_id):
                    continue
                if await self._allowed(parent, place, user_input, kind):
                    candidates.append((parent, place))
        if len(candidates) != 1:
            return denied
        parent, place = candidates[0]
        if not parent.last_update_success or not place.is_fresh():
            return (
                "L’analisi non è al momento disponibile."
                if italian
                else "The current assessment is unavailable."
            )
        if kind == "briefing":
            return " ".join(
                str(value)
                for value in (place.headline, place.attributes.get("recommended_action"))
                if value
            )[:600]
        if kind == "alerts":
            warnings = hazards_for(place).get("official_warnings", [])
            if not warnings:
                return (
                    "Non ho un’allerta verificata per quest’area."
                    if italian
                    else "I do not have a verified warning for this area."
                )
            warning = warnings[0]
            text = (
                warning.get("headline")
                or warning.get("type")
                or ("Allerta ufficiale" if italian else "Official warning")
            )
            if warning.get("expires_at"):
                text += (". Valida fino alle " if italian else ". Valid until ") + _clock(
                    warning["expires_at"]
                )
            if warning.get("covers_point") is False:
                text += (
                    ". L’area di allerta è nelle vicinanze, non comprende il punto."
                    if italian
                    else ". The warning area is nearby, not over the point."
                )
            return text
        weather = parent.weather_coordinators.get(place.place_id)
        if weather is None or not weather.last_update_success:
            return "Previsione non disponibile." if italian else "Forecast unavailable."
        plan = make_outlook(weather.data, hours=6 if kind == "rain" else 24)
        if not plan["available"]:
            return "Previsione non disponibile." if italian else "Forecast unavailable."
        if kind == "rain":
            rain = plan["precipitation"]
            if rain["status"] == "not_expected":
                return (
                    (
                        "Non sono previste precipitazioni nella fascia "
                        if italian
                        else "No precipitation is forecast in the window "
                    )
                    + _clock(plan["period_start"])
                    + "–"
                    + _clock(plan["period_end"])
                )
            if window := rain["first_window"]:
                return (
                    (
                        "Precipitazioni previste nella fascia "
                        if italian
                        else "Precipitation forecast in the window "
                    )
                    + _clock(window["start"])
                    + "–"
                    + _clock(window["end"])
                )
            return (
                "Non ho una fascia di inizio affidabile: mancano alcune ore di previsione."
                if italian
                else "I cannot establish the first window because some forecast hours are missing."
            )
        if kind == "snow":
            amount = plan["snowfall"]["total_cm"]
            if amount is None:
                return (
                    "Quantità di neve prevista non disponibile."
                    if italian
                    else "Snowfall amount unavailable."
                )
            return (
                f"Neve prevista: {amount:g} centimetri nella finestra di 24 ore."
                if italian
                else f"Forecast snowfall: {amount:g} centimetres in the 24-hour window."
            )
        temperature = plan["temperature"]
        if temperature["minimum_c"] is None:
            return (
                "Minima oraria prevista non disponibile."
                if italian
                else "Hourly forecast minimum unavailable."
            )
        return (
            f"Minima oraria prevista: {temperature['minimum_c']:g} gradi Celsius alle "
            if italian
            else f"Forecast hourly minimum: {temperature['minimum_c']:g} degrees Celsius at "
        ) + _clock(temperature["time"])


async def setup_assist(hass, parent):
    if not parent.config_entry.options.get("assist_enabled", False):
        return
    if not await async_setup_component(hass, "conversation", {}):
        return
    manager = hass.data.setdefault(DATA_ASSIST, SScapeAssist(hass))
    manager.add(parent)
    parent.assist_manager = manager
    parent.config_entry.async_on_unload(lambda: manager.remove(parent))
