"""Exclusion is reversible; optional/user-disabled entities must stay disabled."""

from unittest.mock import AsyncMock, patch

from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sscape import async_migrate_entry
from custom_components.sscape.api import PlaceCatalog, parse_snapshot


async def test_exclude_restore_preserves_ids_and_disabled_preferences(hass, payload):
    entry = MockConfigEntry(
        domain="sscape",
        version=2,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={"host": "https://example.com", "api_key": "fictional-key", "account_id": "account-1"},
        options={"places": ["place-1"], "follow_all_places": False, "weather_enabled": False},
    )
    entry.add_to_hass(hass)

    async def snapshot(ids):
        return parse_snapshot(payload if ids else {"states": [], "account_id": "account-1"})

    with (
        patch(
            "custom_components.sscape.api.SScapeClient.async_get_catalog",
            AsyncMock(return_value=PlaceCatalog("account-1", {"place-1": "Home"}, False)),
        ),
        patch("custom_components.sscape.api.SScapeClient.async_get_snapshot", side_effect=snapshot),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        registry = er.async_get(hass)

        def entity(key):
            name = registry.async_get_entity_id(
                "sensor", "sscape", f"{entry.unique_id}|place-1|{key}"
            )
            return registry.async_get(name)

        risk_id = entity("risk").entity_id
        optional = entity("lightning_distance")
        assert optional.disabled_by == er.RegistryEntryDisabler.INTEGRATION
        registry.async_update_entity(
            entity("situation").entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        hass.config_entries.async_update_entry(
            entry, options={"places": [], "follow_all_places": False, "weather_enabled": False}
        )
        await hass.async_block_till_done()
        assert entity("risk").disabled_by == er.RegistryEntryDisabler.INTEGRATION
        assert entity("risk").options["sscape"]["place_excluded"]
        hass.config_entries.async_update_entry(
            entry,
            options={"places": ["place-1"], "follow_all_places": False, "weather_enabled": False},
        )
        await hass.async_block_till_done()
        assert entity("risk").entity_id == risk_id
        assert entity("risk").disabled_by is None
        assert entity("lightning_distance").disabled_by == er.RegistryEntryDisabler.INTEGRATION
        assert entity("situation").disabled_by == er.RegistryEntryDisabler.USER


async def test_migration_freezes_previously_imported_places_not_future_ones(hass):
    entry = MockConfigEntry(
        domain="sscape",
        version=1,
        title="sScape",
        unique_id="https://example.com|account-1",
        data={},
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor", "sscape", f"{entry.unique_id}|previous-place|risk", config_entry=entry
    )
    assert await async_migrate_entry(hass, entry)
    assert entry.version == 2
    assert entry.options["places"] == ["previous-place"]
    assert entry.options["follow_all_places"] is False
