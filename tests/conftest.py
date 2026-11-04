"""Offline Home Assistant fixtures. All places and keys here are fictional."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow loading the actual custom component in the HA test runtime."""


@pytest.fixture
def payload():
    """Match the existing GET /integrations/places/states Go envelope."""
    now = datetime.now(UTC)
    briefing = {
        "risk_level": "moderate",
        "situation_phase": "approach",
        "primary_signal": "storm_approach",
        "headline": "A storm is approaching Test place",
        "summary": "Radar shows an approaching cell. The exact track can change.",
        "recommended_action": "Check outdoor plans and follow official warnings.",
        "confidence_label": "track-based",
        "approach_eta_minutes": 18,
        "updated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=20)).isoformat(),
    }
    return {
        "states": [
            {
                "place": {"id": "place-1", "user_id": "account-1", "name": "Test place"},
                "live_state_available": True,
                "state": {"risk_level": "moderate", "briefing": deepcopy(briefing)},
            }
        ],
        "count": 1,
        "live_count": 1,
    }
