"""Useful health diagnostics with no keys, account IDs, place names or coordinates."""


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = getattr(entry, "runtime_data", None)
    result = {
        "integration_version": "0.4.0",
        "config_entry_state": entry.state.value,
        "runtime_available": coordinator is not None,
        "reauthentication_required": any(
            flow["context"].get("source") == "reauth"
            and flow["context"].get("entry_id") == entry.entry_id
            for flow in hass.config_entries.flow.async_progress()
        ),
    }
    if coordinator is None:
        return result
    return result | {
        "failure_reason": coordinator.failure_reason,
        "stopping": coordinator.stopping,
        "stream_connected": coordinator.stream_connected,
        "stream_reconnects": coordinator.stream_reconnects,
        "last_push_at": coordinator.last_push_at.isoformat() if coordinator.last_push_at else None,
        "last_update_success": coordinator.last_update_success,
        "poll_interval_seconds": coordinator.update_interval.total_seconds(),
        "place_count": len(coordinator.data.places),
        "fresh_place_count": sum(place.is_fresh() for place in coordinator.data.places.values()),
    }
