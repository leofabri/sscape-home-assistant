"""Small, read-only client for the existing account-scoped sScape API."""

import json
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import quote, urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import MAX_INTERVAL, MIN_INTERVAL, RISK_LEVELS
from .targets import validate_point

MAX_BODY_BYTES = 2 * 1024 * 1024


class SScapeError(Exception):
    """Safe error without server bodies, URLs or credentials."""


class AuthenticationError(SScapeError):
    """Expired, revoked or invalid API key."""


class PermissionDenied(SScapeError):
    """The key lacks places:read or the plan does not allow API access."""


class CannotConnect(SScapeError):
    """Temporary connection, timeout or HTTP failure."""


class ProtocolError(CannotConnect):
    """Unexpected or ambiguous API response."""


class ServerUpgradeRequired(CannotConnect):
    """The server cannot guarantee explicit place selection yet."""


class RateLimited(CannotConnect):
    """Retry only after the requested delay."""

    def __init__(self, retry_after: float = MIN_INTERVAL) -> None:
        super().__init__("API rate limit reached")
        self.retry_after = retry_after


def normalize_host(value: str) -> str:
    """Accept an origin, never credentials/query/path; HTTP only on a local network."""
    parsed = urlsplit(value.strip())
    host = parsed.hostname
    if (
        not host
        or parsed.scheme not in ("https", "http")
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
        or any(char.isspace() for char in value)
        or "\\" in value
    ):
        raise ValueError("Enter a complete server origin without credentials or a path")
    _ = parsed.port  # Validate malformed ports, including out-of-range values.
    if parsed.scheme == "http":
        local = host == "localhost" or host.endswith(".local")
        try:
            address = ip_address(host)
            local = address.is_loopback or (
                address.is_private
                and not address.is_link_local
                and not address.is_unspecified
                and not address.is_reserved
            )
        except ValueError:
            pass
        if not local:
            raise ValueError("Use HTTPS for a remote server")
    return f"{parsed.scheme}://{parsed.netloc.lower()}"


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(UTC) if result.tzinfo else None
    except ValueError:
        return None


def _text(value: Any, limit: int = 4096) -> str | None:
    return value[:limit] if isinstance(value, str) and value.strip() else None


@dataclass(frozen=True)
class PlaceSnapshot:
    """Assessment for one saved place, not a fabricated local weather measurement."""

    place_id: str
    name: str
    briefing: dict[str, Any]
    live: bool

    @property
    def updated_at(self) -> datetime | None:
        """Assessment time, deliberately not described as an observation timestamp."""
        return _time(self.briefing.get("updated_at"))

    def is_fresh(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        updated = self.updated_at
        expires = _time(self.briefing.get("expires_at"))
        # The API assigns 20/30/60 minute TTLs. Never extend its expiry locally.
        return bool(
            self.live
            and updated
            and expires
            and updated < expires
            and -timedelta(seconds=60) <= now - updated <= timedelta(hours=1)
            and now < expires
        )

    @property
    def risk_level(self) -> str | None:
        value = self.briefing.get("risk_level")
        return value if isinstance(value, str) and value in RISK_LEVELS else None

    @property
    def headline(self) -> str | None:
        return _text(self.briefing.get("headline"), 255)

    @property
    def eta_minutes(self) -> int | float | None:
        window = self.briefing.get("time_window")
        if isinstance(window, dict) and window.get("reliability") != "stable":
            return None
        value = self.briefing.get("approach_eta_minutes")
        if (
            self.briefing.get("situation_phase") != "approach"
            or type(value) not in (int, float)
            or not math.isfinite(value)
            or value < 0
        ):
            return None
        return value

    @property
    def attributes(self) -> dict[str, Any]:
        """Bounded human-readable evidence; no account identifiers or raw response dump."""
        fields = (
            "headline",
            "summary",
            "recommended_action",
            "confidence_label",
            "situation_phase",
            "primary_signal",
            "exposure",
            "exposure_source",
            "data_quality_status",
        )
        return {key: text for key in fields if (text := _text(self.briefing.get(key)))}

    def metric(self, key):
        value = self.briefing.get(key)
        return (
            value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        )

    def arrival_bound(self, key):
        window = self.briefing.get("time_window")
        if (
            not isinstance(window, dict)
            or window.get("reliability") not in ("stable", "variable")
            or self.briefing.get("situation_phase") != "approach"
        ):
            return None
        value = window.get(key)
        return (
            value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None
        )

    @property
    def lightning_distance(self):
        if isinstance(self.briefing.get("data_quality"), dict):
            return self.metric("nearest_lightning_distance_20m_km")
        return self.metric("nearest_lightning_distance_km")

    def reading(self, variable):
        from .measurements import valid_reading

        return valid_reading(self.briefing.get("observation_context"), variable)

    def signal(self, key):
        if key == "storm_approach":
            phase = self.briefing.get("situation_phase")
            return (
                phase == "approach" if phase in ("quiet", "watch", "approach", "impact") else None
            )
        field = {
            "lightning_detected": "lightning_count_20m",
            "official_warning": "official_alert_count",
            "earthquake_activity": "earthquakes_24h",
            "hail_reported": "hail_confirmations_20m",
        }.get(key)
        if field:
            count = self.metric(field)
            quality = self.briefing.get("data_quality")
            domain = {
                "lightning_detected": "lightning",
                "official_warning": "official_warnings",
                "earthquake_activity": "earthquakes",
                "hail_reported": "hail_reports",
            }[key]
            if isinstance(quality, dict):
                status = quality.get(domain, {})
                if status.get("state") == "stale" or (
                    count == 0 and status.get("coverage", "unknown") == "unknown"
                ):
                    return None
            return count > 0 if count is not None else None
        if key == "wildfire_detected":
            signals = self.briefing.get("environmental_signals")
            if not isinstance(signals, list):
                return None
            # This catalogue is capped to a few recent environmental signals;
            # absence cannot prove that the wider fire feed is clear/complete.
            return (
                True
                if any(isinstance(item, dict) and item.get("kind") == "fire" for item in signals)
                else None
            )
        return None


@dataclass(frozen=True)
class AccountSnapshot:
    """An account response; an empty list is valid after all places are removed."""

    account_id: str | None
    places: dict[str, PlaceSnapshot]


@dataclass(frozen=True)
class PlaceCatalog:
    """Only authorized place IDs/names, with no coordinates or assessments."""

    account_id: str
    places: dict[str, str]
    restricted: bool
    capabilities: tuple[str, ...] = ()


def parse_snapshot(body: Any) -> AccountSnapshot:
    """Validate envelope and ownership before creating any HA entities."""
    if not isinstance(body, dict) or not isinstance(body.get("states"), list):
        raise ProtocolError("Invalid saved-place response")
    if len(body["states"]) > 1000:
        raise ProtocolError("Saved-place response is too large")
    owner = body.get("account_id")
    if owner is not None and (not isinstance(owner, str) or not owner or "|" in owner):
        raise ProtocolError("Invalid account identity")
    owners: set[str] = {owner} if owner else set()
    places: dict[str, PlaceSnapshot] = {}
    for row in body["states"]:
        if not isinstance(row, dict) or not isinstance(row.get("place"), dict):
            raise ProtocolError("Invalid place envelope")
        place = row["place"]
        place_id, owner, name = place.get("id"), place.get("user_id"), place.get("name")
        if not all(
            isinstance(v, str) and 0 < len(v) <= 128 and "|" not in v for v in (place_id, owner)
        ) or not isinstance(name, str):
            raise ProtocolError("Missing place identity")
        if place_id in places:
            raise ProtocolError("Duplicate place identity")
        owners.add(owner)
        if len(owners) > 1:
            raise ProtocolError("Mixed account response")
        state = row.get("state")
        briefing = row.get("current_briefing")
        live = isinstance(briefing, dict)
        if not live:
            briefing = state.get("briefing") if isinstance(state, dict) else None
            live = row.get("live_state_available") is True and isinstance(state, dict)
        if not isinstance(briefing, dict):
            briefing = {}
        places[place_id] = PlaceSnapshot(place_id, name[:255], briefing, live)
    return AccountSnapshot(next(iter(owners), None), places)


def _retry_after(value: str | None) -> float:
    try:
        seconds = float(value or "")
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value or "") - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return MIN_INTERVAL
    return min(86400, max(MIN_INTERVAL, seconds)) if math.isfinite(seconds) else MAX_INTERVAL


class SScapeClient:
    """One request per account/refresh, using Home Assistant's shared HTTPS session."""

    def __init__(
        self, host: str, api_key: str, session: ClientSession, language: str = "en"
    ) -> None:
        self.host = normalize_host(host)
        if not api_key.strip() or not api_key.isascii() or any(c.isspace() for c in api_key):
            raise ValueError("Invalid API key format")
        self._api_key = api_key
        self._session = session
        self._language = "it" if language.startswith("it") else "en"
        self._capabilities: set[str] = set()
        self._page_cache = {}
        self._cached_selection = None
        self.places_restricted = False
        self.account_id = None
        self.points = {}

    async def async_get_catalog(self) -> PlaceCatalog:
        body = await self._async_get("/api/v1/integrations/places", params={"view": "selection"})
        if not isinstance(body, dict) or body.get("contract_version") != "home-assistant.v2":
            raise ServerUpgradeRequired(
                "Update the sScape server before using this integration version"
            )
        owner, rows = body.get("account_id"), body.get("places")
        if (
            not isinstance(owner, str)
            or not owner
            or len(owner) > 256
            or "|" in owner
            or not isinstance(rows, list)
            or len(rows) > 1000
        ):
            raise ProtocolError("Invalid place catalogue")
        choices = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ProtocolError("Invalid place catalogue")
            place_id, name = row.get("id"), row.get("name")
            if (
                not isinstance(place_id, str)
                or not place_id
                or len(place_id) > 256
                or any(char.isspace() or char in "/|,?#" for char in place_id)
                or not isinstance(name, str)
                or place_id in choices
            ):
                raise ProtocolError("Invalid place catalogue")
            choices[place_id] = name[:255]
        capabilities = body.get("capabilities", [])
        self._capabilities = (
            {v for v in capabilities if isinstance(v, str)}
            if isinstance(capabilities, list)
            else set()
        )
        self.places_restricted = body.get("places_restricted") is True
        self.account_id = owner
        return PlaceCatalog(
            owner, choices, body.get("places_restricted") is True, tuple(sorted(self._capabilities))
        )

    async def async_get_snapshot(self, place_ids: list[str] | None = None) -> AccountSnapshot:
        if place_ids == []:
            return AccountSnapshot(None, {})
        if "automation.snapshot.v1" in self._capabilities:
            return await self._async_get_compact_snapshot(place_ids)
        params = {"place_ids": ",".join(place_ids)} if place_ids is not None else None
        result = parse_snapshot(
            await self._async_get("/api/v1/integrations/places/states", params=params)
        )
        if place_ids is not None and not set(result.places).issubset(place_ids):
            raise ProtocolError("Server returned an unselected place")
        return result

    async def _async_get_compact_snapshot(self, place_ids):
        selection = tuple(place_ids) if place_ids is not None else None
        if self._cached_selection != selection:
            self._page_cache.clear()
            self._cached_selection = selection
        params = {"limit": "25"}
        if place_ids is not None:
            params["place_ids"] = ",".join(place_ids)
        owner, places, cursors = None, {}, set()
        visited = set()
        for _ in range(40):
            cache_key = tuple(sorted(params.items()))
            visited.add(cache_key)
            previous = self._page_cache.get(cache_key)
            query = dict(params)
            if previous and isinstance(previous.get("revision"), str):
                query["revision"] = previous["revision"]
            body = await self._async_get("/api/v1/integrations/places/snapshot", params=query)
            if (
                not isinstance(body, dict)
                or body.get("contract_version") != "automation.snapshot.v1"
            ):
                raise ProtocolError("Invalid automation snapshot")
            if body.get("not_modified") is True:
                if (
                    not previous
                    or body.get("revision") != previous.get("revision")
                    or body.get("account_id") != previous.get("account_id")
                ):
                    raise ProtocolError("Invalid snapshot cache reference")
                clocks = body.get("assessment_times")
                if not isinstance(clocks, dict):
                    raise ProtocolError("Invalid assessment clocks")
                metadata = {key: body.get(key) for key in ("has_more", "next_cursor", "checked_at")}
                body = deepcopy(previous)
                body.update(metadata)
                for row in body["states"]:
                    briefing = row.get("current_briefing")
                    if not isinstance(briefing, dict):
                        continue
                    clock = clocks.get(row["place"]["id"])
                    if (
                        not isinstance(clock, dict)
                        or not _time(clock.get("updated_at"))
                        or not _time(clock.get("expires_at"))
                    ):
                        raise ProtocolError("Missing assessment clocks")
                    # Refresh only the assessment validity, not the actual
                    # observation times, quality flags or forecast valid times.
                    briefing.update(updated_at=clock["updated_at"], expires_at=clock["expires_at"])
            page = parse_snapshot(body)
            if owner and page.account_id != owner:
                raise ProtocolError("Snapshot owner changed")
            owner = page.account_id or owner
            if places.keys() & page.places.keys():
                raise ProtocolError("Duplicate snapshot page")
            if place_ids is not None and not set(page.places).issubset(place_ids):
                raise ProtocolError("Server returned an unselected place")
            places.update(page.places)
            self._page_cache[cache_key] = deepcopy(body)
            if body.get("has_more") is False:
                self._page_cache = {
                    key: value for key, value in self._page_cache.items() if key in visited
                }
                return AccountSnapshot(owner, places)
            cursor = body.get("next_cursor")
            if (
                body.get("has_more") is not True
                or not isinstance(cursor, str)
                or not cursor
                or len(cursor) > 256
                or cursor in cursors
            ):
                raise ProtocolError("Invalid snapshot cursor")
            cursors.add(cursor)
            params["cursor"] = cursor
        raise ProtocolError("Snapshot page limit exceeded")

    async def async_updates(self, place_ids):
        """Bounded SSE invalidations, never credentials in a query string."""
        from .stream import updates

        async for event in updates(self, place_ids):
            yield event

    async def async_get_weather(self, place_id: str) -> dict[str, Any]:
        if place_id in self.points:
            return await self.async_get_point_weather(self.points[place_id], self.account_id)
        body = await self._async_get(
            f"/api/v1/integrations/places/{quote(place_id, safe='')}/weather"
        )
        if (
            not isinstance(body, dict)
            or body.get("place_id") != place_id
            or not isinstance(body.get("forecast"), dict)
        ):
            raise ProtocolError("Invalid weather response")
        return body["forecast"]

    async def _async_point_response(self, point, owner, *, forecast=False):
        point = validate_point(point)
        if self.places_restricted:
            raise PermissionDenied(
                "Coordinate queries require an unrestricted integrations:read key"
            )
        params = {field: str(point[field]) for field in ("latitude", "longitude", "radius_km")}
        params["label"] = point["name"]
        if forecast:
            params["include_forecast"] = "true"
        body = await self._async_get("/api/v1/integrations/briefing", params=params)
        briefing = body.get("briefing") if isinstance(body, dict) else None
        place = briefing.get("place") if isinstance(briefing, dict) else None
        actual_owner = place.get("user_id") if isinstance(place, dict) else None
        if (
            not isinstance(actual_owner, str)
            or not actual_owner
            or "|" in actual_owner
            or (owner is not None and actual_owner != owner)
            or any(
                type(place.get(field)) not in (int, float)
                or not math.isfinite(place[field])
                or abs(place[field] - point[field]) > 1e-6
                for field in ("latitude", "longitude")
            )
        ):
            raise ProtocolError(
                "Geographic response does not match the requested account and target"
            )
        self.account_id = actual_owner
        return body

    async def async_get_point(self, point, owner=None):
        body = await self._async_point_response(point, owner)
        return PlaceSnapshot(point["id"], point["name"], body["briefing"], True)

    async def async_get_point_weather(self, point, owner):
        body = await self._async_point_response(point, owner, forecast=True)
        if not isinstance(body.get("forecast"), dict):
            raise ProtocolError("The server does not support geographic forecasts yet")
        return body["forecast"]

    async def _async_get(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        """Do not follow redirects that could forward X-API-Key to another host."""
        try:
            async with self._session.get(
                f"{self.host}{path}",
                params=params,
                headers={
                    "X-API-Key": self._api_key,
                    "Accept": "application/json",
                    "Accept-Language": self._language,
                },
                timeout=ClientTimeout(total=20),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise AuthenticationError("API key rejected")
                if response.status == 403:
                    raise PermissionDenied("Check API access plan and places:read scope")
                if response.status == 429:
                    raise RateLimited(_retry_after(response.headers.get("Retry-After")))
                if response.status != 200:
                    raise CannotConnect("Server did not return a successful response")
                chunks = bytearray()
                while chunk := await response.content.read(65536):
                    chunks.extend(chunk)
                    if len(chunks) > MAX_BODY_BYTES:
                        raise ProtocolError("Response exceeds size limit")
                return json.loads(chunks)
        except (TimeoutError, ClientError):
            raise CannotConnect("Connection failed or timed out") from None
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid JSON response") from None
