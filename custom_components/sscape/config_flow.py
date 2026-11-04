"""UI configuration, account-safe key replacement and place selection."""

from collections.abc import Mapping
from typing import Any

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

KEY_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


class SScapeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
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
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                    vol.Required(CONF_API_KEY): KEY_SELECTOR,
                }
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
                return self.async_create_entry(
                    title="sScape",
                    data=self._pending,
                    options={
                        CONF_PLACES: selected,
                        CONF_FOLLOW_ALL: follow_all,
                        CONF_INTERVAL: DEFAULT_INTERVAL,
                        CONF_WEATHER: True,
                    },
                )
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


class SScapeOptionsFlow(config_entries.OptionsFlow):
    """Choose a subset or follow all places, including places added later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        current = entry.options.get(CONF_PLACES, [])
        errors = {}
        if not hasattr(self, "_choices"):
            try:
                client = SScapeClient(
                    entry.data[CONF_HOST],
                    entry.data[CONF_API_KEY],
                    async_get_clientsession(self.hass),
                    self.hass.config.language,
                )
                catalog = await client.async_get_catalog()
                if catalog.account_id != entry.data[CONF_ACCOUNT_ID]:
                    raise AuthenticationError()
                self._choices = catalog.places
            except (AuthenticationError, PermissionDenied):
                return self.async_abort(reason="invalid_auth")
            except CannotConnect:
                return self.async_abort(reason="cannot_connect")
        choices = self._choices
        current = [place_id for place_id in current if place_id in choices]
        if user_input is not None:
            if not set(user_input.get(CONF_PLACES, [])).issubset(choices):
                errors["base"] = "invalid_places"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PLACES: user_input.get(CONF_PLACES, []),
                        CONF_INTERVAL: int(user_input[CONF_INTERVAL]),
                        CONF_FOLLOW_ALL: user_input.get(CONF_FOLLOW_ALL, False),
                        CONF_WEATHER: user_input.get(CONF_WEATHER, True),
                        **(
                            {"push_updates": user_input["push_updates"]}
                            if "push_updates" in user_input
                            else {}
                        ),
                    },
                )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_PLACES, default=current): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            multiple=True,
                            options=[
                                {"value": key, "label": label} for key, label in choices.items()
                            ],
                        )
                    ),
                    vol.Required(
                        CONF_INTERVAL, default=entry.options.get(CONF_INTERVAL, DEFAULT_INTERVAL)
                    ): vol.All(vol.Coerce(int), vol.Range(min=MIN_INTERVAL, max=MAX_INTERVAL)),
                    vol.Optional(
                        CONF_FOLLOW_ALL, default=entry.options.get(CONF_FOLLOW_ALL, False)
                    ): bool,
                    vol.Optional(CONF_WEATHER, default=entry.options.get(CONF_WEATHER, True)): bool,
                    vol.Optional(
                        "push_updates", default=entry.options.get("push_updates", True)
                    ): bool,
                }
            ),
            errors=errors,
        )
