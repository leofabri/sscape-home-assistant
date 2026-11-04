"""The native setup panel is admin-only, shared across entries and removable."""

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape.setup_panel import DATA, setup_panel


async def test_headless_setup_does_not_load_frontend(hass):
    entry = MockConfigEntry(domain="sscape")
    await setup_panel(hass, entry)
    assert DATA not in hass.data


async def test_panel_admin_static_once_multi_entry_cleanup(hass):
    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")
    first, second = MockConfigEntry(domain="sscape"), MockConfigEntry(domain="sscape")
    callbacks = []
    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()) as static,
        patch(
            "homeassistant.components.panel_custom.async_register_panel", AsyncMock()
        ) as register,
        patch("homeassistant.components.frontend.async_remove_panel", MagicMock()) as remove,
        patch.object(first, "async_on_unload", side_effect=callbacks.append),
        patch.object(second, "async_on_unload", side_effect=callbacks.append),
    ):
        await setup_panel(hass, first)
        await setup_panel(hass, second)
        assert static.await_count == 1 and register.await_count == 1
        assert register.call_args.kwargs["require_admin"] is True
        callbacks[0]()
        remove.assert_not_called()
        callbacks[1]()
        remove.assert_called_once_with(hass, "sscape-setup")
        callbacks[1]()
        assert remove.call_count == 1


async def test_existing_panel_is_never_overwritten_or_removed(hass):
    from homeassistant.components.frontend import DATA_PANELS

    assert await async_setup_component(hass, "http", {})
    hass.config.components.add("frontend")
    hass.data[DATA_PANELS] = {"sscape-setup": object()}
    entry = MockConfigEntry(domain="sscape")
    callbacks = []
    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        patch(
            "homeassistant.components.panel_custom.async_register_panel", AsyncMock()
        ) as register,
        patch("homeassistant.components.frontend.async_remove_panel", MagicMock()) as remove,
        patch.object(entry, "async_on_unload", side_effect=callbacks.append),
    ):
        await setup_panel(hass, entry)
        callbacks[0]()
        register.assert_not_called()
        remove.assert_not_called()
