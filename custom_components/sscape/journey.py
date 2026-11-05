"""One coherent setup journey, also reusable to edit existing integrations."""

from time import monotonic

import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .notifications import async_notify, notification_choices, target_options


class JourneyMixin:
    async def _save(self, updates):
        self._draft_options = self._options() | updates
        steps = getattr(self, "_journey_steps", [])
        if steps:
            return await getattr(self, "async_step_" + steps.pop(0))()
        return self._finish_setup()

    def _finish_setup(self):
        if getattr(self, "_initial_setup", False):
            return self.async_create_entry(
                title="sScape",
                data=self._pending,
                options=self._options(),
                description="setup_ready",
                description_placeholders={"setup_url": "/sscape-setup"},
            )
        return self.async_create_entry(title="", data=self._options())

    async def async_step_preferences(self, user_input=None):
        opts = self._options()
        if user_input is not None:
            self._draft_options = opts | {
                "weather_enabled": user_input.get("weather_enabled", True),
                "dashboard_profile": user_input.get("dashboard_profile", "essential"),
            }
            self._journey_steps = [
                step
                for key, step in (
                    ("configure_local", "local_sensors"),
                    ("configure_notifications", "notifications"),
                    ("configure_assist", "assist"),
                )
                if user_input.get(key)
            ] + ["review"]
            return await self._save({})
        return self.async_show_form(
            step_id="preferences",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "weather_enabled", default=opts.get("weather_enabled", True)
                    ): bool,
                    vol.Optional(
                        "dashboard_profile", default=opts.get("dashboard_profile", "essential")
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["essential", "detailed"], translation_key="dashboard_profile"
                        )
                    ),
                    vol.Optional("configure_local", default=False): bool,
                    vol.Optional("configure_notifications", default=False): bool,
                    vol.Optional("configure_assist", default=False): bool,
                }
            ),
        )

    async def async_step_review(self, user_input=None):
        if user_input is not None:
            return self._finish_setup()
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={
                "targets": ", ".join(self._names().values())[:1200] or "—",
                "count": str(len(self._names())),
            },
        )

    async def async_step_notifications(self, user_input=None):
        opts = self._options()
        errors = {}
        choices = notification_choices(self.hass)
        if user_input is not None:
            updates = {
                "episode_notifications": user_input.get("episode_notifications", False),
                "episode_kinds": user_input.get("episode_kinds", ["storm", "official"]),
            }
            try:
                if updates["episode_notifications"]:
                    updates |= target_options(self.hass, user_input.get("notification_target", ""))
            except HomeAssistantError:
                errors["base"] = "invalid_notify_service"
            else:
                self._draft_options = opts | updates
                if updates["episode_notifications"]:
                    self._notification_test_sent = False
                    return await self.async_step_notification_test()
                return await self._save({})
        current = (
            "entity:" + opts["notify_entity_id"]
            if opts.get("notify_entity_id")
            else "service:" + opts["notify_service"]
            if opts.get("notify_service")
            else ""
        )
        if current not in {item["value"] for item in choices}:
            current = ""
        return self.async_show_form(
            step_id="notifications",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "episode_notifications", default=opts.get("episode_notifications", False)
                    ): bool,
                    vol.Optional("notification_target", default=current): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[{"value": "", "label": "—"}, *choices]
                        )
                    ),
                    vol.Optional(
                        "episode_kinds", default=opts.get("episode_kinds", ["storm", "official"])
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["storm", "official", "earthquake", "thermal"],
                            multiple=True,
                            translation_key="episode_kinds",
                        )
                    ),
                }
            ),
        )

    async def async_step_notification_test(self, user_input=None):
        errors = {}
        if user_input is not None:
            action = user_input.get("test_action", "skip")
            if action in ("received", "skip"):
                if action == "received" and not getattr(self, "_notification_test_sent", False):
                    errors["base"] = "test_not_sent"
                else:
                    self._draft_options = self._options() | {
                        "notification_receipt_confirmed": action == "received"
                    }
                    return await self._save({})
            elif action == "send":
                if monotonic() - getattr(self, "_last_notification_test", -1000) < 30:
                    errors["base"] = "test_rate_limited"
                else:
                    self._last_notification_test = monotonic()
                    try:
                        await async_notify(
                            self.hass,
                            self._options(),
                            {
                                "title": "sScape · Test",
                                "message": "Notifica di prova sScape. Nessuna allerta reale."
                                if self.hass.config.language.startswith("it")
                                else "sScape test notification. This is not a real warning.",
                            },
                        )
                    except (HomeAssistantError, TimeoutError):
                        errors["base"] = "notification_test_failed"
                    else:
                        self._notification_test_sent = True
        return self.async_show_form(
            step_id="notification_test",
            errors=errors,
            description_placeholders={
                "test_state": "✓" if getattr(self, "_notification_test_sent", False) else "—"
            },
            data_schema=vol.Schema(
                {
                    vol.Required("test_action", default="skip"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=["send", "received", "skip"],
                            translation_key="notification_test",
                        )
                    )
                }
            ),
        )

    async def async_step_assist(self, user_input=None):
        opts = self._options()
        if user_input is not None:
            self._draft_options = opts | {
                "assist_enabled": user_input.get("assist_enabled", False),
                "assist_allow_anonymous": user_input.get("assist_allow_anonymous", False),
            }
            if user_input.get("assist_enabled") and user_input.get("expose_selected_entities"):
                self._draft_options["assist_exposure_pending"] = list(self._names())
            if user_input.get("configure_aliases"):
                return await self.async_step_assist_target()
            return await self._save({})
        return self.async_show_form(
            step_id="assist",
            data_schema=vol.Schema(
                {
                    vol.Optional("assist_enabled", default=opts.get("assist_enabled", False)): bool,
                    vol.Optional("expose_selected_entities", default=False): bool,
                    vol.Optional("configure_aliases", default=False): bool,
                    vol.Optional(
                        "assist_allow_anonymous", default=opts.get("assist_allow_anonymous", False)
                    ): bool,
                }
            ),
        )

    async def async_step_assist_target(self, user_input=None):
        if user_input is not None:
            self._alias_target = user_input["target"]
            return await self.async_step_assist_aliases()
        return self.async_show_form(
            step_id="assist_target",
            data_schema=vol.Schema(
                {
                    vol.Required("target"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {"value": key, "label": name} for key, name in self._names().items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_assist_aliases(self, user_input=None):
        errors = {}
        opts = self._options()
        aliases = dict(opts.get("assist_aliases", {}))
        current = aliases.get(self._alias_target, [])
        if user_input is not None:
            values = list(
                dict.fromkeys(
                    word.strip() for word in user_input.get("place_aliases", []) if word.strip()
                )
            )
            reserved = {
                name.casefold() for key, name in self._names().items() if key != self._alias_target
            }
            reserved.update(
                word.casefold()
                for key, words in aliases.items()
                if key != self._alias_target
                for word in words
            )
            if len(values) > 8 or any(
                len(word) > 80 or word.casefold() in reserved for word in values
            ):
                errors["base"] = "invalid_aliases"
            else:
                aliases[self._alias_target] = values
                return await self._save({"assist_aliases": aliases})
        return self.async_show_form(
            step_id="assist_aliases",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Optional("place_aliases", default=current): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=current, multiple=True, custom_value=True
                        )
                    )
                }
            ),
            description_placeholders={"target": self._names().get(self._alias_target, "")},
        )
