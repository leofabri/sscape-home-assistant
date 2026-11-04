"""Small, read-only client for the existing account-scoped sScape API."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import MAX_INTERVAL, MIN_INTERVAL, RISK_LEVELS

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
        )
        return {key: text for key in fields if (text := _text(self.briefing.get(key)))}


@dataclass(frozen=True)
class AccountSnapshot:
    """An account response; an empty list is valid after all places are removed."""

    account_id: str | None
    places: dict[str, PlaceSnapshot]


def parse_snapshot(body: Any) -> AccountSnapshot:
    """Validate envelope and ownership before creating any HA entities."""
    if not isinstance(body, dict) or not isinstance(body.get("states"), list):
        raise ProtocolError("Invalid saved-place response")
    if len(body["states"]) > 1000:
        raise ProtocolError("Saved-place response is too large")
    owners: set[str] = set()
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

    async def async_get_snapshot(self) -> AccountSnapshot:
        """Do not follow redirects that could forward X-API-Key to another host."""
        try:
            async with self._session.get(
                f"{self.host}/api/v1/integrations/places/states",
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
                return parse_snapshot(json.loads(chunks))
        except (TimeoutError, ClientError):
            raise CannotConnect("Connection failed or timed out") from None
        except (ValueError, UnicodeError):
            raise ProtocolError("Invalid JSON response") from None
