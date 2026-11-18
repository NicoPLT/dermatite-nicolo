# CLAUDE.md — Dermocosmetic Price Tracker Italia

App statica (GitHub Pages) + scraper Python eseguito da GitHub Actions. Risponde alla domanda:
"Dove e come compro TUTTI e 5 i prodotti spendendo il meno possibile, incluse spedizioni e spostamenti?"

## Catalogo (config.json → products)

| ID | Prodotto | Formato | EAN | Minsan |
|---|---|---|---|---|
| P001 | Uriage Xémose Olio Detergente | 1000 ml | 3661434003370 | 927291005 |
| P002 | La Roche-Posay Lipikar Baume AP+M | 400 ml | 3337875696548 | 979265434 |
| P003 | Ceramol 311 Lipocrema | **100 ml** (50 ml fino al 18/11/2026, in `format_history`) | 8029408107014 | 980512762 |
| P004 | Ceramol Crema Mani 311 | 100 ml | 8029408093058 | 977658020 |
| P005 | La Roche-Posay Anthelios UVMune 400 Fluido Invisibile SPF50+ (senza profumo) | 50 ml | 3337875797597 | 982981666 |

- P003 e P004 sono della stessa linea e hanno lo stesso formato: le parole chiave sono reciprocamente esclusive ("lipocrema" / "mani").
  `scrapers.base.resolve_shared_urls` impedisce che una stessa pagina valga per due prodotti.
- Cambio formato: aggiorna `config.json` (con `format_history`), poi `python maintenance.py format-change <ID>`.
  Lo storico conserva il vecchio formato nel campo `format`; dashboard e ottimizzatore usano solo il formato corrente.

## Regole non negoziabili

- **Mai prezzi inventati o stimati.** Un valore mancante è `null`/`None`, mai `0`. Dati simulati SOLO in `tests/fixtures/`.
- **Spedizione sconosciuta ≠ gratis.** La soglia di spedizione gratuita si applica solo se `free_threshold_verified` è vero.
- In UI la dicitura è sempre "Miglior prezzo trovato tra le farmacie monitorate", mai "migliore del mercato".
- **Rispetta robots.txt e ToS.** Niente bypass di CAPTCHA, Cloudflare, Akamai o login. Niente `playwright-stealth`, niente UA falsi.
  Ritmo: ≥2 s tra le richieste allo stesso dominio, 1 richiesta alla volta per dominio, User-Agent identificabile (`config.json → http`).
- Se un sito blocca (403/429 persistente/challenge): stato `BLOCKED` o `RATE_LIMITED`, documentalo in `pharmacies.json` e passa oltre.
- Offerte con match prodotto/formato non affidabile → escluse dalla classifica verificata.
- Scrittura JSON **atomica** (`storage.write_json_atomic`: file temporaneo + `os.replace`).
  Mai sovrascrivere `data.json`/`price_history.json` con dati vuoti se lo scraping fallisce.
- Nessuna credenziale o dato personale nei JSON pubblici. Località = città o CAP, mai l'indirizzo.
- Le nuove farmacie scoperte non diventano mai `ACTIVE` da sole: l'attivazione è manuale.
- Il ritiro fisico non è mai gratis di default. Il costo del viaggio è sempre una stima etichettata come tale.

## Struttura

- `scraper.py`: orchestratore giornaliero (scraping → ottimizzazione → `data.json` + `price_history.json`).
- `scrapers/`: libreria di scraping.
  - `http.py`: sessione "educata" (rate limit per dominio, robots, retry, Retry-After, AIA TLS).
  - `robots.py`: parser robots.txt con wildcard.
  - `extract.py`: prezzi/disponibilità/identificativi da JSON-LD → microdata → meta → HTML → CSS → testo.
  - `matcher.py`: match prodotto (EAN/Minsan, poi nome+marca+formato rigorosi).
  - `sitemap.py`: lettura sitemap e filtro degli URL candidati.
  - `base.py`: classe `BaseScraper` da cui ereditano tutti gli scraper.
  - `platforms.py`: comportamenti per piattaforma (PrestaShop, Shopware, OpenCart, Shopify...).
  - Un modulo per farmacia: `alpifarma.py`, `redcare.py`, `efarma.py`, `topfarmacia.py`; le altre in `additional.py`.
  - `generic_jsonld.py`: scraper generico (solo dati strutturati, mai testo visibile) per i siti senza scraper dedicato,
    più probe, compatibilità `FULL`/`PARTIAL`/`NONE`/`BLOCKED`, report in `docs/probe/` e `candidate_offers`.
  - `__init__.py`: registro `id farmacia → classe`. Il dispatcher (`scraper.make_scraper`) usa lo scraper dedicato se esiste,
    altrimenti `generic_jsonld` per i domini `ACTIVE` con `scraper: "generic_jsonld"`.
- **Siti non ACTIVE**: le loro offerte vanno SOLO in `data.json → candidate_offers` (`NON_VERIFICATO`), mai in ottimizzatore o storico.
  La promozione è solo manuale: `pharmacy_discovery.py promote <dominio>`, con probe di meno di 7 giorni e conferma esplicita.
- `shipping.py` + `shipping_config.json`: costi di spedizione, soglie, attendibilità.
- `physical_pharmacies.py`: farmacie fisiche (Nominatim + Overpass/OSM) e costi di viaggio stimati.
- `optimizer.py`: ottimizzatore esaustivo del carrello (5 prodotti, modalità A–E).
- `pharmacy_discovery.py`: scoperta settimanale di nuovi rivenditori (`pharmacies.json`, stato `DISCOVERED`).
- `storage.py`: I/O JSON atomico, storico append-only con campo `format` e migrazione senza perdite.
- `maintenance.py`: operazioni sui dati (cambio formato, migrazione dello storico).
- `index.html`, `assets/styles.css` e `assets/app.js`: dashboard statica (Tailwind CDN + Chart.js CDN + moduli JS vanilla). Ogni modifica UI deve usare i token e i componenti definiti nel [design system](docs/design-system.md).
- Il confronto delle modalità e le stime locali usano i risultati pubblicati dall’ottimizzatore in `carts.channel_comparison` e `carts.travel_previews`. Un importo assente si presenta come «n.d.».
- Per verificare una modifica UI usa le anteprime sotto `tests/fixtures/ui/` con il parametro `?data=tests/fixtures/ui/<caso>/` e il banner «DATI DI TEST, NON REALI». Il [README](README.md#dashboard-e-anteprime-di-sviluppo) riporta casi e comando per le schermate.

## Comandi

```bash
pip install -r requirements.txt
python -m pytest -q                         # tutti i test (offline, usano tests/fixtures)
python -m py_compile *.py scrapers/*.py tests/*.py
python scraper.py                           # scraping reale delle farmacie ACTIVE
python scraper.py --pharmacy clubfarma      # una sola farmacia
python scraper.py --dry-run                 # niente scrittura su data.json/price_history.json
python scraper.py --refresh-candidates      # rilegge sitemap/ricerche per gli URL candidati
python scraper.py --product P003 --dry-run  # ricerca mirata (solo dry-run)
python maintenance.py format-change P003    # applica un cambio formato già scritto in config.json
python scraper.py --probe <dominio>         # probe JSON-LD (scraper generico) -> docs/probe/<dominio>.md
python scraper.py --probe-all --probe-limit 5
python pharmacy_discovery.py promote <dominio>   # promozione manuale (conferma a terminale)
python pharmacy_discovery.py demote <dominio>
python shipping.py --check                  # verifica automatica delle pagine spedizione
python physical_pharmacies.py               # aggiorna farmacie fisiche vicine alla località
python pharmacy_discovery.py                # scoperta nuovi rivenditori (mai ACTIVE da soli)
python optimizer.py                         # ricalcola i carrelli da data.json esistente
python -m http.server 8000                  # dashboard su http://localhost:8000
```

## Aggiungere una farmacia

1. Verifica robots.txt, ToS, sitemap e JSON-LD (vedi `docs/analisi_siti.md`).
2. Aggiungi una classe in `scrapers/additional.py` (o un nuovo modulo) che eredita da una piattaforma in `platforms.py`.
3. Registra la classe in `scrapers/__init__.py`.
4. Aggiungi la farmacia a `pharmacies.json` (stato `VERIFIED`) e le spedizioni a `shipping_config.json`.
5. Lancia `python scraper.py --pharmacy <id> --dry-run`. Se estrae almeno un'offerta autentica, porta lo stato ad `ACTIVE`.
