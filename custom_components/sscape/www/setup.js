import {action, createDashboard} from "./setup-controller.js";
import {renderPreview, updatePreviewHass} from "./setup-preview.js";
import {renderTests} from "./setup-tests.js";
const GROUPS={storm:["Temporali e fulmini","Storms and lightning"],official:["Avvisi ufficiali","Official warnings"],earthquake:["Terremoti","Earthquakes"],thermal:["Anomalie termiche","Thermal detections"],winter:["Neve e visibilità","Snow and visibility"],observations:["Stazioni nelle vicinanze","Nearby stations"],local:["La tua stazione","Your local instruments"]};

// HA's theme, typography and icon family, with no CDN or third-party UI library.
class SScapeSetup extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode: "open"});
    this.targets = []; this.selected = ""; this.all = false; this.busy = false;
    this.profile="essential";this.groups=new Set(Object.keys(GROUPS));this.viewIndex=0;this.activateOptional=true;this.destinations=[];
    this.config = null; this.optional = []; this.enabled = new Set();
  }
  set hass(value) {
    this._hass = value;
    updatePreviewHass(this.shadowRoot.querySelector(".preview"),value);
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
      this.profile=this.targets[0]?.dashboard_profile || "essential";this.destinations=data.notification_targets || [];
    } catch { this.error = this.text("Non riesco a leggere i luoghi. Controlla l’integrazione.", "Unable to load targets. Check the integration."); }
    this.render();
  }
  check(label, checked, change) {
    const row = this.node("label", null, {className: "check"});
    const input = this.node("input", null, {type: "checkbox", checked, disabled: this.busy});
    input.dataset.focusKey="check:"+label;
    input.addEventListener("change", () => change(input.checked));
    row.append(input, this.node("span", label)); return row;
  }
  button(label, fn, disabled = false) {
    const button = this.node("button", label, {type: "button", disabled: this.busy || disabled});
    button.dataset.focusKey="button:"+label;
    button.addEventListener("click", fn); return button;
  }
  invalidate() { this.config = null; this.confirmed = false; this.enabled.clear(); this.render(); }
  async preview() {
    if (this.busy) return;
    this.busy = true; this.error = ""; this.render();
    try {
      const data = await action(this._hass, "get_dashboard", {
        device_id: this.selected, all_selected_places: this.all, include_optional: true, profile:this.profile, detail_groups:[...this.groups]});
      this.config = data.config;this.viewIndex=0;this.viewTargets=data.view_targets || {};
      this.previewDevices=data.target_device_ids || (this.all?this.targets.map(t=>t.device_id):[this.selected]);
      const active = this.targets.filter(t=>this.previewDevices.includes(t.device_id));
      this.optional = active.flatMap(t => Object.entries(t.entities)
        .filter(([,e]) => e.disabled && e.disabled_by === "integration")
        .map(([key,e]) => ({...e, name: t.name + " · " + (e.name || key)})));
      // Only optional entities present in this exact preview may be enabled.
      const ids = new Set(JSON.stringify(this.config).match(/(?:sensor|binary_sensor|weather|event)\.[a-z0-9_]+/g) || []);
      this.optional = this.optional.filter(e => ids.has(e.entity_id));
      this.enabled = new Set(this.activateOptional?this.optional.map(e => e.entity_id):[]);
      this.confirmed = false;
    } catch { this.error = this.text("Anteprima non disponibile. Nessuna dashboard è stata modificata.", "Preview unavailable. No dashboard was changed."); }
    this.busy = false; this.render();
    if(this.config)requestAnimationFrame(()=>{
      const active=this.shadowRoot.activeElement;
      if(active && active.dataset.focusKey!=="button:"+this.text("Prepara anteprima","Prepare preview"))return;
      const preview=this.shadowRoot.querySelector('.preview');
      if(preview){preview.tabIndex=-1;preview.focus({preventScroll:true});preview.closest('section').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});}
    });
  }
  async create() {
    if (this.busy || !this.confirmed || !this.config) return;
    this.busy = true; this.error = ""; this.render();
    try {
      const current=await action(this._hass,"get_setup");
      const chosen=this.targets.filter(t=>this.previewDevices.includes(t.device_id));
      const active=(current.targets || []).filter(t=>chosen.some(old=>old.device_id===t.device_id));
      const eligible=new Set(active.flatMap(t=>Object.values(t.entities).filter(e=>!e.disabled || e.disabled_by==="integration").map(e=>e.entity_id)));
      if(active.length!==chosen.length || [...this.enabled].some(id=>!eligible.has(id)))throw new Error("preview_changed");
      const result = await createDashboard(this._hass, this.config, this.title || "sScape", true, [...this.enabled]);
      this.created = result.url;
      if (result.failed.length) this.error = this.text("Dashboard creata. Alcune entità opzionali non sono state abilitate: puoi attivarle nelle impostazioni.", "Dashboard created. Some optional entities could not be enabled; use entity settings.");
    } catch { this.error = this.text("Creazione non riuscita. Le dashboard esistenti non sono state toccate.", "Creation failed. Existing dashboards were not changed."); }
    this.busy = false; this.render();
  }
  render() {
    const root = this.shadowRoot; const focused=root.activeElement?.dataset.focusKey;root.replaceChildren();
    root.append(this.node("link",null,{rel:"stylesheet",href:"/sscape-static/setup.css?v=0.6.0"}));
    const header = this.node("header"); const menu = this.node("ha-menu-button"); menu.hass = this._hass;
    header.append(menu, this.node("h1", "sScape")); root.append(header);
    const main = this.node("main"); root.append(main);
    if (!this._hass.user?.is_admin) { main.append(this.node("p", this.text("Accesso riservato agli amministratori.", "Administrator access required."))); return; }
    main.append(this.node("h2", this.text("I tuoi luoghi, a colpo d’occhio", "Your places at a glance")),
      this.node("p", this.text("Situazione attuale, prossime ore e dati disponibili. Prima l’anteprima, poi decidi: nessuna dashboard esistente verrà modificata.", "Current situation, next hours and available data. Preview first, then decide: existing dashboards will not be changed.")));
    if (this.error) main.append(this.node("div", this.error, {className:"error", role:"alert"}));
    if (this.created) { main.append(this.node("h3", this.text("Dashboard pronta", "Dashboard ready")), this.node("a", this.text("Apri dashboard", "Open dashboard"), {href:this.created,className:"open"})); return; }
    if (!this.targets.length) { main.append(this.node("p", this.text("Non ci sono luoghi attivi. Configura prima un luogo salvato o un punto per coordinate.", "No active targets. Configure a saved place or coordinate target first."))); }
    else {
      const step = this.node("section", null, {className:"step"}); main.append(step);
      step.append(this.node("h3", this.text("1 · Scegli cosa vedere", "1 · Choose what to see")));
      const label = this.node("label", this.text("Luogo", "Place"), {className:"field"});
      const select = this.node("select", null, {disabled:this.busy || this.all,ariaLabel:this.text("Luogo","Place")});
      select.dataset.focusKey="place";
      for (const t of this.targets) select.append(this.node("option", t.name, {value:t.device_id, selected:t.device_id===this.selected}));
      select.addEventListener("change", () => {this.selected=select.value;this.invalidate();});label.append(select); step.append(label);
      step.append(this.check(this.text("Tutti i luoghi dello stesso account", "All targets in the same account"), this.all, v=>{this.all=v;this.invalidate();}));
      const profileLabel=this.node("label",this.text("Cosa vuoi vedere?","What would you like to see?"),{className:"field"});
      const profile=this.node("select",null,{disabled:this.busy});
      profile.dataset.focusKey="profile";
      for(const [key,it,en] of [["essential","Essenziale · situazione e prossime ore","Essential · situation and next hours"],["detailed","Dettagliata · scegli gli approfondimenti","Detailed · choose the evidence"]])profile.append(this.node("option",this.text(it,en),{value:key,selected:this.profile===key}));
      profile.addEventListener("change",()=>{this.profile=profile.value;this.invalidate();});profileLabel.append(profile);step.append(profileLabel);
      if(this.profile==="detailed"){
        const groups=this.node("div",null,{className:"groups"});
        for(const [key,names] of Object.entries(GROUPS))groups.append(this.check(this.text(...names),this.groups.has(key),v=>{v?this.groups.add(key):this.groups.delete(key);this.invalidate();}));
        step.append(groups);
      }
      const title = this.node("label", this.text("Titolo della nuova dashboard", "New dashboard title"), {className:"field"});
      const input = this.node("input", null, {type:"text",maxLength:80,value:this.title || "sScape",disabled:this.busy});
      input.dataset.focusKey="title";
      input.addEventListener("input", () => {this.title=input.value;});title.append(input);step.append(title);
      step.append(this.button(this.text("Prepara anteprima", "Prepare preview"), ()=>this.preview(), !this.selected));
    }
    if (this.config) {
      const step = this.node("section", null, {className:"step"}); main.append(step);
      step.append(this.node("h3", this.text("2 · Guarda il risultato", "2 · See the result")));
      if(this.config.views.length>1){
        const row=this.node("label",this.text("Vista","View"),{className:"field"});
        const select=this.node("select");
        select.dataset.focusKey="view";
        this.config.views.forEach((view,i)=>select.append(this.node("option",view.title,{value:String(i),selected:i===this.viewIndex})));
        select.addEventListener("change",()=>{this.viewIndex=Number(select.value);this.render();});row.append(select);step.append(row);
      }
      const preview=this.node("div",null,{className:"preview"});step.append(preview);
      const view=this.config.views[this.viewIndex];
      const mapped=this.viewTargets[view.path];
      const matches=this.targets.filter(t=>mapped?t.device_id===mapped:this.all?t.name===view.title:t.device_id===this.selected);
      const target=matches.length===1?matches[0]:undefined;
      renderPreview(preview,this._hass,view,target);
      if(this.optional.length) {
        step.append(this.node("p",this.text("Attiva solo gli indicatori opzionali che vuoi usare. Nessuna entità disabilitata da te viene riattivata automaticamente.","Enable only the optional indicators you want. User-disabled entities are never re-enabled automatically.")));
        step.append(this.check(this.text("Attiva gli indicatori necessari ai dettagli scelti","Enable the indicators needed for selected details"),this.activateOptional,v=>{this.activateOptional=v;this.enabled=new Set(v?this.optional.map(e=>e.entity_id):[]);this.confirmed=false;this.render();}));
        const details=this.node("details");details.append(this.node("summary",this.text("Quali indicatori saranno attivati?","Which indicators will be enabled?")));
        for(const e of this.optional)details.append(this.node("p",e.name));step.append(details);
      }
      step.append(this.check(this.text("Confermo: crea una nuova dashboard e abilita gli indicatori selezionati.","Confirm: create a new dashboard and enable the selected indicators."),this.confirmed,v=>{this.confirmed=v;this.render();}));
      step.append(this.button(this.busy?this.text("Operazione in corso…","Working…"):this.text("Crea dashboard","Create dashboard"),()=>this.create(),!this.confirmed));
    }
    renderTests(this,main);
    const links=this.node("div",null,{className:"links"});
    links.append(this.node("a",this.text("Coordinate, sensori locali, notifiche e Assist","Coordinates, local instruments, notifications and Assist"),{href:"/config/integrations/integration/sscape"}));main.append(links);
    if(focused)[...root.querySelectorAll('[data-focus-key]')].find(e=>e.dataset.focusKey===focused)?.focus({preventScroll:true});
  }
}
if (!customElements.get("sscape-setup")) customElements.define("sscape-setup", SScapeSetup);
