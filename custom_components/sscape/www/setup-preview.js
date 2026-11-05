// Read-only live preview: documented native helpers when loaded, explicit fallback otherwise.
const labels = {
  low: ["Basso", "Low"], moderate: ["Moderato", "Moderate"], high: ["Alto", "High"],
  severe: ["Elevato", "Severe"], unknown: ["Dato non fornito", "Value not supplied"],
  unavailable: ["Collegamento non disponibile", "Connection unavailable"],
  expected: ["Previste", "Forecast"], not_expected: ["Non previste", "Not forecast"],
  data_incomplete: ["Previsione incompleta", "Incomplete forecast"]
};

export function element(tag, text, props = {}) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  Object.assign(node, props);
  return node;
}

function stateLabel(hass, entity) {
  const state = hass.states?.[entity];
  if (!state) return hass.language?.startsWith("it") ? "Indicatore da attivare" : "Indicator to enable";
  if (["unknown", "unavailable"].includes(state.state)) return state.attributes.status_message || labels[state.state][hass.language?.startsWith("it") ? 0 : 1];
  const label = labels[state.state]?.[hass.language?.startsWith("it") ? 0 : 1] || state.state;
  return label + (state.attributes.unit_of_measurement ? " " + state.attributes.unit_of_measurement : "");
}

function fallbackCard(hass, card, target) {
  if (card.type === "heading") return element("h3", card.heading);
  if (card.type === "grid") {
    const grid = element("div", null, {className: "preview-grid"});
    grid.style.setProperty("--preview-columns", String(Math.min(2, card.columns || 2)));
    for (const child of card.cards || []) {
      const node = fallbackCard(hass, child, target); if (node) grid.append(node);
    }
    return grid;
  }
  if (card.type === "conditional") {
    if (!(card.conditions || []).every(c => (hass.states?.[c.entity]?.state || "unknown") !== c.state_not)) return null;
    return fallbackCard(hass, card.card, target);
  }
  const box = element("ha-card", null, {className: "preview-card"});
  if (card.type === "markdown") {
    box.append(element("p", target?.summary?.headline || "—", {className:"preview-headline"}));
  } else if (card.type === "weather-forecast") {
    const state = hass.states?.[card.entity];
    const icon = element("ha-icon"); icon.icon = "mdi:weather-partly-cloudy";
    box.append(icon, element("span", card.name || target?.name || "sScape"));
    const value = state?.attributes?.temperature;
    box.append(element("strong", typeof value === "number" ? value + " " + (state.attributes.temperature_unit || "") : stateLabel(hass, card.entity)));
    for (const row of target?.summary?.next || []) {
      const text = row.text || (row.value != null ? row.value + " " + row.unit : "");
      let timing = "";
      if (row.start && row.end) {
        const clock = value => new Date(value).toLocaleTimeString(hass.language || "en", {hour:"2-digit",minute:"2-digit"});
        timing = " · " + clock(row.start) + "–" + clock(row.end);
      }
      box.append(element("small", row.label + (text ? ": " + text : "") + timing));
    }
  } else if (card.type === "button") {
    box.append(element("span", card.name));
  } else {
    const state = hass.states?.[card.entity];
    box.append(element("small", card.name || state?.attributes?.friendly_name || "sScape"),
      element("strong", stateLabel(hass, card.entity)));
  }
  return box;
}

export async function renderPreview(container, hass, view, target) {
  container.replaceChildren();
  let helpers;
  if (typeof window.loadCardHelpers === "function") {
    try { helpers = await window.loadCardHelpers(); } catch { /* use the labelled fallback */ }
  }
  if (!container.isConnected) return;
  container.dataset.renderer = helpers ? "native" : "simplified";
  const caption = hass.language?.startsWith("it")
    ? (helpers ? "Anteprima con le card native di Home Assistant" : "Anteprima semplificata con i dati attuali. Le card native possono avere differenze grafiche.")
    : (helpers ? "Preview using native Home Assistant cards" : "Simplified preview using current data. Native cards may look different.");
  container.append(element("p", caption, {className:"preview-caption"}));
  if (helpers) {
    const body = element("div", null, {className:"native-preview"}); body.inert = true;
    body.setAttribute("aria-hidden", "true");
    for (const config of view.cards || []) {
      try { const card = helpers.createCardElement(config); card.hass = hass; body.append(card); }
      catch { const card = fallbackCard(hass, config, target); if (card) body.append(card); }
    }
    const description = element("p", target?.summary?.headline || view.title, {className:"sr-only"});
    container.append(description, body);
  } else {
    for (const config of view.cards || []) {
      const card = fallbackCard(hass, config, target); if (card) container.append(card);
    }
  }
}

export function updatePreviewHass(container, hass) {
  for (const card of container?.querySelector(".native-preview")?.children || []) card.hass = hass;
}
