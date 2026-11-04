"""Useful health diagnostics with no keys, account IDs, place names or coordinates."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    return {
        "integration_version": "0.1.0",
        "last_update_success": coordinator.last_update_success,
        "poll_interval_seconds": coordinator.update_interval.total_seconds(),
        "place_count": len(coordinator.data.places),
        "fresh_place_count": sum(place.is_fresh() for place in coordinator.data.places.values()),
    }
