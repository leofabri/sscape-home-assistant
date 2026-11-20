"""Private mobile-location links. No entity or Recorder state contains the results."""

import asyncio
import json
import math
import re
from datetime import timedelta
from time import monotonic

import aiohttp
import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DEFAULT_HOST

DATA = "sscape_mobile_location"
FIX_LIFETIME = timedelta(minutes=8)
SEND_INTERVAL = 60
SECRET = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def mobile_report_signal(entry_id):
    # Mobile App's location webhook emits this registration-bound signal.
    # Do not load the entire optional integration just to get the constant.
    return f"mobile_app_location_update_{entry_id}"


class MobileLinkError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _mobile_entry(hass, entry_id, owner):
    entry = hass.config_entries.async_get_entry(entry_id)
    if (
        entry is not None
        and entry.domain == "mobile_app"
        and entry.data.get("user_id") == owner
        and entry.disabled_by is None
    ):
        return entry
    return None


def _gps_fix(report, when):
    gps = report.get("gps")
    accuracy = report.get("gps_accuracy")
    if not isinstance(gps, (tuple, list)) or len(gps) != 2:
        return None
    lat, lon = gps
    if not all(
        type(number) in (int, float) and math.isfinite(number) for number in (lat, lon, accuracy)
    ):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 < accuracy <= 250):
        return None
    # The Companion API provides no fix timestamp in its location payload.
    # Use receipt of a real GPS report, never a restored tracker state.
    return {
        "latitude": lat,
        "longitude": lon,
        "accuracy_m": accuracy,
        "observed_at": when.isoformat(),
    }


class MobileLinks:
    def __init__(self, hass):
        self.hass = hass
        self.store = Store(hass, 1, DATA, private=True)
        self.links = {}
        self.pending = {}
        self.unsubscribe = {}
        self.scheduled = {}
        self.tasks = {}
        self.last_sent = {}
        self.results = {}
        self.last_notice = {}
        self.errors = {}
        self.lock = asyncio.Lock()

    async def load(self):
        self.links = (await self.store.async_load() or {}).get("links", {})
        for owner, link in self.links.items():
            self.listen(owner, link)

    async def request(self, method, path, *, payload=None, token=None):
        # This panel uses official sScape only. Never take a URL or credential
        # destination from a websocket message or follow redirects.
        url = DEFAULT_HOST + "/api/v1/home-assistant/location/" + path
        headers = {"Accept-Language": self.hass.config.language}
        if token:
            headers["Authorization"] = "Bearer " + token
        session = async_get_clientsession(self.hass)
        try:
            async with (
                asyncio.timeout(30),
                session.request(
                    method, url, json=payload, headers=headers, allow_redirects=False
                ) as response,
            ):
                if response.status == 204:
                    return {}
                if response.status != 200 and response.status != 201:
                    raise MobileLinkError(
                        {401: "not_authorized", 409: "old_report", 422: "invalid_fix"}.get(
                            response.status, "server_unavailable"
                        )
                    )
                body = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    body.extend(chunk)
                    if len(body) > 32_768:
                        raise MobileLinkError("server_unavailable")
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise MobileLinkError("server_unavailable")
                return value
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise MobileLinkError("server_unavailable") from None

    def status(self, owner):
        link = self.links.get(owner)
        choices = [
            {"id": entry.entry_id, "name": entry.title}
            for entry in self.hass.config_entries.async_entries("mobile_app")
            if _mobile_entry(self.hass, entry.entry_id, owner) is not None
        ]
        result = self.results.get(owner)
        if result:
            expiry = dt_util.parse_datetime(result.get("expires_at", ""))
            if expiry is None or expiry <= dt_util.utcnow():
                self.results.pop(owner, None)
                result = None
        return {
            "choices": choices,
            "linked": link is not None,
            "device": link["device"] if link else None,
            "use_in_home_assistant": link["use_in_home_assistant"] if link else False,
            "use_in_sscape": link["use_in_sscape"] if link else False,
            "error": self.errors.get(owner),
            "briefing": result,
        }

    async def link(self, owner, device, code):
        async with self.lock:
            if _mobile_entry(self.hass, device, owner) is None:
                raise MobileLinkError("device_not_owned")
            if owner in self.links or not SECRET.fullmatch(code):
                raise MobileLinkError("invalid_link")
            reply = await self.request("POST", "link", payload={"code": code})
            token = reply.get("token")
            scopes = [reply.get("use_in_home_assistant"), reply.get("use_in_sscape")]
            if (
                not isinstance(token, str)
                or not SECRET.fullmatch(token)
                or not all(type(v) is bool for v in scopes)
                or not any(scopes)
            ):
                raise MobileLinkError("server_unavailable")
            if _mobile_entry(self.hass, device, owner) is None:
                await self.request("DELETE", "link", token=token)
                raise MobileLinkError("device_not_owned")
            link = {
                "device": device,
                "token": token,
                "use_in_home_assistant": scopes[0],
                "use_in_sscape": scopes[1],
            }
            updated = {**self.links, owner: link}
            try:
                await self.store.async_save({"links": updated})
            except OSError:
                await self.request("DELETE", "link", token=token)
                raise MobileLinkError("server_unavailable") from None
            self.links = updated
            self.errors.pop(owner, None)
            self.listen(owner, link)

    async def unlink(self, owner):
        async with self.lock:
            if not (link := self.links.get(owner)):
                return
            self.stop(owner)
            # Stop survives even if Home Assistant is restarted during a
            # failed network call. A disconnect attempt never restarts GPS.
            self.links = {**self.links, owner: {**link, "paused": True}}
            try:
                await self.store.async_save({"links": self.links})
            except (OSError, HomeAssistantError):
                raise MobileLinkError("server_unavailable") from None
            try:
                await self.request("DELETE", "link", token=link["token"])
            except MobileLinkError as err:
                if err.code != "not_authorized":
                    self.errors[owner] = "revocation_pending"
                    raise
            updated = {key: value for key, value in self.links.items() if key != owner}
            try:
                await self.store.async_save({"links": updated})
            except (OSError, HomeAssistantError):
                raise MobileLinkError("server_unavailable") from None
            self.links = updated

    @callback
    def listen(self, owner, link):
        if link.get("paused"):
            return

        @callback
        def report(data):
            if _mobile_entry(self.hass, link["device"], owner) is None:
                self.stop(owner)
                self.errors[owner] = "device_not_owned"
                return
            fix = _gps_fix(data, dt_util.utcnow())
            if fix is None:
                return
            self.pending[owner] = fix
            self.schedule(owner)

        signal = mobile_report_signal(link["device"])
        self.unsubscribe[owner] = async_dispatcher_connect(self.hass, signal, report)

    @callback
    def stop(self, owner):
        for group in (self.unsubscribe, self.scheduled):
            if remove := group.pop(owner, None):
                remove()
        if task := self.tasks.pop(owner, None):
            task.cancel()
        for group in (self.pending, self.last_sent, self.results, self.last_notice, self.errors):
            group.pop(owner, None)

    @callback
    def schedule(self, owner):
        if owner in self.tasks or owner in self.scheduled:
            return
        delay = max(0, self.last_sent.get(owner, -SEND_INTERVAL) + SEND_INTERVAL - monotonic())

        @callback
        def run(_now):
            self.scheduled.pop(owner, None)
            self.tasks[owner] = self.hass.async_create_task(
                self.submit(owner), "sScape private mobile location"
            )

        self.scheduled[owner] = async_call_later(self.hass, delay, run)

    async def submit(self, owner):
        try:
            fix = self.pending.pop(owner, None)
            link = self.links.get(owner)
            user = await self.hass.auth.async_get_user(owner)
            if (
                not fix
                or not link
                or not user
                or not user.is_active
                or _mobile_entry(self.hass, link["device"], owner) is None
            ):
                return
            if dt_util.utcnow() - dt_util.parse_datetime(fix["observed_at"]) >= FIX_LIFETIME:
                return
            self.last_sent[owner] = monotonic()
            reply = await self.request("POST", "sample", payload=fix, token=link["token"])
            self.errors.pop(owner, None)
            if not link["use_in_home_assistant"]:
                return
            briefing = reply.get("briefing")
            if not isinstance(briefing, dict):
                return
            # Explicit allowlist: no location, IDs or provider data leave the
            # private websocket even if a future backend adds more fields.
            summary = {
                key: briefing[key]
                for key in (
                    "headline",
                    "recommended_action",
                    "risk_level",
                    "observed_at",
                    "expires_at",
                )
                if isinstance(briefing.get(key), str)
            }
            weather = briefing.get("model_weather")
            if isinstance(weather, dict) and type(weather.get("temperature_c")) in (float, int):
                summary["temperature_c"] = weather["temperature_c"]
            self.results[owner] = summary
            await self.notify(owner, link, reply.get("notice"), summary)
        except MobileLinkError as err:
            self.errors[owner] = err.code
            if err.code == "not_authorized":
                if remove := self.unsubscribe.pop(owner, None):
                    remove()
                self.pending.pop(owner, None)
                self.results.pop(owner, None)
        finally:
            self.tasks.pop(owner, None)
            if owner in self.pending:
                self.schedule(owner)

    async def notify(self, owner, link, notice, briefing):
        if not isinstance(notice, dict) or not isinstance(notice.get("key"), str):
            return
        if (
            self.last_notice.get(owner) == notice["key"]
            or _mobile_entry(self.hass, link["device"], owner) is None
        ):
            return
        expiry = min(
            dt_util.parse_datetime(value) or dt_util.utcnow()
            for value in (notice.get("expires_at", ""), briefing.get("expires_at", ""))
        )
        if expiry <= dt_util.utcnow() or not self.hass.services.has_service(
            "notify", "send_message"
        ):
            return
        # Match the actual Mobile App registration. A notify service name or
        # an arbitrary HA target could belong to a different family member.
        entities = [
            ent.entity_id
            for ent in er.async_entries_for_config_entry(er.async_get(self.hass), link["device"])
            if ent.domain == "notify" and ent.platform == "mobile_app" and ent.disabled_by is None
        ]
        if len(entities) != 1:
            return
        try:
            async with asyncio.timeout(15):
                await self.hass.services.async_call(
                    "notify",
                    "send_message",
                    {
                        "entity_id": entities[0],
                        "title": briefing.get("headline", "sScape"),
                        "message": briefing.get("recommended_action", "")
                        or briefing.get("headline", "sScape"),
                    },
                    blocking=True,
                )
        except (HomeAssistantError, TimeoutError):
            self.errors[owner] = "notification_failed"
            return
        self.last_notice[owner] = notice["key"]


@websocket_api.websocket_command({vol.Required("type"): "sscape/mobile_location/status"})
@callback
def ws_status(hass, connection, message):
    connection.send_result(message["id"], hass.data[DATA].status(connection.user.id))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "sscape/mobile_location/link",
        vol.Required("device"): vol.All(str, vol.Length(min=1, max=64)),
        vol.Required("code"): vol.All(str, vol.Length(min=43, max=43)),
    }
)
@websocket_api.async_response
async def ws_link(hass, connection, message):
    try:
        await hass.data[DATA].link(connection.user.id, message["device"], message["code"])
    except MobileLinkError as err:
        connection.send_error(message["id"], err.code, "Could not link this phone")
    else:
        ws_status(hass, connection, message)


@websocket_api.websocket_command({vol.Required("type"): "sscape/mobile_location/unlink"})
@websocket_api.async_response
async def ws_unlink(hass, connection, message):
    try:
        await hass.data[DATA].unlink(connection.user.id)
    except MobileLinkError as err:
        connection.send_error(message["id"], err.code, "Could not finish disconnecting this phone")
    else:
        ws_status(hass, connection, message)


async def setup_mobile_location(hass):
    manager = hass.data[DATA] = MobileLinks(hass)
    await manager.load()
    for command in (ws_status, ws_link, ws_unlink):
        websocket_api.async_register_command(hass, command)

    if "frontend" in hass.config.components:
        from pathlib import Path

        from homeassistant.components.http import StaticPathConfig
        from homeassistant.components.panel_custom import async_register_panel

        path = Path(__file__).parent / "www" / "mobile-location.js"
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/sscape-mobile.js", str(path), False)]
        )
        await async_register_panel(
            hass,
            webcomponent_name="sscape-mobile-location",
            frontend_url_path="sscape-mobile",
            sidebar_title="sScape GPS",
            sidebar_icon="mdi:cellphone-marker",
            module_url="/sscape-mobile.js",
            require_admin=False,
        )
