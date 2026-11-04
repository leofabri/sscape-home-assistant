# Changelog

## 0.2.0

- Explicit place selection during setup; an empty selection means no places.
- Optional follow-all mode, including future places accessible to the API key.
- Migration freezes previously imported places; stable entity IDs and user-disabled preferences are preserved.
- Server-enforced, place-restricted API keys, configurable in the sScape profile.
- Native weather entity with hourly model forecasts, source attribution and original valid times.
- Storm-approach and lightning-activity binary sensors; optional official-warning, thermal-detection, earthquake and reported-hail sensors.
- Optional lightning counts/distances and recent-earthquake counts; absent evidence stays unknown.
- Importable risk-change automation blueprint, including confirmed recovery and no false all-clear after an outage.
- Current assessments no longer depend on the last persisted notification transition (server update required).

Requires the sScape backend with the `home-assistant.v2` selection contract.
Update HACS, restart Home Assistant, then review the integration options.

## 0.1.0

First public custom integration release.

- Home Assistant UI setup with an API key and a complete sScape server address.
- One device per followed place, shared polling, selectable places and update interval.
- Risk, situation, estimated approaching-storm arrival and analysis timestamp sensors.
- Diagnostic binary sensor for unavailable/expired analysis; missing data never becomes low risk.
- Account-safe key replacement, outage backoff and rate-limit handling.
- Italian and English translations and original sScape brand icon.
- Offline tests on Home Assistant 2026.2.3 and 2026.10.4, including loopback HTTP integration.

Install through HACS as a custom repository or copy `custom_components/sscape`
manually. This is not a Home Assistant Core integration or a default HACS
catalogue listing. Actual installation in the user's Home Assistant has not
been verified. Existing sScape API account-plan permissions apply.
