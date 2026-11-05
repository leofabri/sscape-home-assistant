"""Native options steps for targets, local instruments, notifications and voice."""

from uuid import uuid4

import voluptuous as vol
from homeassistant.const import CONF_API_KEY, CONF_HOST
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, CannotConnect, PermissionDenied, SScapeClient
from .journey import JourneyMixin
from .targets import validate_point


def coordinate_schema(point=None, hass=None, *, home=False):
    point = point or {}
    schema = vol.Schema(
        {
            vol.Required(
                "name",
                default=point.get(
                    "name", "Casa" if hass and hass.config.language.startswith("it") else "Home"
                ),
            ): str,
            vol.Required(
                "latitude", default=point.get("latitude", hass.config.latitude if hass else 0)
            ): vol.Coerce(float),
            vol.Required(
                "longitude", default=point.get("longitude", hass.config.longitude if hass else 0)
            ): vol.Coerce(float),
            vol.Required("radius_km", default=point.get("radius_km", 25)): vol.All(
                vol.Coerce(float), vol.Range(min=1, max=1000)
            ),
            vol.Optional("enabled", default=point.get("enabled", True)): bool,
        }
    )
    return vol.Schema(
        {
            key: value
            for key, value in schema.schema.items()
            if not home or key.schema not in ("latitude", "longitude")
        }
    )


class SetupFlowMixin(JourneyMixin):
    def _options(self):
        return (
            self._draft_options
            if hasattr(self, "_draft_options")
            else dict(self.config_entry.options)
        )

    def _names(self):
        opts = self._options()
        choices = getattr(self, "_choices", {})
        names = {
            key: name
            for key, name in choices.items()
            if opts.get("follow_all_places", False) or key in opts.get("places", [])
        }
        names.update(
            {
                p["id"]: p["name"]
                for p in opts.get("coordinate_targets", [])
                if p.get("enabled", True)
            }
        )
        return names

    async def async_step_coordinate(self, user_input=None):
        points = self._options().get("coordinate_targets", [])
        if user_input is not None:
            self._using_home_position = False
            self._editing_point = next((p for p in points if p["id"] == user_input["target"]), None)
            return await self.async_step_coordinate_details()
        return self.async_show_form(
            step_id="coordinate",
            data_schema=vol.Schema(
                {
                    vol.Required("target", default="new"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": "new",
                                    "label": "Aggiungi un punto"
                                    if self.hass.config.language.startswith("it")
                                    else "Add a point",
                                }
                            ]
                            + [{"value": p["id"], "label": p["name"]} for p in points]
                        )
                    )
                }
            ),
        )

    async def async_step_coordinate_details(self, user_input=None):
        errors = {}
        current = self._editing_point or {}
        if user_input is not None:
            point = {"id": current.get("id", "point-" + uuid4().hex), **user_input}
            if getattr(self, "_using_home_position", False):
                point.update(
                    latitude=self.hass.config.latitude,
                    longitude=self.hass.config.longitude,
                    origin="home_assistant",
                )
            try:
                validate_point(point)
                points = [
                    p
                    for p in self._options().get("coordinate_targets", [])
                    if p["id"] != point["id"]
                ]
                if len(points) >= 10:
                    raise ValueError()
                if point.get("enabled", True):
                    client = SScapeClient(
                        self.config_entry.data[CONF_HOST],
                        self.config_entry.data[CONF_API_KEY],
                        async_get_clientsession(self.hass),
                        self.hass.config.language,
                    )
                    await client.async_get_point(point, self.config_entry.data["account_id"])
                points.append(point)
            except PermissionDenied:
                errors["base"] = "coordinate_access_denied"
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except ValueError:
                errors["base"] = "invalid_coordinates"
            else:
                return await self._save({"coordinate_targets": points})
        return self.async_show_form(
            step_id="coordinate_details",
            data_schema=coordinate_schema(
                current, self.hass, home=getattr(self, "_using_home_position", False)
            ),
            errors=errors,
        )

    async def async_step_local_sensors(self, user_input=None):
        if user_input is not None:
            if user_input["target"] not in self._names():
                return self.async_abort(reason="invalid_places")
            self._local_target = user_input["target"]
            return await self.async_step_local_sensor_details()
        return self.async_show_form(
            step_id="local_sensors",
            data_schema=vol.Schema(
                {
                    vol.Required("target"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": value}
                                for key, value in self._names().items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_local_sensor_details(self, user_input=None):
        from .local_sensors import LOCAL_SPECS, validate_binding

        bindings = dict(self._options().get("local_sensors", {}))
        previous = bindings.get(self._local_target, {})
        errors = {}
        if user_input is not None:
            if not user_input.get("confirm_outdoor"):
                errors["base"] = "outdoor_required"
            else:
                try:
                    selected = {
                        key: value
                        for key, value in user_input.items()
                        if key in LOCAL_SPECS and value
                    }
                    for variable, entity in selected.items():
                        validate_binding(self.hass, variable, entity)
                except ValueError:
                    errors["base"] = "invalid_local_sensor"
                else:
                    bindings[self._local_target] = selected
                    return await self._save({"local_sensors": bindings})
        schema = {
            vol.Optional(key, default=previous.get(key)): vol.Any(
                None, selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            )
            for key in LOCAL_SPECS
        }
        schema[vol.Required("confirm_outdoor", default=False)] = bool
        return self.async_show_form(
            step_id="local_sensor_details", data_schema=vol.Schema(schema), errors=errors
        )
