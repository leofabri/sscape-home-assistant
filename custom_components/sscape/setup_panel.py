"""Admin setup panel. Dashboard writes go through HA's authenticated WebSocket API."""

from pathlib import Path

from homeassistant.core import callback

PANEL = "sscape-setup"
DATA = "sscape_setup_panel"


async def setup_panel(hass, entry):
    # Headless HA remains supported; frontend is an optional after_dependency.
    if "frontend" not in hass.config.components:
        return
    from homeassistant.components import frontend
    from homeassistant.components.http import StaticPathConfig
    from homeassistant.components.panel_custom import async_register_panel

    state = hass.data.setdefault(DATA, {"entries": set(), "static": False, "owned": False})
    if not state["static"]:
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/sscape-static", str(Path(__file__).parent / "www"), False)]
        )
        state["static"] = True
    if not state["entries"] and PANEL not in hass.data.get(frontend.DATA_PANELS, {}):
        await async_register_panel(
            hass,
            webcomponent_name="sscape-setup",
            frontend_url_path=PANEL,
            sidebar_title="sScape setup",
            sidebar_icon="mdi:weather-partly-cloudy",
            module_url="/sscape-static/setup.js?v=0.6.0",
            require_admin=True,
        )
        state["owned"] = True
    state["entries"].add(entry.entry_id)

    @callback
    def remove():
        state["entries"].discard(entry.entry_id)
        if not state["entries"] and state["owned"]:
            frontend.async_remove_panel(hass, PANEL)
            state["owned"] = False

    entry.async_on_unload(remove)
