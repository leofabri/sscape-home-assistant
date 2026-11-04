"""Rich, bounded evidence never turns catalogue context into an impact claim."""

from datetime import UTC, datetime, timedelta

from custom_components.sscape.api import parse_snapshot
from custom_components.sscape.hazards import hazards_for


def test_detailed_warning_earthquake_and_thermal_context(payload):
    now = datetime.now(UTC)
    b = payload["states"][0]["state"]["briefing"]
    b.update(
        official_alert_count=1,
        official_alert_severity="severe",
        official_alert_type="wind",
        official_alert_headline="Wind warning",
        official_alert_onset=now.isoformat(),
        official_alert_expires=(now + timedelta(hours=3)).isoformat(),
        recent_earthquakes_24h=[
            {
                "id": "quake-one",
                "magnitude": 4.1,
                "distance_km": 30,
                "latitude": 45,
                "longitude": 9,
                "depth_km": 10,
                "area": "Test area",
                "occurred_at": now.isoformat(),
            }
        ],
        environmental_signals=[
            {
                "id": "thermal-one",
                "kind": "fire",
                "source": "test-satellite",
                "observed_at": now.isoformat(),
                "distance_km": 40,
                "frp_mw": 5,
            }
        ],
    )
    value = hazards_for(parse_snapshot(payload).places["place-1"])
    assert value["available"]
    assert value["official_warnings"][0]["headline"] == "Wind warning"
    assert value["official_warnings"][0]["expires_at"] == b["official_alert_expires"]
    assert value["earthquakes"][0]["area"] == "Test area"
    assert value["thermal_detections"][0]["kind"] == "thermal_detection"
    assert not value["thermal_detections"][0]["confirmed_fire"]


def test_expired_analysis_and_old_events_not_replayed(payload):
    b = payload["states"][0]["state"]["briefing"]
    b["recent_earthquakes_24h"] = [
        {
            "id": "old",
            "magnitude": 4,
            "distance_km": 3,
            "occurred_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        }
    ]
    assert not hazards_for(parse_snapshot(payload).places["place-1"])["earthquakes"]
    b["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert hazards_for(parse_snapshot(payload).places["place-1"]) == {
        "available": False,
        "reason": "assessment_unavailable",
    }
