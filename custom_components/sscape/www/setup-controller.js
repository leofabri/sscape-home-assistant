// Only authenticated, documented HA commands. No storage edits or existing-dashboard writes.
export async function action(hass, service, service_data = {}) {
  const result = await hass.callWS({type: "call_service", domain: "sscape", service,
    service_data, return_response: true});
  return result.response;
}

export async function createDashboard(hass, config, title, confirmed, enabled = []) {
  if (!confirmed || !hass.user?.is_admin || !config?.views?.length || !title.trim()) {
    throw new Error("confirmation_required");
  }
  // A new, unpredictable URL on every attempt. HA rejects collisions atomically.
  const url_path = "sscape-" + crypto.randomUUID().replaceAll("-", "");
  const expected_id = url_path.replaceAll("-", "_");
  let created;
  try {
    created = await hass.callWS({type: "lovelace/dashboards/create", url_path,
      title: title.trim().slice(0, 80), icon: "mdi:weather-partly-cloudy",
      show_in_sidebar: true, require_admin: true});
    if (!created || created.url_path !== url_path || created.id !== expected_id) {
      throw new Error("unexpected_dashboard_response");
    }
    await hass.callWS({type: "lovelace/config/save", url_path, config});
  } catch (error) {
    // Roll back only the exact dashboard just created, never an existing one.
    if (created?.url_path === url_path && created.id === expected_id) {
      try { await hass.callWS({type: "lovelace/dashboards/delete", dashboard_id: created.id}); }
      catch { /* Leave an empty new dashboard rather than deleting another target. */ }
    }
    throw error;
  }
  // Enabling optional entities is separately confirmed in the preview.
  const failed = [];
  for (const entity_id of [...new Set(enabled)]) {
    try { await hass.callWS({type: "config/entity_registry/update", entity_id, disabled_by: null}); }
    catch { failed.push(entity_id); }
  }
  return {url: "/" + url_path + "/sscape", failed};
}
