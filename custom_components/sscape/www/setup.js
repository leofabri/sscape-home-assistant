import {action, createDashboard} from "./setup-controller.js";

// HA's theme, typography and icon family, with no CDN or third-party UI library.
class SScapeSetup extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode: "open"});
    this.targets = []; this.selected = ""; this.all = false; this.busy = false;
    this.config = null; this.optional = []; this.enabled = new Set();
  }
  set hass(value) {
    this._hass = value;
    if (!this.started && this.isConnected) this.start();
  }
  connectedCallback() { if (this._hass && !this.started) this.start(); }
  text(it, en) { return this._hass.language?.startsWith("it") ? it : en; }
  node(tag, text, props = {}) {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    Object.assign(el, props); return el;
  }
  async start() {
    this.started = true; this.render();
    if (!this._hass.user?.is_admin) return;
    try {
      const data = await action(this._hass, "get_setup");
      this.targets = data.targets || []; this.selected = this.targets[0]?.device_id || "";
    } catch { this.error = this.text("Non riesco a leggere i luoghi. Controlla l’integrazione.", "Unable to load targets. Check the integration."); }
    this.render();
  }
  check(label, checked, change) {
    const row = this.node("label", null, {className: "check"});
    const input = this.node("input", null, {type: "checkbox", checked, disabled: this.busy});
    input.addEventListener("change", () => change(input.checked));
    row.append(input, this.node("span", label)); return row;
  }
  button(label, fn, disabled = false) {
    const button = this.node("button", label, {type: "button", disabled: this.busy || disabled});
    button.addEventListener("click", fn); return button;
  }
  invalidate() { this.config = null; this.confirmed = false; this.enabled.clear(); this.render(); }
  async preview() {
    if (this.busy) return;
    this.busy = true; this.error = ""; this.render();
    try {
      const data = await action(this._hass, "get_dashboard", {
        device_id: this.selected, all_selected_places: this.all, include_optional: true});
      this.config = data.config;
      const active = this.all ? this.targets : this.targets.filter(t => t.device_id === this.selected);
      this.optional = active.flatMap(t => Object.entries(t.entities)
        .filter(([,e]) => e.disabled && e.disabled_by === "integration")
        .map(([key,e]) => ({...e, name: t.name + " · " + (e.name || key)})));
      // Only optional entities present in this exact preview may be enabled.
      const ids = new Set(JSON.stringify(this.config).match(/(?:sensor|binary_sensor|weather|event)\.[a-z0-9_]+/g) || []);
      this.optional = this.optional.filter(e => ids.has(e.entity_id));
      this.enabled = new Set(this.optional.map(e => e.entity_id));
      this.confirmed = false;
    } catch { this.error = this.text("Anteprima non disponibile. Nessuna dashboard è stata modificata.", "Preview unavailable. No dashboard was changed."); }
    this.busy = false; this.render();
  }
  async create() {
    if (this.busy || !this.confirmed || !this.config) return;
    this.busy = true; this.error = ""; this.render();
    try {
      const result = await createDashboard(this._hass, this.config, this.title || "sScape", true, [...this.enabled]);
      this.created = result.url;
      if (result.failed.length) this.error = this.text("Dashboard creata. Alcune entità opzionali non sono state abilitate: puoi attivarle nelle impostazioni.", "Dashboard created. Some optional entities could not be enabled; use entity settings.");
    } catch { this.error = this.text("Creazione non riuscita. Le dashboard esistenti non sono state toccate.", "Creation failed. Existing dashboards were not changed."); }
    this.busy = false; this.render();
  }
  render() {
    const root = this.shadowRoot; root.replaceChildren();
    const style = this.node("style", `
      :host{display:block;height:100%;overflow:auto;background:var(--primary-background-color);color:var(--primary-text-color);font:var(--paper-font-body1_-_font-family,Roboto,sans-serif)}
      *{box-sizing:border-box}header{display:flex;align-items:center;gap:12px;padding:12px 16px;background:var(--app-header-background-color);color:var(--app-header-text-color)}
      h1{font-size:20px;font-weight:500;margin:0}ha-menu-button{flex-shrink:0}main{max-width:880px;margin:auto;padding:24px 20px 56px}h2{font-size:22px;font-weight:500;margin:0 0 8px}h3{font-size:17px;margin:0 0 12px}
      p{color:var(--secondary-text-color);line-height:1.5;margin:8px 0 20px}.step{border-bottom:1px solid var(--divider-color);padding:24px 0}.step:first-child{padding-top:0}
      label.field{display:grid;gap:8px;margin:16px 0}select,input[type=text]{font:inherit;padding:12px;background:var(--card-background-color);color:var(--primary-text-color);border:1px solid var(--divider-color);border-radius:8px;width:100%;min-height:44px}
      .check{display:flex;align-items:center;gap:12px;min-height:44px;line-height:1.45;padding:6px 0}.check input{width:20px;height:20px;accent-color:var(--primary-color);flex-shrink:0}
      button,.open{font:inherit;min-height:44px;border:0;border-radius:8px;padding:12px 18px;background:var(--primary-color);color:var(--text-primary-color);cursor:pointer;text-decoration:none;display:inline-block}button:disabled{opacity:.5;cursor:default}:focus-visible{outline:3px solid var(--primary-color);outline-offset:3px}
      .views{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:12px;margin:20px 0}.view{background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:12px;padding:18px}.view ul{padding-left:20px;margin:0;color:var(--secondary-text-color);line-height:1.65}.error{color:var(--error-color);padding:12px 0}.links{display:flex;gap:20px;flex-wrap:wrap;margin-top:24px}a{color:var(--primary-color)}
      @media(max-width:500px){main{padding:20px 16px 48px}button,.open{width:100%;text-align:center}}`);
    root.append(style);
    const header = this.node("header"); const menu = this.node("ha-menu-button"); menu.hass = this._hass;
    header.append(menu, this.node("h1", "sScape")); root.append(header);
    const main = this.node("main"); root.append(main);
    if (!this._hass.user?.is_admin) { main.append(this.node("p", this.text("Accesso riservato agli amministratori.", "Administrator access required."))); return; }
    main.append(this.node("h2", this.text("I tuoi luoghi, a colpo d’occhio", "Your places at a glance")),
      this.node("p", this.text("Prepara una dashboard nativa. Prima l’anteprima, poi decidi: nessuna dashboard esistente verrà modificata.", "Prepare a native dashboard. Preview first, then decide: existing dashboards will not be changed.")));
    if (this.error) main.append(this.node("div", this.error, {className:"error", role:"alert"}));
    if (this.created) { main.append(this.node("h3", this.text("Dashboard pronta", "Dashboard ready")), this.node("a", this.text("Apri dashboard", "Open dashboard"), {href:this.created,className:"open"})); return; }
    if (!this.targets.length) { main.append(this.node("p", this.text("Non ci sono luoghi attivi. Configura prima un luogo salvato o un punto per coordinate.", "No active targets. Configure a saved place or coordinate target first."))); }
    else {
      const step = this.node("section", null, {className:"step"}); main.append(step);
      step.append(this.node("h3", this.text("1 · Scegli cosa vedere", "1 · Choose what to see")));
      const label = this.node("label", this.text("Luogo", "Place"), {className:"field"});
      const select = this.node("select", null, {disabled:this.busy || this.all,ariaLabel:this.text("Luogo","Place")});
      for (const t of this.targets) select.append(this.node("option", t.name, {value:t.device_id, selected:t.device_id===this.selected}));
      select.addEventListener("change", () => {this.selected=select.value;this.invalidate();});label.append(select); step.append(label);
      step.append(this.check(this.text("Tutti i luoghi selezionati nell’integrazione", "All targets selected in the integration"), this.all, v=>{this.all=v;this.invalidate();}));
      const title = this.node("label", this.text("Titolo della nuova dashboard", "New dashboard title"), {className:"field"});
      const input = this.node("input", null, {type:"text",maxLength:80,value:this.title || "sScape",disabled:this.busy});
      input.addEventListener("input", () => {this.title=input.value;});title.append(input);step.append(title);
      step.append(this.button(this.text("Prepara anteprima", "Prepare preview"), ()=>this.preview(), !this.selected));
    }
    if (this.config) {
      const step = this.node("section", null, {className:"step"}); main.append(step);
      step.append(this.node("h3", this.text("2 · Anteprima dei contenuti", "2 · Content preview")));
      const views = this.node("div", null, {className:"views"}); step.append(views);
      for (const view of this.config.views) {
        const card = this.node("div", null, {className:"view"});card.append(this.node("h3", view.title));
        const list=this.node("ul");
        const types=new Set((view.cards||[]).map(c=>c.type));
        for (const [type,it,en] of [["weather-forecast","Previsione oraria","Hourly forecast"],["grid","Rischio, osservazioni e indicatori","Risk, observations and indicators"],["entities","Riepilogo dei luoghi","Places overview"],["conditional","Orari e dettagli quando disponibili","Timing and details when available"],["button","Collegamento alla mappa","Map link"]]) if(types.has(type)) list.append(this.node("li",this.text(it,en)));
        card.append(list);views.append(card);
      }
      if(this.optional.length) {
        step.append(this.node("p",this.text("Attiva solo gli indicatori opzionali che vuoi usare. Nessuna entità disabilitata da te viene riattivata automaticamente.","Enable only the optional indicators you want. User-disabled entities are never re-enabled automatically.")));
        for(const e of this.optional)step.append(this.check(e.name,this.enabled.has(e.entity_id),v=>v?this.enabled.add(e.entity_id):this.enabled.delete(e.entity_id)));
      }
      step.append(this.check(this.text("Confermo: crea una nuova dashboard e abilita gli indicatori selezionati.","Confirm: create a new dashboard and enable the selected indicators."),this.confirmed,v=>{this.confirmed=v;this.render();}));
      step.append(this.button(this.busy?this.text("Operazione in corso…","Working…"):this.text("Crea dashboard","Create dashboard"),()=>this.create(),!this.confirmed));
    }
    const links=this.node("div",null,{className:"links"});
    links.append(this.node("a",this.text("Coordinate, sensori locali, notifiche e Assist","Coordinates, local instruments, notifications and Assist"),{href:"/config/integrations/integration/sscape"}));main.append(links);
  }
}
if (!customElements.get("sscape-setup")) customElements.define("sscape-setup", SScapeSetup);
