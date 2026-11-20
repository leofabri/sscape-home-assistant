const base = 'sscape/mobile_location/';

class SScapeMobileLocation extends HTMLElement {
  #hass;
  #data;
  #timer;
  #owner;
  #busy = false;
  #failed = false;
  #ui = this.attachShadow({ mode: 'open' });

  set hass(value) {
    this.#hass = value;
    const owner = value?.user?.id;
    if (this.#owner !== owner) {
      this.#owner = owner;
      this.#data = undefined;
      this.#draw();
      if (owner && this.isConnected) void this.#refresh();
    }
  }

  connectedCallback() {
    this.#draw();
    void this.#refresh();
    this.#timer = setInterval(() => { if (this.#data?.linked && !this.#busy) void this.#refresh(); }, 15000);
  }

  disconnectedCallback() {
    clearInterval(this.#timer);
    this.#data = undefined;
  }

  async #refresh() {
    const owner = this.#owner;
    if (!this.#hass || !owner) return;
    try {
      const status = await this.#hass.callWS({ type: base + 'status' });
      if (this.#owner !== owner || !this.isConnected) return;
      this.#data = status;
      this.#failed = false;
    } catch {
      if (this.#owner !== owner || !this.isConnected) return;
      this.#data = undefined;
      this.#failed = true;
    }
    this.#draw();
  }

  #node(parent, tag, text) {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    parent.append(node);
    return node;
  }

  #draw() {
    const it = (this.#hass?.language ?? 'en').startsWith('it');
    const say = (a, b) => it ? a : b;
    this.#ui.replaceChildren();
    this.#node(this.#ui, 'style', `
      :host{display:block;color:var(--primary-text-color,#202327)}
      main{width:min(100% - 2rem,40rem);margin-inline:auto;padding-block:1.25rem}
      h1{font-size:1.65rem;margin:0 0 .75rem} p{line-height:1.5;text-wrap:pretty}
      label{display:grid;gap:.25rem;margin-block:1rem} input,select,button{box-sizing:border-box;min-height:44px;font:inherit;padding:.4rem .6rem}
      input,select{width:100%;background:var(--card-background-color);color:inherit;border:1px solid var(--divider-color);border-radius:6px}
      button{background:var(--primary-color,#036b9e);color:var(--text-primary-color,#fff);border:none;border-radius:6px}
      button:disabled{opacity:.6} a{color:var(--primary-color,#036b9e)}
      section{border-block-start:1px solid var(--divider-color);margin-block-start:1rem;padding-block-start:.5rem}
    `);
    const main = this.#node(this.#ui, 'main');
    this.#node(main, 'h1', say('sScape sul mio telefono', 'sScape on my phone'));
    if (!this.#data) {
      this.#node(main, 'p', this.#failed ? say('Connessione non disponibile.', 'Connection unavailable.') : say('Caricamento…', 'Loading…')).setAttribute('role', 'status');
      const retry = this.#node(main, 'button', say('Aggiorna', 'Refresh'));
      retry.onclick = () => void this.#refresh();
      return;
    }
    if (this.#data.linked) {
      const name = this.#data.choices.find(device => device.id === this.#data.device)?.name;
      this.#node(main, 'p', name ? say(`Collegato: ${name}`, `Linked: ${name}`) : say('Telefono non disponibile', 'Phone unavailable'));
      if (this.#data.use_in_home_assistant) this.#node(main, 'p', say('Meteo e avvisi personali qui', 'Personal weather and alerts here'));
      if (this.#data.use_in_sscape) this.#node(main, 'p', say('Posizione e avvisi anche in sScape', 'Location and alerts also in sScape'));
      const b = this.#data.briefing;
      if (this.#data.use_in_home_assistant) {
        const view = this.#node(main, 'section');
        if (b) {
          this.#node(view, 'h2', b.headline);
          if (b.temperature_c != null) this.#node(view, 'p', say(`Temperatura stimata: ${b.temperature_c} °C`, `Estimated temperature: ${b.temperature_c} °C`));
          this.#node(view, 'p', b.recommended_action);
          this.#node(view, 'small', say('Posizione ricevuta alle ', 'Location received at ') + new Date(b.observed_at).toLocaleTimeString(it ? 'it' : 'en', { hour: '2-digit', minute: '2-digit' }));
        } else {
          this.#node(view, 'p', say('Posizione non aggiornata. Aspettiamo un nuovo GPS dal telefono.', 'Location unavailable. Waiting for a new GPS report.'));
        }
      }
      if (this.#data.error) this.#node(main, 'p', say('Il collegamento non è operativo. Controlla il dispositivo e l’autorizzazione.', 'Check your phone and the link authorization.'));
      const unlink = this.#node(main, 'button', say('Scollega il mio telefono', 'Disconnect my phone'));
      unlink.onclick = () => void this.#change('unlink', {}, unlink);
      return;
    }
    this.#node(main, 'p', say('Apri le impostazioni di sScape con il tuo account e genera un codice temporaneo.', 'Open sScape settings with your own account and create a temporary code.'));
    const link = this.#node(main, 'a', say('Apri le impostazioni sScape', 'Open sScape settings'));
    link.href = 'https://sscape.leofabri.com/profile?section=developer#home-assistant-location';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    if (!this.#data.choices.length) {
      this.#node(main, 'p', say('Nessun telefono registrato con questo utente Home Assistant.', 'No phone is registered to this Home Assistant user.'));
      return;
    }
    const form = this.#node(main, 'form');
    const label = this.#node(form, 'label', say('Scegli il tuo telefono', 'Choose your phone'));
    const select = this.#node(label, 'select');
    this.#node(select, 'option', say('Seleziona…', 'Select…')).value = '';
    select.required = true;
    for (const device of this.#data.choices) this.#node(select, 'option', device.name).value = device.id;
    const code = this.#node(this.#node(form, 'label', say('Codice da sScape', 'Code from sScape')), 'input');
    code.type = 'password';
    code.autocomplete = 'off';
    code.required = true;
    code.maxLength = 43;
    const submit = this.#node(form, 'button', say('Collega', 'Link phone'));
    form.onsubmit = event => {
      event.preventDefault();
      const secret = code.value.trim();
      code.value = '';
      void this.#change('link', { device: select.value, code: secret }, submit);
    };
  }

  async #change(action, fields, button) {
    if (this.#busy) return;
    const owner = this.#owner;
    this.#busy = button.disabled = true;
    try {
      const status = await this.#hass.callWS({ type: base + action, ...fields });
      if (owner !== this.#owner || !this.isConnected) return;
      this.#data = status;
      this.#draw();
    } catch {
      if (owner !== this.#owner || !this.isConnected) return;
      const it = (this.#hass?.language ?? 'en').startsWith('it');
      this.#node(button.parentNode, 'p', it ? 'Operazione non riuscita. Controlla il codice o riprova.' : 'Could not finish. Check the code or try again.').setAttribute('role', 'alert');
    } finally {
      this.#busy = button.disabled = false;
    }
  }
}

customElements.define('sscape-mobile-location', SScapeMobileLocation);
