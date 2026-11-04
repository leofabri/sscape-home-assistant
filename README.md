# sScape per Home Assistant

Un dispositivo per ogni luogo seguito, configurato dall’interfaccia di Home
Assistant. Nessun YAML obbligatorio, nessuna porta da aprire, nessuna password
dell’account da consegnare all’integrazione.

## Installazione con HACS

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

Servono almeno un luogo seguito e un piano account che consenta l’accesso API
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
una richiesta per account, non per sensore. Le previsioni vengono aggiornate
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

Licenza del codice: MIT, come il progetto principale. Questa licenza non
concede diritti aggiuntivi sui dati meteorologici o sulle API sScape.
