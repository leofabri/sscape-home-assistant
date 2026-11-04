"""sScape account-scoped environmental assessments for Home Assistant."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY, CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SScapeClient
from .const import CONF_FOLLOW_ALL, CONF_PLACES, CONF_WEATHER, DOMAIN
from .coordinator import SScapeCoordinator

type SScapeConfigEntry = ConfigEntry[SScapeCoordinator]
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.WEATHER]


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
    _update_place_registry(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: SScapeConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SScapeConfigEntry) -> bool:
    """Remove listeners and periodic polling through the HA platform lifecycle."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _update_place_registry(hass, entry):
    """Disable exclusions reversibly, preserving history and user-disabled entities."""
    registry = er.async_get(hass)
    allowed = set(entry.runtime_data.catalog.places)
    if not entry.options.get(CONF_FOLLOW_ALL, False):
        allowed.intersection_update(entry.options.get(CONF_PLACES, []))
    prefix = f"{entry.unique_id}|"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or not entity.unique_id.startswith(prefix):
            continue
        place_id = entity.unique_id[len(prefix) :].split("|", 1)[0]
        enabled = place_id in allowed and (
            entity.domain != "weather" or entry.options.get(CONF_WEATHER, True)
        )
        if not enabled and entity.disabled_by is None:
            options = dict(entity.options.get(DOMAIN, {}))
            options["place_excluded"] = True
            registry.async_update_entity_options(entity.entity_id, DOMAIN, options)
            registry.async_update_entity(
                entity.entity_id, disabled_by=er.RegistryEntryDisabler.INTEGRATION
            )
        elif (
            enabled
            and entity.disabled_by == er.RegistryEntryDisabler.INTEGRATION
            and entity.options.get(DOMAIN, {}).get("place_excluded")
        ):
            registry.async_update_entity(entity.entity_id, disabled_by=None)
            options = dict(entity.options.get(DOMAIN, {}))
            options.pop("place_excluded", None)
            registry.async_update_entity_options(entity.entity_id, DOMAIN, options or None)


async def async_migrate_entry(hass, entry):
    if entry.version > 2:
        return False
    if entry.version == 1:
        options = dict(entry.options)
        selected = options.get(CONF_PLACES, [])
        if not selected:
            prefix = f"{entry.unique_id}|"
            selected = sorted(
                {
                    entity.unique_id[len(prefix) :].split("|", 1)[0]
                    for entity in er.async_entries_for_config_entry(
                        er.async_get(hass), entry.entry_id
                    )
                    if entity.platform == DOMAIN and entity.unique_id.startswith(prefix)
                }
            )
        options.update({CONF_PLACES: selected, CONF_FOLLOW_ALL: False, CONF_WEATHER: True})
        hass.config_entries.async_update_entry(entry, version=2, options=options)
    return True
