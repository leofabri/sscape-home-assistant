# Changelog

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
