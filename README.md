# Dermocosmetic Price Tracker Italia

App gratuita e automatica che risponde a una domanda:

> **Dove e come compro tutti e 5 i prodotti spendendo davvero il meno possibile, incluse spedizioni e costi di spostamento?**

Ogni mattina alle 08:00 (ora di Roma) GitHub Actions legge i prezzi dalle farmacie online monitorate, calcola il carrello migliore e
pubblica una dashboard statica su GitHub Pages. Non c'è nessun server sempre acceso.

La dashboard mostra sempre la dicitura **"Miglior prezzo trovato tra le farmacie monitorate"**. Non è il prezzo più basso del mercato.

| ID | Prodotto | Formato | EAN | Minsan |
|---|---|---|---|---|
| P001 | Uriage Xémose Olio Detergente | 1000 ml | 3661434003370 | 927291005 |
| P002 | La Roche-Posay Lipikar Baume AP+M | 400 ml | 3337875696548 | 979265434 |
| P003 | Ceramol 311 Lipocrema | **100 ml** (fino al 18/11/2026: 50 ml) | 8029408107014 | 980512762 |
| P004 | Ceramol Crema Mani 311 | 100 ml | 8029408093058 | 977658020 |
| P005 | La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+ (senza profumo) | 50 ml | 3337875797597 | 982981666 |

Gli identificativi sono stati verificati il 18/11/2026 sul JSON-LD di almeno due farmacie indipendenti (fonti in `config.json`).

**Attenzione P003 / P004.** Ceramol 311 Lipocrema 100 ml e Ceramol 311 Crema Mani 100 ml hanno stessa marca, stessa linea e stesso formato.
P003 richiede "lipocrema" ed esclude "mani"; P004 fa il contrario. Una stessa pagina non viene mai assegnata a due prodotti.

---

## Principi

- **Nessun prezzo inventato.** Un valore mancante è `null`, mai `0`. I dati simulati esistono solo in `tests/fixtures/`.
- **Spedizione sconosciuta ≠ gratis.** La soglia di spedizione gratuita si applica solo se è stata verificata sul sito della farmacia.
- **Match rigoroso.** Un'offerta entra in classifica solo se EAN o Minsan coincidono, oppure se nome, marca e formato coincidono con regole severe.
  Multipack, varianti (colorata, AP+MAX, edizione limitata…) e formati diversi vengono esclusi.
- **Prezzo verificato da più fonti.** Il prezzo viene letto da JSON-LD, microdata, meta tag e HTML e le fonti vengono confrontate.
  Su verafarma.it, per esempio, il JSON-LD indicava un prezzo diverso da quello mostrato. Senza una maggioranza l'offerta è "PREZZO NON VERIFICATO".
- **Scraping rispettoso.** Lo scraper:
  - rispetta robots.txt;
  - fa una richiesta alla volta per dominio, con almeno 2,5 s di pausa (65 s sui negozi Shopify, come chiesto dal loro `Retry-After`);
  - usa un User-Agent identificabile;
  - non aggira mai CAPTCHA, Cloudflare o Akamai: un sito che blocca diventa `BLOCKED` e viene saltato.

---

## Installazione locale

Requisiti: Python ≥ 3.11 e git.

```bash
git clone https://github.com/<tuo-utente>/<tuo-repo>.git
cd <tuo-repo>
python -m venv .venv
# Windows: .venv\Scripts\activate    Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
```

Le farmacie attive servono HTML lato server. Per eseguire anche il test della dashboard nel browser e riprodurre le schermate dell’audit, installa Playwright con Chromium.

```bash
pip install playwright
playwright install --with-deps chromium
```

Il workflow di scraping installa Playwright quando uno scraper richiede JavaScript.

## Dashboard e anteprime di sviluppo

Dalla directory del progetto esegui `python -m http.server 8000` e apri [la dashboard locale](http://localhost:8000/). Il browser legge i tre file relativi `data.json`, `price_history.json` e `pharmacies.json`. La stessa struttura funziona sotto il percorso del repository su GitHub Pages.

Il primo riquadro mostra il costo del carrello e quanti prodotti copre. A seguire puoi confrontare domicilio, ritiro, acquisto in negozio e modalità mista, consultare le offerte per prodotto e selezionare un prodotto nello storico. La differenza in euro per ciascuna modalità, il risparmio rispetto a un singolo punto vendita e le stime di viaggio provengono dall’ottimizzatore. Il dettaglio del mercato distingue le offerte dei siti in valutazione. Lo stato del sistema raccoglie esiti e problemi dell’ultima scansione.

Per l’anteprima aggiungi `?data=tests/fixtures/ui/<caso>/` all’URL. La barra **DATI DI TEST, NON REALI** contrassegna ogni anteprima. Rimuovi il parametro per tornare ai dati della scansione.

| Caso | Cosa controllare |
| --- | --- |
| [completo](http://localhost:8000/?data=tests/fixtures/ui/completo/) | Cinque prodotti, quattro modalità confrontabili, offerte candidate separate. Nove giorni di storico per P001, zero per P002 e un giorno del formato corrente per P003. |
| [mancanti](http://localhost:8000/?data=tests/fixtures/ui/mancanti/) | Due prodotti acquistabili, spedizione sconosciuta, un solo giorno di storico e tentativo di scansione fallito. |
| [nessuna-offerta](http://localhost:8000/?data=tests/fixtures/ui/nessuna-offerta/) | Catalogo presente, zero offerte e storico vuoto. |
| [storico-due](http://localhost:8000/?data=tests/fixtures/ui/storico-due/) | Due giorni di prezzi per prodotto, con grafico e tabella. |
| [errore](http://localhost:8000/?data=tests/fixtures/ui/errore/) | `price_history.json` volutamente troncato per provare lo stato di errore dello storico mentre carrello e registro sono disponibili. |

L’[audit UX](docs/ux-audit.md) include le schermate iniziali a 375, 768 e 1280 px. Le [schermate del redesign](docs/screenshots/dopo/) seguono lo schema `<caso>-<larghezza>.png`, ad esempio `reale-375.png` e `completo-1280.png`. Per rigenerarle con il server locale sulla porta 8000, esegui `python tests/ui_review.py --url http://127.0.0.1:8000/`. Per modificare l’interfaccia consulta il [design system](docs/design-system.md).

## Configurazione

Tutto si configura in `config.json`:

| Sezione | Cosa contiene |
|---|---|
| `http` | User-Agent, ritmo delle richieste, timeout, retry. **Aggiorna `user_agent` con l'URL del tuo repository.** |
| `scraping` | Candidati per prodotto, frequenza di rilettura delle sitemap, tolleranza sui prezzi. |
| `location` | Città o CAP (mai un indirizzo) per le farmacie fisiche. |
| `travel` | Mezzo (`mezzi_pubblici`, `auto`, `piedi`, `bici`), distanza massima, €/km auto, parcheggio, biglietti A/R. |
| `products` | Catalogo, identificativi verificati, parole chiave, varianti accettate ed escluse. |

I costi di viaggio sono **parametri tuoi, non prezzi rilevati**. Il default (mezzi pubblici, 4,40 € andata e ritorno) è un'ipotesi da adattare.

## Esecuzione manuale

```bash
python scraper.py                         # tutte le farmacie ACTIVE -> data.json + price_history.json
python scraper.py --pharmacy clubfarma --dry-run   # prova una farmacia senza scrivere i dati
python scraper.py --refresh-candidates    # rilegge sitemap/ricerche per trovare gli URL prodotto
python scraper.py --product P003 --dry-run   # ricerca mirata di un prodotto (solo in prova)
python optimizer.py                       # ricalcola i carrelli dal data.json esistente
python -m http.server 8000                # dashboard su http://localhost:8000
```

Codici di uscita di `scraper.py`:
- `0`: almeno un'offerta valida;
- `2`: nessuna offerta valida. `data.json` e lo storico **non** vengono sovrascritti e il fallimento viene registrato in `last_attempt`;
- `3`: errore di scrittura.

I log giornalieri sono in `logs/scrape_AAAA-MM-GG.log`. Su Actions si scaricano come artifact.

## Spedizioni

`shipping_config.json` contiene, per ogni farmacia:
- costo standard, soglia di gratuità (inclusiva "da" o esclusiva "oltre"), contrassegno, ritiro;
- fonte (URL), citazione testuale, data di verifica e attendibilità (`alta` / `media` / `bassa` / `sconosciuta`).

```bash
python shipping.py --check            # rilegge le pagine ufficiali e segnala le differenze
python shipping.py --check --apply    # aggiorna i valori trovati in modo univoco
python shipping.py --quote meafarma 45.50
```

Se una farmacia ha costo `null`, ogni carrello che la include risulta **incompleto** e non può essere un vincitore certo.

## Località e ritiro fisico

```bash
python physical_pharmacies.py                          # aggiorna (cache settimanale)
python physical_pharmacies.py --city Torino --max-km 5 --force
python physical_pharmacies.py --postcode 20121 --mode auto
```

- La località viene geocodificata con Nominatim e le farmacie arrivano da Overpass (OpenStreetMap, ODbL).
  Le distanze partono dal centro della città o del CAP, in linea d'aria × `road_factor`.
- **I prezzi in negozio non sono pubblicati online.** La disponibilità in farmacia fisica è quindi "non verificata".
  L’ottimizzatore calcola la **soglia di convenienza** che compare nella dashboard. È il totale sotto cui comprare in negozio costa meno dell’ordine online, incluso il viaggio.
- Se rilevi prezzi di persona, inseriscili in `store_prices.json` con data e fonte. L'ottimizzatore li userà per le modalità C e D.
- Il ritiro di ordini online (es. alpifarma, gratuito ad Alagna Valsesia) viene proposto solo entro la distanza massima.
- Nelle impostazioni **Località di ritiro** puoi salvare città, CAP, mezzo e distanza in questo browser. La simulazione locale usa le stime di viaggio precalcolate per le farmacie della scansione.
  Per cambiare località per tutti, avvia il workflow con l'input `location` oppure usa il comando qui sopra.

## Come viene calcolato il carrello

`optimizer.py` valuta **in modo esaustivo** ogni assegnazione "prodotto → dove comprarlo".

**Costo** = prodotti + una spedizione per ordine (con le soglie verificate) + costi di ritiro + un viaggio per luogo visitato.
Un viaggio verso un luogo conta una volta sola, anche se lì ritiri più prodotti.

| Modalità | Significato |
|---|---|
| A | tutto online a domicilio da una farmacia |
| E | tutto online a domicilio da più farmacie |
| B | tutto online con ritiro in farmacia |
| C | tutto in farmacia fisica (serve `store_prices.json`) |
| D | misto |

Risultati:
- miglior carrello assoluto, con 1 farmacia e con al massimo 2;
- classifiche a domicilio, ritiro fisico e misto;
- risparmio rispetto all'acquisto in un solo punto;
- affidabilità di ogni carrello;
- alternative incomplete, elencate a parte.

Se un prodotto non è acquistabile in nessuna farmacia, i carrelli sono dichiarati **parziali**.

## Cambiare il formato di un prodotto

Esempio reale: P003 è passato da 50 ml a 100 ml il 18/11/2026.

1. In `config.json` aggiorna `format`, `ean` e `minsan`. EAN e Minsan vanno impostati solo se verificati su JSON-LD di almeno due fonti, altrimenti `null`.
   Aggiorna anche `search_queries`, `url_filter` e `match`. Aggiungi in `format_history` il formato precedente con la data `until`.
2. `python maintenance.py format-change P003`. Il comando:
   - aggiunge il campo `format` alle voci di storico che non lo hanno, senza cancellarne nessuna (le vecchie P003 diventano "50 ml");
   - scarta da `data.json` le offerte del vecchio formato e ricalcola i carrelli;
   - azzera gli URL in cache del prodotto.
   È idempotente.
3. `python scraper.py --product P003 --dry-run` per controllare subito cosa si trova.
   Le offerte vere entrano in `data.json` al run successivo.

Dashboard e ottimizzatore usano solo il formato corrente. Le rilevazioni del vecchio formato restano in `price_history.json`,
escluse dal grafico e dalle statistiche, e la dashboard segnala il cambio.

## Aggiungere una farmacia

1. Verifica raggiungibilità, robots.txt, sitemap, JSON-LD e ToS (vedi `docs/analisi_siti.md`).
2. Crea una classe in `scrapers/additional.py` che eredita dalla piattaforma giusta in `scrapers/platforms.py`
   (`PrestaShopScraper`, `ShopwareScraper`, `OpenCartScraper`, `MagentoScraper`, `ShopifyScraper`). Di solito bastano
   `pharmacy_id`, `name`, `base_url`, `sitemap_urls` ed eventualmente `price_css` e `availability_css`.
3. Registrala in `scrapers/__init__.py`.
4. Aggiungila a `pharmacies.json` (stato `VERIFIED`) e le sue spedizioni a `shipping_config.json`.
5. Provala: `python scraper.py --pharmacy <id> --dry-run`.
6. Attivala: `python scraper.py --pharmacy <id> --activate`. Diventa `ACTIVE` **solo** se estrae almeno un'offerta autentica
   con match per EAN/Minsan; la prova resta salvata in `activation_evidence`.

## Scraper generico JSON-LD per i siti scoperti

Per i siti senza scraper dedicato, `scrapers/generic_jsonld.py` cerca i 5 prodotti leggendo **solo dati strutturati**
(JSON-LD, poi microdata, poi meta tag; mai il testo visibile).

```bash
python scraper.py --probe farmacialoreto.it        # valuta un dominio -> docs/probe/farmacialoreto.it.md
python scraper.py --probe-all --probe-limit 5      # i siti DISCOVERED/UNDER_REVIEW senza scraper dedicato
python pharmacy_discovery.py promote farmacialoreto.it   # promozione MANUALE ad ACTIVE (chiede conferma)
python pharmacy_discovery.py demote farmacialoreto.it    # torna a UNDER_REVIEW
```

- **Pagine prodotto**: si parte dalla sitemap (da robots.txt o `/sitemap.xml`, prima le sitemap "prodotti").
  Se non basta, si usa la ricerca interna: pattern già salvato, poi OpenSearch, poi pattern comuni.
  Il pattern che funziona viene salvato in `search_url_template`. Dei risultati si tengono al massimo 5 link che contengono marca e nome.
- **Parsing**:
  - JSON malformato tollerato: BOM, commenti, virgole finali, entità HTML;
  - gestiti `@graph`, `ItemList`, `ProductGroup`/`hasVariant` e offerte multiple, con la scelta della variante del formato giusto;
  - `AggregateOffer` usata solo se il prezzo è univoco;
  - da `priceSpecification` si prende il prezzo pubblico, esclusi i prezzi riservati agli iscritti o di listino;
  - solo prezzi in EUR;
  - `shippingDetails` registrata come semplice indizio.
- **Sicurezza**:
  - robots.txt verificato per ogni percorso;
  - tetto di richieste per dominio e per esecuzione (`config.json → generic_jsonld.max_requests_per_domain`, default 25);
  - 403, 429 o challenge rendono il sito `BLOCKED`.
- **Compatibilità** scritta in `pharmacies.json → jsonld_compatibility`:

  | Valore | Significato |
  |---|---|
  | `FULL` | almeno un'offerta con prezzo, disponibilità ed EAN/Minsan coincidente |
  | `PARTIAL` | dati strutturati presenti ma incompleti |
  | `NONE` | nessun dato strutturato utile |
  | `BLOCKED` | il sito blocca le richieste |

- **Offerte candidate**: le offerte dei siti non `ACTIVE` vanno **solo** in `data.json → candidate_offers`, marcate `NON_VERIFICATO`.
  Non entrano mai in carrello, migliori prezzi o storico. La dashboard le mostra a parte, nella sezione "Mercato".
- **Promozione**: è ammessa solo se:
  - la compatibilità è `FULL` o `PARTIAL`;
  - il probe ha meno di 7 giorni;
  - robots.txt consente ancora i percorsi usati;
  - digiti il dominio per confermare.

  Se la spedizione del sito non è verificata, viene creata in `shipping_config.json` una voce con valori `null` e attendibilità
  `NON_VERIFICATA`. Finché non la compili, i carrelli con quella farmacia restano "incompleti" e non possono vincere.
- **Scoperta settimanale**: lancia il probe su al massimo 5 nuovi siti (`probe_per_discovery_run`). Non promuove mai nulla.

## Scoperta automatica di nuove farmacie

```bash
python pharmacy_discovery.py              # raccolta + verifica di un lotto (default 15 siti)
python pharmacy_discovery.py --batch 5 --dry-run
```

Fonti, configurabili in `pharmacies.json → discovery`:

| Fonte | Stato al 18/11/2026 |
|---|---|
| Elenco curato di farmacie online note | in uso, da verificare |
| Open data Regione Puglia | in uso: farmacie e parafarmacie autorizzate alla vendita online di medicinali OTC |
| Elenco del Ministero della Salute | protetto da una verifica JavaScript anti-bot: consultazione **solo manuale** |
| Trovaprezzi e idealo | rispondono 403 ai bot: saltati |
| "iFarma" | non risulta un comparatore di prezzi (`ifarma.net` è una rivista): aggiungi l'URL giusto se esiste |

Regole:
- i siti nuovi entrano come `DISCOVERED`, la verifica automatica li porta al massimo a `UNDER_REVIEW`, e **non diventano mai `ACTIVE` da soli**;
- l'autorizzazione alla vendita online di medicinali non implica la vendita di dermocosmetici e non viene presentata come tale;
- i domini esclusi (marketplace, comparatori) sono in `excluded_domains`;
- i siti `BLOCKED` vengono ricontrollati ogni settimana con una sola richiesta a robots.txt.

---

## Pubblicazione su GitHub

1. **Crea il repository** su GitHub (pubblico o privato). Vedi i limiti sotto prima di scegliere.
2. **Carica i file** (dalla cartella del progetto, dove `git init` è già stato eseguito):
   ```bash
   git add -A
   git commit -m "Prima versione del price tracker"
   git remote add origin https://github.com/<tuo-utente>/<tuo-repo>.git
   git push -u origin main
   ```
3. **Configura Actions:** *Settings → Actions → General → Workflow permissions* → **Read and write permissions**.
   Il workflow dichiara già `permissions: contents: write`.
4. **Attiva Pages:** *Settings → Pages → Build and deployment → Source: Deploy from a branch → `main` / `(root)`*.
   Il file `.nojekyll` evita l'elaborazione Jekyll. La dashboard sarà su `https://<tuo-utente>.github.io/<tuo-repo>/`.
5. **Verifica l'automazione:** *Actions → Scrape prezzi → Run workflow*. Al termine controlla:
   - che ci sia un commit "Aggiornamento prezzi …";
   - la dashboard (Pages si aggiorna in 1–2 minuti);
   - l'artifact dei log.

### Come funziona l'orario delle 08:00

I cron di GitHub sono in UTC, quindi ci sono due slot: 06:00 UTC (08:00 con l'ora legale) e 07:00 UTC (08:00 con l'ora solare).
Lo step iniziale lascia proseguire l'esecuzione solo se a Roma sono le 08, oppure se lo slot che l'ha avviata è quello delle 08
per l'offset del giorno. La seconda condizione rende il controllo robusto ai ritardi delle code di GitHub, che a volte superano l'ora.
Il file `.github/last_scheduled_run` impedisce comunque due esecuzioni pianificate nello stesso giorno.
Al cambio dell'ora non serve toccare nulla.

Il lunedì il workflow esegue anche la scoperta di nuove farmacie e la verifica delle spedizioni. Si possono forzare con l'input `weekly_tasks`.

### Consumo dei minuti gratuiti

Un'esecuzione dura circa 7–8 minuti, quasi tutti di attesa per rispettare il ritmo dei negozi Shopify.
- Repository pubblico: minuti illimitati.
- Repository privato: circa 240 min/mese su 2.000 gratuiti. Attenzione: GitHub Pages su repository privati richiede un piano a pagamento.

---

## Troubleshooting

| Sintomo | Causa probabile | Cosa fare |
|---|---|---|
| `BLOCCATO` / stato `BLOCKED` | protezione anti-bot (403, "Verifying Device", challenge) | Niente: è voluto. Il sito viene ricontrollato ogni settimana. |
| `LIMITATO (429)` | rate limit del sito (tipico di Shopify) | Il dominio è sospeso per quell'esecuzione. Se succede spesso, aumenta `min_delay_seconds` della classe. Per farmafides e vigorito, ancora da attivare, avvia il workflow con l'input `activate_pharmacies` = `farmafides farmacievigorito`: il tentativo parte dall'IP di GitHub. |
| `NON TROVATO` | URL prodotto cambiato o prodotto tolto dal catalogo | `python scraper.py --pharmacy <id> --refresh-candidates`. |
| `MATCH NON AFFIDABILE` | la pagina è una variante, un multipack o un altro formato | Leggi `match_reasons` in `data.json`. Se è un falso negativo, rivedi `match` in `config.json`. |
| `PREZZO NON VERIFICATO` | fonti di prezzo in disaccordo, prezzo solo nel testo, o prodotto esaurito senza prezzo | Vedi `price_sources` e `notes` dell'offerta. |
| Pagine PrestaShop vuote | header `Accept` con `application/json` | È già corretto in `scrapers/http.py`: non reintrodurlo. |
| Errore TLS su un sito | il server non invia il certificato intermedio | La sessione completa la catena via AIA con firma verificata. Non disattivare mai la verifica TLS. |
| Overpass in timeout | l'istanza pubblica è sovraccarica | Il codice prova i mirror. Riprova più tardi: i dati restano in cache per una settimana. |
| Il workflow non parte | permessi o slot orario | Controlla i permessi di scrittura e il log dello step "Controllo orario". |

## Limiti noti (onesti)

- **Copertura**: le farmacie monitorate sono un campione. "Miglior prezzo trovato tra le farmacie monitorate" non significa "del mercato".
- **Siti non monitorabili**:
  - topfarmacia.it (403 Akamai);
  - efarma.com (dal 18/11/2026 "Verifying Device");
  - Ministero della Salute, Trovaprezzi e idealo (anti-bot).
- **P002 (Lipikar Baume AP+M 400 ml)** risultava esaurito o senza prezzo in tutte le farmacie monitorate il 18/11/2026. Nelle sitemap compare
  il formato "AP+MAX" 400 ml, con codici nuovi. Se è il successore e vuoi seguirlo, aggiorna il catalogo in `config.json` con i nuovi EAN e Minsan verificati.
- **Prezzi in negozio**: non sono pubblici, quindi la modalità "tutto in farmacia" è calcolabile solo con `store_prices.json`.
- **Viaggi**: sono stime, calcolate in linea d'aria dal centro della località, senza percorsi reali né tempo.
- **Spedizioni**: vale la tariffa per l'Italia continentale. Isole, Livigno, Venezia e i supplementi per peso non sono modellati.
  Farmacia Iris Diana calcola la spedizione al checkout in base al peso: il costo è sconosciuto.
- **Disponibilità**: è quella dichiarata dalla pagina al momento della lettura. Su siti senza dati strutturati (farmavillage)
  è dedotta dalla presenza del pulsante carrello.
- **Uso**: progetto personale e non commerciale. Se il repository è pubblico, anche i prezzi raccolti lo diventano.
  Le condizioni d'uso di alcuni siti (es. efarma) consentono solo un uso personale.
- **Nominatim e Overpass**: il loro robots.txt esclude i crawler dagli endpoint API. Qui sono usati come API, nei limiti delle loro usage policy
  (User-Agent identificabile, una geocodifica in cache, una query a settimana). In alternativa puoi inserire lat/lon in `config.json → location`.

## Struttura

```
├── scraper.py  pharmacy_discovery.py  optimizer.py  shipping.py  physical_pharmacies.py  storage.py  maintenance.py
├── config.json  data.json  price_history.json  pharmacies.json  shipping_config.json
├── product_urls.json  physical_pharmacies.json  store_prices.json
├── index.html  README.md  CLAUDE.md  requirements.txt  .nojekyll
├── assets/    (fogli di stile, moduli JavaScript e favicon della dashboard)
├── scrapers/  (base, http, robots, extract, matcher, sitemap, platforms, generic_jsonld, un modulo per farmacia)
├── docs/probe/  (un report per ogni dominio valutato con lo scraper generico)
├── tests/     (test_scraper, test_optimizer, test_shipping, test_data, fixtures/ con dati SIMULATI)
├── docs/analisi_siti.md
├── docs/design-system.md  docs/ux-audit.md  docs/screenshots/
├── logs/
└── .github/workflows/scrape.yml
```
