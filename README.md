# sScape per Home Assistant

Un dispositivo per ogni luogo o punto geografico scelto, configurato dall’interfaccia di Home
Assistant. Nessun YAML obbligatorio, nessuna porta da aprire, nessuna password
dell’account da consegnare all’integrazione.

## Aggiornamento 0.5.1

Le richieste per coordinate rispettano anche il `Retry-After` ricevuto dal server.
Un limite di quota non viene confuso con un semplice punto temporaneamente privo
di dati. La 0.5.1 include tutte le funzioni descritte sotto.

## Novità della 0.5

- **Coordinate**: latitudine, longitudine e raggio anche senza salvare un luogo
  in sScape, usando l’API geolocalizzata esistente. Fino a 10 punti, modificabili
  e sospendibili nelle opzioni HA. Le coordinate sono conservate nella
  configurazione HA e inviate a sScape per ottenere analisi e previsioni;
  nessun luogo viene creato nell’account.
- **Allerte ed eventi**: indicatori opzionali per allerta principale e scadenza,
  magnitudo/distanza/ora del terremoto più recente e distanza dell’anomalia
  termica più vicina. Gli attributi mantengono fonte ed evidenze. Un’allerta
  può riguardare il **raggio monitorato** senza coprire il punto: `covers_point`
  distingue i casi verificati. Cataloghi e dettaglio delle allerte sono parziali.
- **Inverno**: neve prevista e visibilità minima nelle prossime 24 ore. Se manca
  un’ora necessaria, il valore resta indisponibile. La neve prevista non è una
  misura del manto presente; la minima dell’aria non conferma gelo al suolo.
- **Sensori locali**: associa temperatura, umidità, pressione, vento, raffica,
  intensità di pioggia e contatore da strumenti esterni HA. Unità convertite e
  freshness verificata. I dati **non vengono caricati su sScape** e non cambiano
  il rischio. Senza timestamp osservativo esplicito si indica l’ora di ricezione
  HA, non un’ora di misura inventata. Il contatore non diventa intensità di pioggia.
- **Notifiche per episodio**: facoltative, tramite servizi `notify`, per esempio
  la Companion App. Luoghi accorpati solo per stesso ID e sorgente, non per
  vicinanza. Contesto e distanze restano specifici del luogo. Nessun replay
  all’avvio o falso cessato allarme. Non associare anche un blueprint allo stesso
  evento se vuoi evitare doppioni. Nessun dispositivo viene comandato.
- **Dashboard guidata**: apri **sScape setup** nella barra laterale come
  amministratore. Scegli un luogo o tutti quelli importati, prepara l’anteprima,
  seleziona gli indicatori opzionali e conferma. Viene creata solo una **nuova**
  dashboard, inizialmente riservata agli amministratori, senza copiare YAML.
  Le dashboard esistenti e le entità disabilitate da te non vengono modificate.
- **Assist**: abilita le frasi e, se vuoi, esponi rischio e meteo dei luoghi
  selezionati. Con l’agente Home Assistant integrato chiedi «sScape situazione
  a Casa», «sScape allerte a Casa», «sScape quando piove a Casa», «sScape neve
  a Casa» oppure «sScape ora più fredda a Casa». Anche in inglese. Alias
  facoltativi, nomi ambigui rifiutati, esposizione e permessi di lettura rispettati.
  Le richieste senza identità utente, tipiche di alcuni satelliti vocali condivisi,
  richiedono un consenso separato, disabilitato inizialmente.

### Attivazione

Aggiorna in HACS e **riavvia completamente Home Assistant**. In **Impostazioni →
Dispositivi e servizi → sScape → Configura** usa «Dopo il salvataggio configura»
per coordinate, sensori locali, notifiche o Assist. Ripetere la procedura non
cancella le altre opzioni. Il pannello guidato richiede il frontend HA;
su installazioni headless restano le azioni amministrative di sola lettura
`sscape.get_dashboard`, `sscape.get_setup` e `sscape.get_hazards`.

Per i **luoghi salvati** serve `places:read`. Per le **coordinate** serve
`integrations:read` e una chiave **non limitata ai luoghi**: la restrizione è
intenzionale e non viene aggirata. In modalità mista servono entrambi i permessi.
Resta necessario un piano con accesso API. In una nuova configurazione puoi
scegliere subito le coordinate, senza alcun luogo salvato. Le previsioni per
coordinate richiedono il backend con supporto additivo `include_forecast=true`.

Le dashboard sono create tramite WebSocket HA autenticato, mai modificando
`.storage`. Dopo la creazione puoi modificarle con gli strumenti nativi;
l’integrazione non le aggiorna automaticamente. I sensori locali continuano
durante un’interruzione cloud **dopo il caricamento**. Il primo avvio richiede
una connessione valida a sScape; i sensori originali HA restano indipendenti.

## Novità della 0.4

- **Precipitazioni nelle prossime ore**: un nuovo sensore mostra «Previste»,
  «Non previste» oppure «Previsione incompleta», senza trasformare dati mancanti
  in bel tempo. Usa le prossime sei ore del prodotto disponibile.
- **Quando cambia**: dalla pagina del dispositivo puoi abilitare la prossima
  fascia di precipitazioni, l’ora della raffica massima oraria e l’ora più fredda.
  Questi tre orari coprono 24 ore, come i sensori numerici di minima e raffica;
  l’indicatore sintetico delle precipitazioni guarda invece alle prossime sei.
  Sono previsioni orarie, non orari esatti di inizio o misure al suolo.
- **Tutti i tuoi luoghi**: in `sscape.get_dashboard` attiva **Includi tutti i
  luoghi importati**. Ottieni una panoramica e una vista per ciascun luogo scelto,
  mai per quelli esclusi. Arrivo del temporale e orari non pertinenti restano
  nascosti, mentre rischio e indisponibilità dei dati restano visibili.
- **Automazioni e annunci**: `sscape.get_outlook` restituisce fasce di
  precipitazioni, raffica massima e ora più fredda per 1–24 ore, senza ulteriori
  richieste API. Il nuovo blueprint per le precipitazioni esegue soltanto le
  azioni che scegli tu, senza attivazioni all’avvio o al ritorno da un guasto.

La previsione sScape normalmente parte dalla prossima ora completa. Il campo
`period_start` indica da quando è coperta: non descrive i minuti immediatamente
successivi. Le finestre rispettano le ore originali del modello; non stimiamo
arbitrariamente minuti mancanti. Se il prodotto include l’ora in corso, gli
accumuli possono comprenderne la parte già trascorsa. Le quantità sono sull’intera
ora, mai una stima della pioggia ancora da cadere. Una raffica massima su tre ore
non entra nel picco orario; la minima dell’aria non conferma gelo al suolo.

Per aggiornare una dashboard già importata, genera nuovamente lo YAML e usalo in
una **nuova** dashboard: non modifichiamo o sovrascriviamo quelle esistenti.

## Novità della 0.3

- Aggiornamenti push **in uscita** per i luoghi autorizzati; polling di recupero
  sempre attivo. Non devi aprire porte né configurare MQTT in Home Assistant.
- Fase del temporale, intervallo di arrivo e qualità dei dati. Un’analisi nuova
  non rende nuovi radar e osservazioni. Copertura sconosciuta non significa
  assenza di pericolo; zero significa zero record nella finestra indicata.
- Sensori opzionali per misure di stazione (con distanza e timestamp) e numeri
  di previsione per le prossime 24 ore. Le raffiche misurate sono convertite
  da m/s a km/h, senza modificare il valore originale conservato negli attributi.
- Eventi nativi di cambiamento e blueprint per eventi e soglie scelte da te.

Le funzioni avanzate vengono usate soltanto se dichiarate dal server. Un backend
con il solo contratto 0.2 continua a funzionare in polling, senza inventare dati.
Il push riduce l’attesa dopo un aggiornamento del backend, **non** la latenza
del provider meteorologico. Le risposte incrementali aggiornano la validità
dell’analisi senza cambiare timestamp o qualità delle osservazioni.

### Dashboard pronta con le tue entità

1. Apri **Strumenti per sviluppatori → Azioni** e scegli `sscape.get_dashboard`.
2. Seleziona il dispositivo del luogo e premi Esegui.
3. Copia il contenuto del campo `yaml` in una nuova dashboard manuale.

Si usano soltanto card native, inclusa la previsione oraria; nessuna custom card
aggiuntiva. Il contenuto viene generato con gli identificativi effettivi delle
entità abilitate. L’azione **non crea né sovrascrive dashboard**. Non incollare
il risultato sopra una dashboard esistente senza averne conservato il contenuto.

`sscape.get_briefing` restituisce invece rischio, spiegazione, azione consigliata,
validità e qualità dei dati: puoi usarli in un’automazione o annuncio scelto da te.
Entrambe sono azioni di sola lettura riservate agli amministratori Home Assistant;
non richiedono di inserire una chiave API nelle automazioni.

## Installazione con HACS

**Correzione 0.3.2:** se compare `FAILED_UNLOAD` o «Can't link device to
unknown config entry», aggiorna sScape in HACS e **riavvia completamente Home
Assistant**. Il semplice ricaricamento non recupera una configurazione già in
questo stato. Non eliminare l’integrazione, i dispositivi o i registri: la patch
conserva identificativi e cronologia. Se dopo il riavvio viene richiesta la
riautenticazione, inserisci una chiave valida dello stesso account, con permesso
`places:read` e accesso API attivo; la patch non aggira i controlli di accesso.

Repository pubblico dedicato:
**[leofabri/sscape-home-assistant](https://github.com/leofabri/sscape-home-assistant)**.
È disponibile come **repository personalizzato**, non è ancora incluso nel
catalogo predefinito HACS. Non devi accedere al repository principale sScape.

1. Apri **HACS → menu ⋮ → Repository personalizzati**.
2. Inserisci `https://github.com/leofabri/sscape-home-assistant` e scegli
   **Integrazione** come categoria.
3. Cerca e scarica **sScape**, quindi riavvia Home Assistant.
4. Apri **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → sScape**.
5. Inserisci il server `https://sscape.leofabri.com` e una chiave API del tuo
   account con il permesso `places:read`, creata nel profilo sScape.
6. Scegli esplicitamente i luoghi da importare. Puoi invece attivare
   **Segui tutti i luoghi**, comprese le aggiunte future autorizzate dalla chiave.

[Apri il repository in HACS](https://my.home-assistant.io/redirect/hacs_repository/?owner=leofabri&repository=sscape-home-assistant&category=integration)

Il collegamento rapido richiede HACS già installato; puoi sempre usare i
passaggi manuali sopra. Il marchio è distribuito come asset locale usando
l’icona originale sScape, senza modificarla.

Testata con il framework Home Assistant 2026.2.3 / Python 3.13 e
2026.10.4 / Python 3.14. Nessuna installazione reale dell’utente è stata
modificata o certificata. La versione 0.2 richiede il backend sScape con
contratto di selezione `home-assistant.v2`.

## Installazione manuale alternativa

1. Scarica lo ZIP dalla [release](https://github.com/leofabri/sscape-home-assistant/releases/latest)
   oppure usa i sorgenti. Copia **solo** `custom_components/sscape` in
   `/config/custom_components/sscape` del tuo Home Assistant.
2. Riavvia Home Assistant.
3. Apri **Impostazioni → Dispositivi e servizi → Aggiungi integrazione → sScape**.
4. Lascia il server `https://sscape.leofabri.com`, oppure inserisci l’origine
   HTTPS della tua installazione, senza `/api/v1`.
5. Nel profilo sScape crea una chiave API con il permesso `places:read` e
   inseriscila nel campo dedicato. Non serve la password sScape.

In modalità luoghi serve almeno un luogo seguito; in entrambe serve un piano che consenta l’accesso API
(attualmente Starter, Pro o Enterprise). Il componente non modifica questi
permessi né richiede abbonamenti aggiuntivi per HACS. Per installazioni locali
HTTP è ammesso soltanto su indirizzi LAN/loopback o nomi `.local`; HTTPS è
obbligatorio per i server remoti.

## Cosa trovi

Per ogni luogo scelto:

| Entità | Significato |
| --- | --- |
| Rischio | `low`, `moderate`, `high`, `severe`, tradotti nell’interfaccia |
| Situazione | Il titolo della valutazione sScape, massimo 255 caratteri |
| Arrivo stimato del temporale | Minuti, solo quando il server indica una fase di avvicinamento con ETA valida |
| Analisi aggiornata | Timestamp UTC della valutazione, **non** dell’osservazione meteorologica |
| Analisi non disponibile o scaduta | Attivo quando la risposta non contiene una valutazione fresca |
| Temporale in avvicinamento | Solo quando la valutazione indica una fase di avvicinamento |
| Attività elettrica rilevata | Presenza di rilevazioni recenti nel raggio monitorato, se il conteggio è disponibile |
| Meteo | Entità `weather` con stima attuale e previsioni orarie, non misure di una stazione |

Sono disponibili, ma disabilitati inizialmente, anche sensori per allerte
ufficiali locali, rilevazioni termiche, terremoti, segnalazioni di grandine,
numero e distanza dei fulmini. Abilitali dalla pagina del dispositivo se utili.
Una rilevazione termica non conferma da sola un incendio né un impatto sul luogo;
un terremoto recente non indica automaticamente danni. L’assenza di una voce
in un catalogo parziale non diventa un falso «nessun pericolo».

Il meteo conserva crediti e licenze delle fonti. Le precipitazioni orarie
riguardano l’ora precedente al timestamp; un massimo di raffica su tre ore
non viene presentato come massimo orario. Una previsione mancante rimane
indisponibile senza rendere indisponibili le valutazioni di rischio valide.

Negli attributi del sensore Rischio trovi sintesi, azione consigliata, fase,
segnale principale e confidenza, quando forniti dal server. Il componente
non calcola nuovi rischi, non trasforma contesti regionali in allerte locali,
non interpreta una stazione lontana come misura al tuo indirizzo e non somma
conteggi lightning di fornitori diversi.

Le valutazioni scadute, prive di timestamp/scadenza o mancanti diventano
`unavailable`, **mai** rischio basso o zero. Anche un’ETA assente è
`unavailable`, non zero. La disponibilità è ricontrollata a ogni polling:
può esserci al massimo un intervallo di polling prima che una scadenza si
rifletta nello stato. In caso di errore di rete tutte le entità interessate
diventano indisponibili. Non usare questi sensori come unico sistema di
protezione di persone o beni: non sostituiscono gli avvisi ufficiali.

## Luoghi, aggiornamenti e automazioni

Nelle **Opzioni** scegli i luoghi: **selezione vuota significa nessun luogo**.
«Segui tutti» è una scelta separata, mai implicita. Puoi disattivare il meteo.
Il polling delle valutazioni è di 120 secondi, configurabile fra 60 e 900:
una richiesta per account per i luoghi salvati, non per sensore. Per le coordinate
si usa una richiesta per punto e intervallo, con cache contro i burst e al massimo
due richieste meteo in parallelo. Le previsioni vengono aggiornate
ogni 10 minuti con una richiesta per luogo selezionato, massimo due in parallelo.
Retry-After e backoff limitano le richieste durante problemi.
La revoca/scadenza della chiave avvia il flusso di riautenticazione; una
chiave di un altro account viene rifiutata. La sostituzione corretta conserva
gli identificativi delle entità.

Un luogo escluso viene disabilitato senza cancellare entità o cronologia.
Se lo riselezioni vengono riabilitate soltanto le entità disabilitate da
questa esclusione, non quelle che avevi disabilitato tu o quelle opzionali.
Aggiornando dalla 0.1, la selezione viene fissata ai luoghi già importati:
nessun nuovo luogo viene aggiunto automaticamente. I nomi dei luoghi vengono
aggiornati senza sovrascrivere il nome personalizzato dato in Home Assistant.
Le opzioni ricaricano l’integrazione. Per rimuoverla usa **Dispositivi e servizi**.

Per le automazioni usa i valori interni, ad esempio `high` o `severe`, non le
traduzioni. Aggiungi condizioni che richiedano entità disponibili; non trattare
un passaggio a `unavailable` come cessazione del rischio. Questa versione legge
le API: non sostituisce i webhook già esistenti per eventi a bassa latenza e
non interviene sulle notifiche push del browser.

### Blueprint per i cambi di rischio

Da **Impostazioni → Automazioni e scene → Blueprint → Importa blueprint** usa:

`https://github.com/leofabri/sscape-home-assistant/blob/main/blueprints/automation/sscape/risk_change.yaml`

Scegli il sensore Rischio e le azioni da eseguire. Non viene inviata alcuna
notifica né attivato un dispositivo finché non configuri l’automazione.
Il blueprint reagisce agli aumenti di livello, non agli aggiornamenti degli
attributi; l’azione facoltativa di rientro richiede rischio basso stabile per
alcuni minuti. `unavailable → low` non è trattato come cessato allarme.
Su un server aggiornato, dati scaduti o copertura sconosciuta bloccano anche
l’azione facoltativa di rientro.

Nella stessa directory trovi `situation_change.yaml` e
`measurement_threshold.yaml`: importa il rispettivo URL GitHub come sopra.
Il primo reagisce agli eventi scelti senza duplicare ripristini o aggiornamenti
degli attributi. Il secondo usa una soglia e un tempo di conferma scelti da te,
per esempio su una raffica di stazione o sulla minima **prevista**. Non classifica
automaticamente gelo al suolo, danni o sicurezza di una tenda. Nessun dispositivo
viene comandato automaticamente dall’integrazione.

Per il nuovo sensore di previsione puoi importare anche:

`https://github.com/leofabri/sscape-home-assistant/blob/main/blueprints/automation/sscape/forecast_precipitation.yaml`

Il passaggio richiesto è «Non previste → Previste», confermato per il tempo
scelto. Dati incompleti, aggiornamenti dei soli attributi e ripristini da
indisponibilità non eseguono azioni. Non esiste un’azione automatica di «cessato
pericolo» e non viene comandato alcun dispositivo senza la tua configurazione.

## Credenziali e diagnostica

Nel profilo sScape, sezione sviluppatori, puoi creare una chiave con
**Limita questa chiave ai luoghi scelti**. Il server nega anche gli endpoint
globali e per coordinate: non è solo un filtro grafico. Le chiavi esistenti
non cambiano. Per limitarne una, creane una nuova e sostituiscila tramite la
riautenticazione dell’integrazione, poi revoca la precedente se non più usata.
La selezione nelle opzioni di Home Assistant limita ciò che viene richiesto,
ma non restringe da sola i permessi della chiave sul server.

La chiave rimane nella configurazione locale Home Assistant e può essere
revocata in sScape. È mascherata nei moduli, mai inserita nell’URL né nei log
dell’integrazione. Proteggi comunque i backup di Home Assistant: una chiave
memorizzata non diventa innocua perché il campo è mascherato.
La diagnostica esporta solo conteggi e stato polling, non chiavi, nomi dei
luoghi, coordinate o identificativi account. Le risposte sono limitate a 2 MiB
e i redirect HTTP non sono seguiti per evitare inoltri della chiave.

## Test e pacchetto

Con Python 3.14, dalla radice di questa directory:

```sh
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/pytest --timeout=20 --cov=custom_components.sscape --cov-branch
.venv/bin/ruff check .
node --test tests/setup-controller.test.mjs
.venv/bin/python tools/package.py --output /tmp/sscape-home-assistant.zip
```

La CI esegue i test su entrambe le versioni Python/Home Assistant e il
validatore ufficiale HACS. I test sono offline e usano il vero framework Home Assistant con dati
fittizi. Non certificano un’installazione fisica o l’accesso di un account
reale. Il pacchetto contiene soltanto il componente e la licenza, non chiavi,
configurazioni server, test o altri sorgenti del progetto. L’asset grafico è
l’icona sScape originale, inclusa senza trasformazioni.

## Riferimenti ufficiali

- [Requisiti HACS](https://www.hacs.dev/docs/publish/integration/)
- [Repository pubblici HACS](https://hacs.dev/docs/publish/start/)
- [Polling condiviso Home Assistant](https://contributors.home-assistant.io/core/integrations/fetching-data/)
- [Pannelli personalizzati Home Assistant](https://www.home-assistant.io/integrations/panel_custom/)

Licenza del codice: MIT, come il progetto principale. Questa licenza non
concede diritti aggiuntivi sui dati meteorologici o sulle API sScape.
