# Analisi dei siti candidati (Fase 1)

Data verifica: **18/11/2026**, richieste HTTP reali da rete domestica italiana.
User-Agent usato: `DermoPriceTracker/0.1 (+personal non-commercial price monitoring; contact via GitHub repo)`.
Ritmo: una richiesta alla volta per dominio, pausa di almeno 2 s tra le richieste.

Il dettaglio per farmacia è in [`pharmacies.json`](../pharmacies.json).

## Sintesi

| Dominio | Raggiungibile | robots.txt | Ricerca interna consentita | Sitemap | JSON-LD Product | EAN in JSON-LD | Stato |
|---|---|---|---|---|---|---|---|
| alpifarma.it | sì | 200 | **no** (`?search_query=`, `/ricerca`) | sì (PrestaShop) | sì | sì | VERIFIED |
| redcare.it | sì | 200 | **no** (`/*search.htm*`) | sì (78 file prodotto) | sì | sì | VERIFIED |
| efarma.com | sì (Cloudflare) | 200 | **no** (`/catalogsearch/`, `?q=`) | sì | sì | sì | VERIFIED |
| farmasave.it | sì | 200 | sì | sì (Shopware, gz) | sì | no (Minsan in sku) | VERIFIED |
| farmastrike.it | sì | 200 | sì | sì (OpenCart) | sì | no (Minsan in sku) | VERIFIED |
| farmafides.it | sì (Cloudflare) | 200 | sì | sì (72 file prodotto) | non verificato | non verificato | UNDER_REVIEW (429) |
| farmacievigorito.it | sì (Cloudflare) | 200 | **no** (`/search`) | sì (103 file prodotto) | non verificato | non verificato | UNDER_REVIEW (429) |
| verafarma.it | sì | 200 | sì | sì (Shopware, gz) | sì, **prezzo inaffidabile** | no (Minsan in sku) | VERIFIED |
| meafarma.it | sì | 200 | sì | sì (OpenCart) | sì | no (Minsan in sku) | VERIFIED |
| farmaciairisdiana.it | sì | 200 | **no** (`?search_query=`) | sì (PrestaShop) | **no** (solo meta) | EAN nell'URL | VERIFIED |
| farmavillage.it | sì (TLS incompleto) | 404 (nessuna regola) | sì | non trovata | **no** (solo HTML) | no | VERIFIED |
| clubfarma.it | sì | 200 | **no** (`?search_query=`) | sì (PrestaShop) | sì | sì (+ Minsan) | VERIFIED |
| topfarmacia.it | **no**: 403 Akamai | 403 | non verificabile | non verificabile | non verificabile | non verificabile | **BLOCKED** |

`VERIFIED` qui significa che robots.txt, l'accesso alle pagine prodotto e la presenza di prezzi sono stati controllati a mano. Una farmacia passa ad `ACTIVE` solo dopo che il suo scraper ha estratto almeno un'offerta autentica (Fase 3/9).

## Conseguenze sul metodo di raccolta

1. **La ricerca interna è vietata da robots.txt su 6 siti su 11** (PrestaShop, Magento, redcare, vigorito). Su questi si usa solo
   **sitemap → URL prodotto → pagina prodotto**. Le pagine prodotto sono sempre consentite.
2. **Le sitemap sono grandi** (fino a 227.000 URL su redcare, 103 file su vigorito). Vanno lette **al massimo una volta a settimana**.
   Gli URL prodotto trovati restano in cache (`product_urls.json`). L'esecuzione giornaliera legge solo le poche pagine prodotto già note:
   circa 5 per farmacia.
3. **Shopify/Cloudflare applica un rate limit** (`429 local_rate_limited`, `Retry-After: 60`). È scattato dopo la lettura completa
   delle sitemap (72 e 103 file) ed è durato oltre 20 minuti. Lo scraper rispetta `Retry-After` e, dopo un 429, sospende il dominio
   per il resto dell'esecuzione invece di riprovare a raffica.
4. **Il JSON-LD non è sempre affidabile.** Su verafarma.it il JSON-LD indica 9,83 € mentre il prezzo mostrato, il microdata e il
   meta tag indicano 10,42 € (Ceramol Crema Mani, 18/11/2026). Il JSON-LD sembra condiviso con farmasave.it, che ha davvero 9,83 €.
   Lo scraper raccoglie quindi il prezzo da tutte le fonti (JSON-LD, microdata, meta, HTML), le confronta e segnala i conflitti.
   Se non c'è una maggioranza, l'offerta diventa `PREZZO NON VERIFICATO`.
5. **farmavillage.it** invia il certificato TLS senza l'intermedio Let's Encrypt (YR2). La catena viene completata scaricando
   l'intermedio dall'URL AIA del certificato. La verifica TLS resta piena, contro i CA di `certifi` (nessun `verify=False`).
6. **topfarmacia.it** risponde 403 "Access Denied" (Akamai) già su `robots.txt`. Non si tenta alcun aggiramento.

## Identificativi prodotto verificati

EAN e codice Minsan (codice ministeriale italiano del parafarmaco) sono stati confermati dal JSON-LD di almeno due farmacie indipendenti.

| ID | Prodotto | EAN (GTIN-13) | Minsan | Fonti (JSON-LD) |
|---|---|---|---|---|
| P001 | Uriage Xémose Olio Detergente (Huile Lavante Apaisante) 1 L | 3661434003370 | 927291005 | clubfarma, redcare, efarma |
| P002 | La Roche-Posay Lipikar Baume AP+M 400 ml | 3337875696548 | 979265434 | clubfarma (EAN+Minsan), meafarma (Minsan), alpifarma/irisdiana (EAN nell'URL) |
| P003 | Ceramol 311 Lipocrema 50 ml *(formato monitorato fino al 18/11/2026; ora 100 ml, vedi in fondo)* | 8029408107021 | 980512750 | clubfarma, redcare, efarma |
| P004 | Ceramol Crema Mani 311 100 ml | 8029408093058 | 977658020 | alpifarma, clubfarma, efarma, redcare |
| P005 | La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+ 50 ml | 3337875797597 | 982981666 | redcare, clubfarma; meafarma lo chiama esplicitamente "Senza Profumo" |

Trappole trovate, che il matcher deve gestire:

- **P002**: il Minsan 981401019 (redcare IT981401019, verafarma) è il formato **200 ml**, non 400 ml. Vanno escluse anche le varianti
  "AP+M Light", "AP+MAX", "Paper", gli omaggi da 15 ml e le confezioni "set da 2" / "duo pack".
- **P003**: verafarma vende anche una "Ceramol 311 Lipocrema 50 ml" con Minsan **970539351**, senza EAN e oggi non disponibile.
  Il nome coincide ma l'identificativo è diverso, quindi è un conflitto di identificativo e l'offerta va **esclusa**.
  Esistono anche il formato 100 ml (EAN 8029408107014) e i duo pack.
- **P005**: il "Fluido Invisibile" in commercio in Italia è la versione **senza profumo** (EAN 3337875797597). Vanno escluse:
  colorato/tinted (3337875797641), oil control (con/senza profumo), Dermo-Pediatrics/bambini, edizione limitata (3337875925853, Minsan 989974567),
  Pro Sport, bundle con Mela B3 o Hyalu B5, duo pack.
- **P001**: vanno esclusi i formati 50/200/400/500 ml, "Balsamo Olio", "Syndet" e i bundle (es. olio 1 L + balsamo 500 ml su efarma).

## Copertura dei 5 prodotti (da sitemap e ricerca, prima dello scraping)

| Farmacia | P001 | P002 | P003 | P004 | P005 |
|---|---|---|---|---|---|
| alpifarma | ✓ | ✓ | ✓ | ✓ | – |
| redcare | ✓ | ✓ | ✓ | ✓ | ✓ |
| efarma | ✓ | – | ✓ | ✓ | – |
| farmasave | ✓ | – | ✓ | ✓ | ✓ |
| farmastrike | ✓ | ✓ | ✓ | ✓ | – |
| farmafides | ✓ | ✓ | ✓ | ✓ | ✓ |
| farmacievigorito | ✓ | ✓ | ✓ | ✓ | ✓ |
| verafarma | ✓ | – | ✓ | ✓ | ✓ |
| meafarma | ✓ | ✓ | ✓ | ✓ | ✓ |
| farmaciairisdiana | ✓ | ✓ | ✓ | ✓ | ✓ |
| farmavillage | ✓ | – | – | – | ✓ |
| clubfarma | ✓ | ✓ | ✓ | ✓ | ✓ |

✓ = URL candidato trovato. Il match vero si conferma sulla pagina prodotto, tramite EAN/Minsan o nome+marca+formato.

## Termini di servizio

Le condizioni di vendita e d'uso sono state lette automaticamente solo dove robots.txt lo consente, cercando parole chiave
(scraping, crawler, robot, estrazione, mezzi automatizzati, riproduzione...).

- **Nessuna clausola esplicita anti-scraping** trovata su alpifarma, efarma, farmasave, farmastrike, farmafides, verafarma, meafarma, irisdiana, clubfarma.
- **redcare.it** (CGU): divieto generico di copiare e riprodurre i contenuti del sito (proprietà intellettuale).
- **efarma.com** (condizioni d'uso): uso del sito consentito solo in modo "meramente personale e non commerciale"; vietato copiare o distribuire contenuti per scopi commerciali.
- **farmacievigorito.it** e **meafarma.it** escludono da robots.txt le proprie pagine dei termini o delle spedizioni: non sono state lette automaticamente.

Questo progetto è personale e non commerciale. Estrae solo dati fattuali (prezzo, disponibilità, spedizione), cita la fonte con un
link e non ripubblica testi, immagini o marchi. Se la dashboard è pubblica su GitHub Pages, anche i prezzi lo diventano: vedi i limiti nel README.
Questa analisi a parole chiave **non sostituisce una lettura legale completa** dei termini.

## Note sul robots.txt di efarma.com

efarma.com blocca esplicitamente i crawler di addestramento AI (GPTBot, ClaudeBot, CCBot, Google-Extended...). Il tracker non è un
crawler di addestramento: usa un User-Agent proprio e segue le regole del gruppo `User-agent: *`. Se in futuro efarma bloccasse anche
quel gruppo o il nostro UA, lo scraper lo rileverebbe e salterebbe il dominio.

## Aggiornamento dopo lo scraping reale (Fase 9, 18/11/2026 ~10:00)

| Farmacia | Esito | Offerte con prezzo verificato | Stato |
|---|---|---|---|
| clubfarma | OK, match per EAN/Minsan | 5/5 (P002 esaurito) | ACTIVE |
| farmaciairisdiana | OK | 5/5 (P002 esaurito) | ACTIVE |
| farmastrike | OK | 5/5 (P002 esaurito) | ACTIVE |
| meafarma | OK | 5/5 (P002 esaurito) | ACTIVE |
| alpifarma | OK; P002 senza prezzo ("Prodotto non disponibile") | 4/5 | ACTIVE |
| redcare | OK; P002 senza prezzo in pagina | 4/5 | ACTIVE |
| farmasave | OK; P002 non a catalogo | 4/5 | ACTIVE |
| verafarma | OK; P002 non a catalogo; JSON-LD con prezzi di farmasave scartato a maggioranza su 3 prodotti | 4/5 | ACTIVE |
| farmavillage | OK (Minsan da "Codice articolo"); P002–P004 non a catalogo | 2/5 | ACTIVE |
| efarma | dal 18/11 risponde 403 "Verifying Device" anche su robots.txt | 0/5 | BLOCKED |
| topfarmacia | 403 Akamai | 0/5 | BLOCKED |
| farmafides | 429 persistente anche al ritmo di 1 richiesta/65 s | 0/5 | UNDER_REVIEW |
| farmacievigorito | 429 persistente (una sola offerta letta in un tentativo precedente: P001 20,24 € con match per ID) | 0/5 | UNDER_REVIEW |

Note:
- **P002 (Lipikar Baume AP+M 400 ml)** non è acquistabile in nessuna farmacia monitorata: esaurito su 4 siti, senza prezzo su 2, assente sugli altri.
  Le sitemap mostrano il formato "AP+MAX" 400 ml (Minsan nuovi, serie 95…), probabilmente il successore: decisione lasciata all'utente.
- Il 429 dei due negozi Shopify è probabilmente legato all'IP usato per l'analisi, che aveva letto tutte le loro sitemap.
  Il workflow ha l'input `activate_pharmacies` per riprovare l'attivazione dall'IP di GitHub Actions.
- Spedizioni verificate sulle pagine ufficiali per 8 farmacie (`shipping.py --check` le ricontrolla ogni settimana).
  Restano sconosciute: Iris Diana (calcolata a peso al checkout), efarma (bloccata), i due Shopify e topfarmacia.
- Farmacie fisiche: 236 farmacie OSM nel riquadro di Milano, di cui le 25 più vicine entro 3 km dal centro.
  L'interrogazione Overpass funziona solo come bbox via POST: le query `around` via GET andavano in timeout su tutte le istanze.
- Scoperta: 131 candidati (9 dall'elenco curato e 122 dall'open data della Regione Puglia).
  I primi 5 verificati sono passati a UNDER_REVIEW; farmacialoreto.it e farmacosmo.it hanno URL candidati per tutti e 5 i prodotti.
  Ministero della Salute (verifica JavaScript) e Trovaprezzi (403) non sono consultabili automaticamente.

## Cambio formato P003: 50 ml → 100 ml (18/11/2026)

Nuovi identificativi, verificati sul JSON-LD di tre farmacie indipendenti (gtin13 + sku):

| ID | Prodotto | EAN | Minsan | Fonti |
|---|---|---|---|---|
| P003 | Ceramol 311 Lipocrema **100 ml** | 8029408107014 | 980512762 | clubfarma (18,73 €), redcare (18,99 €), alpifarma (19,71 €) |

- I vecchi codici del 50 ml (EAN 8029408107021, Minsan 980512750) non sono più usati dal matcher.
  Restano solo in `format_history` per tracciabilità.
- verafarma ha una seconda "Lipocrema 100 ml" con Minsan **977658107**, senza EAN e "non disponibile": è un conflitto di identificativo e va esclusa.
  È lo stesso schema del 50 ml, dove esisteva il Minsan 970539351.
- Su redcare il titolo è "Ceramol LIPOCREMA 311", senza formato: la pagina è valida solo grazie all'identificativo.
- Rischio di confusione con P004 (Crema Mani 311, 100 ml): parole chiave reciprocamente esclusive e controllo "una pagina = un prodotto".
- Storico: le 8 rilevazioni P003 del 18/11 hanno `format: "50 ml"`, sono conservate ed escluse da grafico e ottimizzatore.
