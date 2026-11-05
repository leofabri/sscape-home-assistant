# Changelog

## 0.6.0

- One initial setup journey for saved places, Home Assistant's home location or manual coordinates, followed by optional local instruments, notifications and Assist; options are saved only on final confirmation. Technical polling/push settings live under Advanced.
- Friendly notification destinations, including modern notify entities. Explicit test, retry limit and manual receipt confirmation; an accepted service call never claims phone delivery.
- Per-place Assist alias chips and one-time, opt-in exposure of selected entities. An explicit admin-only test uses the built-in HA agent, not a default external agent or voice hardware.
- Graphical dashboard preview with Essential/Detailed profiles and topic groups. Native card helpers are used when already loaded; the fallback is clearly labelled simplified. Native previews are inert. Existing dashboards and user-disabled entities are never overwritten.
- New dashboard story: Now / Next hours / Data and coverage, with optional evidence sections. The all-targets view stays within the chosen account, even with identical place names across accounts.
- Missing current values now remain `unknown`, rather than pretending the connection is offline; not-applicable, missing, stale and disconnected have distinct explanations. New per-place connection/assessment diagnostic stays readable during outages. No absent metric becomes zero or an all-clear.
- A still-fresh hourly forecast remains usable during a transient hazard-assessment outage; revoked permissions, expired forecasts and stopping entries still block access. Existing meteorological algorithms, entity IDs and history are preserved.

Update through HACS and fully restart Home Assistant. Generate a new dashboard in **sScape setup** to use the new layout; old dashboards are intentionally left intact.

## 0.5.1

- Geographic requests preserve server `Retry-After` so rate limits are not mistaken for a temporarily empty point. Includes all 0.5.0 features.

## 0.5.0

- Coordinate targets using the existing geographic API, without creating saved places; mixed and coordinate-only modes preserve account and key-scope boundaries.
- Optional official-warning, earthquake, thermal-detection, snowfall and visibility detail, with original source and forecast semantics.
- Local HA instrument mirrors with unit conversion and timestamp/quality checks; measurements are not uploaded or used to alter risk.
- Opt-in episode notifications, no startup replay or proximity-based event merging.
- Admin-only dashboard setup panel with preview, explicit creation consent and exact-new-dashboard rollback safety.
- Opt-in native Assist phrases, per-place aliases, exposure checks and separate consent for requests without a user identity.

## 0.4.0

- New precipitation-outlook sensor: forecast, not forecast, or incomplete data. No false dry result across missing hours.
- Optional forecast timestamps for the next precipitation window, peak one-hour gust period and coldest hourly estimate. No exact-onset, ground-frost or mixed-duration gust claims.
- Read-only `sscape.get_outlook` action for cached 1–24 hour planning windows, coverage and attribution. Respects the API's declared next-complete-hour start.
- `sscape.get_dashboard` can generate an overview and per-place views for all explicitly imported places. Optional timing cards are conditional; unavailable risk information remains visible.
- Place-map links use the existing supported deep link. Current storm URLs are returned by the briefing action instead of baking a soon-outdated storm ID into a long-lived dashboard.
- Opt-in precipitation blueprint: user-selected actions, confirmation time, no startup/restoration/attribute-change triggers.
- Shared initial forecast requests are coordinator-owned, so removing one sensor cannot cancel the weather entity's data.
- Safe diagnostics before setup/after unload and distinct authentication, permission and connection failure reasons. Includes all 0.3.2 unload/reauth fixes.

## 0.3.2

- Fixed `FAILED_UNLOAD` on Home Assistant 2026.10.4 with push enabled: the old stream-cancellation callback returned a boolean where HA expected no result or a coroutine.
- Push now belongs to the config-entry lifecycle and is stopped and awaited before unloading entity platforms. Old coordinators cannot restart it.
- Fixed double reload after reauthentication, including unchanged-key recovery and authentication failures during initial setup.
- Ignore late discovery and successful/authentication responses from a stopping or removed entry, including weather requests. No orphan devices or reauthentication flows are created.
- Added real config-entry and loopback HTTP/SSE regression tests. Entity/device IDs and history are preserved.
- If the old version is already in `FAILED_UNLOAD`, update through HACS and restart Home Assistant fully once. Do not delete/recreate the integration or edit its registries.

## 0.3.1

- The read-only briefing action now reports unavailable after a connection/authentication failure, even when a previously fetched assessment remains in memory. No stale actionable text is returned.
- Added an actual Home Assistant service regression test for this outage path.

## 0.3.0

- Place-scoped outbound push updates with reconnect, backoff and polling reconciliation. No inbound Home Assistant port or new broker.
- Compact, paginated snapshots with revision-based incremental responses; old backends keep the 0.2 polling path.
- Explicit record counts and per-domain freshness/coverage. Unknown coverage is not an all-clear; assessment time is not observation time.
- Lightning distance uses the same 20-minute window as the activity count on updated servers.
- Storm phase, arrival interval, radar observation time and data-quality entities.
- Optional station temperature/humidity/wind/gust sensors with actual observation time, distance, units, attribution and quality checks.
- Optional numeric model-forecast sensors for minimum temperature, precipitation and peak gust, sharing the native weather request.
- Native change events with no startup replay; importable event and numeric-threshold blueprints.
- Read-only `sscape.get_briefing` and `sscape.get_dashboard` actions; generated dashboards use real entity IDs and native cards only.
- Repairs warning for persistently stale observations, without treating unknown coverage as a broken feed.
- Server security fix: place-restricted/read-only place keys cannot use the global WebSocket. The new place stream checks ownership and key permissions and closes revoked connections.

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
