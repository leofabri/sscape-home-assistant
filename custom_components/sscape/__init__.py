"""sScape account-scoped environmental assessments for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SScapeClient
from .coordinator import SScapeCoordinator

type SScapeConfigEntry = ConfigEntry[SScapeCoordinator]
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SScapeConfigEntry) -> bool:
    """Authenticate and fetch data before setting up any entity."""
    coordinator = SScapeCoordinator(
        hass,
        entry,
        SScapeClient(
            entry.data[CONF_HOST],
            entry.data[CONF_API_KEY],
            async_get_clientsession(hass),
            hass.config.language,
        ),
    )
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: SScapeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SScapeConfigEntry) -> bool:
    """Remove listeners and periodic polling through the HA platform lifecycle."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
