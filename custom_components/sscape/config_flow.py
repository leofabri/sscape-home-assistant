"""UI configuration, account-safe key replacement and place selection."""

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    AuthenticationError,
    CannotConnect,
    PermissionDenied,
    ServerUpgradeRequired,
    SScapeClient,
    normalize_host,
)
from .const import (
    CONF_ACCOUNT_ID,
    CONF_FOLLOW_ALL,
    CONF_INTERVAL,
    CONF_PLACES,
    CONF_WEATHER,
    DEFAULT_HOST,
    DEFAULT_INTERVAL,
    DOMAIN,
    MAX_INTERVAL,
    MIN_INTERVAL,
)
from .setup_flow import SetupFlowMixin, coordinate_schema
from .targets import validate_point

KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


class SScapeConfigFlow(SetupFlowMixin, config_entries.ConfigFlow, domain=DOMAIN):
    """Link one sScape account per server; do not use the secret as the identity."""

    VERSION = 2

    async def _validate(
        self, host: str, key: str, *, allow_empty=False
    ) -> tuple[dict[str, str], str | None]:
        try:
            host = normalize_host(host)
        except ValueError:
            return {}, "invalid_host"
        try:
            client = SScapeClient(
                host, key, async_get_clientsession(self.hass), self.hass.config.language
            )
            entry = (
                self._get_reauth_entry() if self.source == config_entries.SOURCE_REAUTH else None
            )
            if entry and entry.options.get("coordinate_only"):
                point = entry.options["coordinate_targets"][0]
                await client.async_get_point(point, entry.data[CONF_ACCOUNT_ID])
                from .api import PlaceCatalog

                snapshot = PlaceCatalog(client.account_id, {}, False)
            else:
                snapshot = await client.async_get_catalog()
        except ServerUpgradeRequired:
            return {}, "server_update_required"
        except (AuthenticationError, ValueError):
            return {}, "invalid_auth"
        except PermissionDenied:
            return {}, "access_denied"
        except CannotConnect:
            return {}, "cannot_connect"
        if not snapshot.places and not allow_empty:
            return {}, "no_places"
        self._catalog = snapshot
        return {CONF_HOST: host, CONF_API_KEY: key, CONF_ACCOUNT_ID: snapshot.account_id}, None

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors = {}
        if user_input is not None:
            if user_input.get("use_coordinates") or user_input.get("location_mode") in (
                "home",
                "coordinates",
            ):
                self._coordinate_mode = user_input.get("location_mode", "coordinates")
                try:
                    host = normalize_host(user_input[CONF_HOST])
                    SScapeClient(host, user_input[CONF_API_KEY], async_get_clientsession(self.hass))
                except ValueError:
                    errors["base"] = "invalid_auth"
                else:
                    self._pending = {CONF_HOST: host, CONF_API_KEY: user_input[CONF_API_KEY]}
                    return await self.async_step_coordinate_target()
                return self.async_show_form(
                    step_id="user",
                    description_placeholders={"profile_url": DEFAULT_HOST + "/profile"},
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                            vol.Required(CONF_API_KEY): KEY_SELECTOR,
                            vol.Optional(
                                "location_mode", default="saved_places"
                            ): selector.SelectSelector(
                                selector.SelectSelectorConfig(
                                    options=["saved_places", "home", "coordinates"],
                                    translation_key="location_mode",
                                )
                            ),
                        }
                    ),
                    errors=errors,
                )
            data, error = await self._validate(user_input[CONF_HOST], user_input[CONF_API_KEY])
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(f"{data[CONF_HOST]}|{data[CONF_ACCOUNT_ID]}")
                self._abort_if_unique_id_configured()
                self._pending = data
                return await self.async_step_places()
        return self.async_show_form(
            step_id="user",
            description_placeholders={"profile_url": DEFAULT_HOST + "/profile"},
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                    vol.Required(CONF_API_KEY): KEY_SELECTOR,
                    vol.Optional("location_mode", default="saved_places"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["saved_places", "home", "coordinates"],
                            translation_key="location_mode",
                        )
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_coordinate_target(self, user_input=None):
        errors = {}
        if user_input is not None:
            point = {"id": "point-" + uuid4().hex, **user_input}
            if getattr(self, "_coordinate_mode", None) == "home":
                point.update(
                    latitude=self.hass.config.latitude,
                    longitude=self.hass.config.longitude,
                    origin="home_assistant",
                )
            try:
                validate_point(point)
                client = SScapeClient(
                    self._pending[CONF_HOST],
                    self._pending[CONF_API_KEY],
                    async_get_clientsession(self.hass),
                    self.hass.config.language,
                )
                await client.async_get_point(point)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except PermissionDenied:
                errors["base"] = "coordinate_access_denied"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_coordinates"
            else:
                await self.async_set_unique_id(f"{client.host}|{client.account_id}")
                self._abort_if_unique_id_configured()
                self._pending |= {CONF_ACCOUNT_ID: client.account_id}
                self._draft_options = {
                    "coordinate_only": True,
                    "coordinate_targets": [point],
                    CONF_PLACES: [],
                    CONF_FOLLOW_ALL: False,
                    CONF_INTERVAL: DEFAULT_INTERVAL,
                    CONF_WEATHER: True,
                }
                self._choices = {}
                self._initial_setup = True
                return await self.async_step_preferences()
        return self.async_show_form(
            step_id="coordinate_target",
            data_schema=coordinate_schema(
                hass=self.hass, home=getattr(self, "_coordinate_mode", None) == "home"
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            data, error = await self._validate(
                entry.data[CONF_HOST], user_input[CONF_API_KEY], allow_empty=True
            )
            if not error and data[CONF_ACCOUNT_ID] != entry.data[CONF_ACCOUNT_ID]:
                error = "wrong_account"
            if error:
                errors["base"] = error
            else:
                # A loaded entry's update listener already schedules its reload.
                # Asking the helper to reload too creates two overlapping reloads.
                needs_reload = (
                    not entry.update_listeners or entry.data[CONF_API_KEY] == data[CONF_API_KEY]
                )
                result = self.async_update_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: data[CONF_API_KEY]},
                )
                if needs_reload:
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return result
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): KEY_SELECTOR,
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SScapeOptionsFlow()

    async def async_step_places(self, user_input=None):
        errors = {}
        if user_input is not None:
            selected = list(dict.fromkeys(user_input.get(CONF_PLACES, [])))
            follow_all = user_input.get(CONF_FOLLOW_ALL, False)
            if not follow_all and not selected:
                errors["base"] = "select_places"
            elif not set(selected).issubset(self._catalog.places):
                errors["base"] = "invalid_places"
            else:
                self._draft_options = {
                    CONF_PLACES: selected,
                    CONF_FOLLOW_ALL: follow_all,
                    CONF_INTERVAL: DEFAULT_INTERVAL,
                    CONF_WEATHER: True,
                }
                self._initial_setup = True
                self._choices = dict(self._catalog.places)
                return await self.async_step_preferences()
        return self.async_show_form(
            step_id="places",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PLACES, default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {"value": key, "label": name}
                                for key, name in self._catalog.places.items()
                            ],
                        )
                    ),
                    vol.Optional(CONF_FOLLOW_ALL, default=False): bool,
                }
            ),
        )


class SScapeOptionsFlow(SetupFlowMixin, config_entries.OptionsFlow):
    """Manage one task directly, or complete the guided journey in one visit."""

    async def async_step_init(self, user_input=None):
        self._draft_options = dict(self.config_entry.options)
        parent = getattr(self.config_entry, "runtime_data", None)
        self._choices = (
            {
                key: place.name
                for key, place in parent.data.places.items()
                if not key.startswith("point-")
            }
            if parent and parent.data
            else {}
        )
        if not self._choices and not self.config_entry.options.get("coordinate_only"):
            try:
                client = SScapeClient(
                    self.config_entry.data[CONF_HOST],
                    self.config_entry.data[CONF_API_KEY],
                    async_get_clientsession(self.hass),
                    self.hass.config.language,
                )
                catalog = await client.async_get_catalog()
                if catalog.account_id == self.config_entry.data[CONF_ACCOUNT_ID]:
                    self._choices = catalog.places
            except (AuthenticationError, PermissionDenied, CannotConnect):
                pass  # Local settings remain editable while the cloud is offline.
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "preferences",
                "saved_places",
                "coordinate",
                "home_point",
                "local_sensors",
                "notifications",
                "assist",
                "advanced",
            ],
        )

    async def async_step_saved_places(self, user_input=None):
        errors = {}
        if not getattr(self, "_loaded_catalog", False):
            client = SScapeClient(
                self.config_entry.data[CONF_HOST],
                self.config_entry.data[CONF_API_KEY],
                async_get_clientsession(self.hass),
                self.hass.config.language,
            )
            try:
                catalog = await client.async_get_catalog()
                if catalog.account_id != self.config_entry.data[CONF_ACCOUNT_ID]:
                    raise AuthenticationError()
            except (AuthenticationError, PermissionDenied):
                return self.async_abort(reason="invalid_auth")
            except CannotConnect:
                return self.async_abort(reason="cannot_connect")
            self._choices, self._loaded_catalog = catalog.places, True
        opts = self._options()
        if user_input is not None:
            if not set(user_input.get(CONF_PLACES, [])).issubset(self._choices):
                errors["base"] = "invalid_places"
            else:
                return await self._save(
                    {
                        CONF_PLACES: user_input.get(CONF_PLACES, []),
                        CONF_FOLLOW_ALL: user_input.get(CONF_FOLLOW_ALL, False),
                        "coordinate_only": False,
                    }
                )
        return self.async_show_form(
            step_id="saved_places",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_PLACES,
                        default=[key for key in opts.get(CONF_PLACES, []) if key in self._choices],
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": name} for key, name in self._choices.items()
                            ],
                            multiple=True,
                        )
                    ),
                    vol.Optional(CONF_FOLLOW_ALL, default=opts.get(CONF_FOLLOW_ALL, False)): bool,
                }
            ),
        )

    async def async_step_home_point(self, user_input=None):
        self._editing_point = next(
            (
                dict(p)
                for p in self._options().get("coordinate_targets", [])
                if p.get("origin") == "home_assistant"
            ),
            {},
        )
        self._editing_point.update(
            latitude=self.hass.config.latitude,
            longitude=self.hass.config.longitude,
            origin="home_assistant",
        )
        self._using_home_position = True
        return await self.async_step_coordinate_details()

    async def async_step_advanced(self, user_input=None):
        opts = self._options()
        if user_input is not None:
            return await self._save(user_input)
        return self.async_show_form(
            step_id="advanced",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_INTERVAL, default=opts.get(CONF_INTERVAL, DEFAULT_INTERVAL)
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                    vol.Optional("push_updates", default=opts.get("push_updates", True)): bool,
                }
            ),
        )
