"""UI configuration, account-safe key replacement and place selection."""

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, CannotConnect, PermissionDenied, SScapeClient, normalize_host
from .const import (
    CONF_ACCOUNT_ID,
    CONF_INTERVAL,
    CONF_PLACES,
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

    VERSION = 1

    async def _validate(self, host: str, key: str) -> tuple[dict[str, str], str | None]:
        try:
            host = normalize_host(host)
        except ValueError:
            return {}, "invalid_host"
        try:
            client = SScapeClient(
                host, key, async_get_clientsession(self.hass), self.hass.config.language
            )
            snapshot = await client.async_get_snapshot()
        except (AuthenticationError, ValueError):
            return {}, "invalid_auth"
        except PermissionDenied:
            return {}, "access_denied"
        except CannotConnect:
            return {}, "cannot_connect"
        if not snapshot.account_id:
            # The existing API returns account identity in each saved place.
            # With zero places, do not invent an identity based on the API key.
            return {}, "no_places"
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
                return self.async_create_entry(title="sScape", data=data)
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
            data, error = await self._validate(entry.data[CONF_HOST], user_input[CONF_API_KEY])
            if not error and data[CONF_ACCOUNT_ID] != entry.data[CONF_ACCOUNT_ID]:
                error = "wrong_account"
            if error:
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_API_KEY: data[CONF_API_KEY]},
                )
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


class SScapeOptionsFlow(config_entries.OptionsFlow):
    """Choose a subset or follow all places, including places added later."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        current = entry.options.get(CONF_PLACES, [])
        coordinator = getattr(entry, "runtime_data", None)
        choices = (
            {place_id: place.name for place_id, place in coordinator.data.places.items()}
            if coordinator and coordinator.data
            else {}
        )
        # Keep an explicitly selected place visible when temporarily missing/deleted.
        choices.update({place_id: place_id for place_id in current if place_id not in choices})
        errors = {}
        if user_input is not None:
            if not set(user_input.get(CONF_PLACES, [])).issubset(choices):
                errors["base"] = "invalid_places"
            else:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_PLACES: user_input.get(CONF_PLACES, []),
                        CONF_INTERVAL: int(user_input[CONF_INTERVAL]),
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
                }
            ),
            errors=errors,
        )
