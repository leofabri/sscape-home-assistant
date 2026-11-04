"""Do not confuse proximity, broad warnings, or missing observations with local impact."""

from custom_components.sscape.api import parse_snapshot


def test_zero_lightning_is_not_a_detection_even_with_a_distant_nearest_event(payload):
    raw = payload["states"][0]["state"]["briefing"]
    raw.update(lightning_count_20m=0, nearest_lightning_distance_km=84)
    place = parse_snapshot(payload).places["place-1"]
    assert place.signal("lightning_detected") is False
    raw.pop("lightning_count_20m")
    assert place.signal("lightning_detected") is None


def test_broad_official_context_is_not_a_local_warning(payload):
    raw = payload["states"][0]["state"]["briefing"]
    raw.update(broad_official_alert_count=166, broad_official_alert_severity="extreme")
    place = parse_snapshot(payload).places["place-1"]
    assert place.signal("official_warning") is None
    raw["official_alert_count"] = 1
    assert place.signal("official_warning") is True


def test_thermal_source_and_observation_not_a_confirmed_fire_or_all_clear(payload):
    raw = payload["states"][0]["state"]["briefing"]
    place = parse_snapshot(payload).places["place-1"]
    assert place.signal("wildfire_detected") is None
    raw["environmental_signals"] = [
        {
            "kind": "fire",
            "source": "NASA_FIRMS",
            "observed_at": "2026-11-04T10:00:00Z",
            "distance_km": 10,
        }
    ]
    assert place.signal("wildfire_detected") is True
    raw["environmental_signals"] = []
    assert place.signal("wildfire_detected") is None
