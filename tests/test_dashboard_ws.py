"""Exercise the actual native HA WebSocket create/save/delete schemas offline."""

from homeassistant.components import frontend, websocket_api
from homeassistant.components.lovelace import LovelaceData, dashboard
from homeassistant.components.lovelace.const import (
    LOVELACE_DATA,
    STORAGE_DASHBOARD_CREATE_FIELDS,
    STORAGE_DASHBOARD_UPDATE_FIELDS,
)
from homeassistant.components.lovelace.websocket import websocket_lovelace_save_config
from homeassistant.helpers import collection


async def test_native_dashboard_create_save_collision_and_cleanup(hass, hass_ws_client):
    # Isolated native test collection, not another collection in the integration.
    hass.data[LOVELACE_DATA] = LovelaceData("storage", {}, None, {})
    storage = dashboard.DashboardsCollection(hass)

    async def changed(change_type, item_id, item):
        url = item["url_path"]
        if change_type == collection.CHANGE_ADDED:
            hass.data[LOVELACE_DATA].dashboards[url] = dashboard.LovelaceStorage(hass, item)
            frontend.async_register_built_in_panel(
                hass, "lovelace", frontend_url_path=url, require_admin=True
            )
        elif change_type == collection.CHANGE_REMOVED:
            frontend.async_remove_panel(hass, url)
            await hass.data[LOVELACE_DATA].dashboards.pop(url).async_delete()

    storage.async_add_listener(changed)
    dashboard.DashboardsCollectionWebSocket(
        storage,
        "lovelace/dashboards",
        "dashboard",
        STORAGE_DASHBOARD_CREATE_FIELDS,
        STORAGE_DASHBOARD_UPDATE_FIELDS,
    ).async_setup(hass)
    websocket_api.async_register_command(hass, websocket_lovelace_save_config)
    client = await hass_ws_client(hass)
    create = {
        "type": "lovelace/dashboards/create",
        "url_path": "sscape-test-dashboard",
        "title": "Fictional dashboard",
        "icon": "mdi:weather-partly-cloudy",
        "show_in_sidebar": True,
        "require_admin": True,
    }
    await client.send_json({"id": 1, **create})
    result = await client.receive_json()
    assert result["success"] and result["result"]["id"] == create["url_path"].replace("-", "_")
    config = {
        "title": "sScape",
        "views": [
            {
                "title": "Test",
                "path": "sscape",
                "cards": [{"type": "tile", "entity": "sensor.fictional"}],
            }
        ],
    }
    await client.send_json(
        {"id": 2, "type": "lovelace/config/save", "url_path": create["url_path"], "config": config}
    )
    assert (await client.receive_json())["success"]
    assert await hass.data[LOVELACE_DATA].dashboards[create["url_path"]].async_load(False) == config
    await client.send_json({"id": 3, **create})
    assert not (await client.receive_json())["success"]
    await client.send_json(
        {"id": 4, "type": "lovelace/dashboards/delete", "dashboard_id": result["result"]["id"]}
    )
    assert (await client.receive_json())["success"]
    assert not hass.data[LOVELACE_DATA].dashboards
