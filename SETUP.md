# Setup rapido – Dermocosmetic Price Tracker

Guida breve per pubblicare il progetto. I dettagli sono nel [README](README.md#pubblicazione-su-github).

## 1. Creare il repository su GitHub

- https://github.com/new → nome a scelta (es. `dermatite-nicolo`)
- **Pubblico** se vuoi GitHub Pages gratis (su repository privati Pages richiede un piano a pagamento)
- Non inizializzarlo con README

## 2. Caricare il codice

```bash
git remote add origin https://github.com/<tuo-utente>/<tuo-repo>.git
git branch -M main
git push -u origin main
```

## 3. Permessi di GitHub Actions

*Settings → Actions → General → Workflow permissions* → **Read and write permissions**.

Non serve nessun token personale (PAT) né alcun secret: il workflow usa il `GITHUB_TOKEN` automatico,
con i permessi dichiarati nel file (`contents: write`). Non creare un secret chiamato `GITHUB_TOKEN`.

## 4. Abilitare GitHub Pages

*Settings → Pages → Build and deployment → Source: Deploy from a branch → `main` / `(root)`* → Salva.

Dashboard: `https://<tuo-utente>.github.io/<tuo-repo>/`.
Ogni commit dei dati su `main` riavvia il deploy automatico "pages build and deployment".

## 5. Verificare il workflow

*Actions → **Scrape prezzi** → Run workflow*. Il flusso è:

```text
test offline → scraping → validazione JSON → commit dei dati su main → deploy di GitHub Pages
```

- Orario automatico: **ogni giorno alle 08:00 ora di Roma** (due cron in UTC, 06:00 e 07:00, e un controllo che lascia
  passare solo quello giusto). Il lunedì anche scoperta farmacie e verifica spedizioni.
- Se i test o la validazione falliscono non viene committato nulla e restano pubblicati i dati precedenti.
- Log sempre disponibili come artifact; in caso di errore anche i file generati (`dati-<id>`).

## Test e prove locali

```bash
pip install -r requirements.txt
python -m pytest -q                           # tutti offline; il test del browser si salta senza Playwright
python validation.py                          # controlla i JSON pubblicati
python scraper.py --pharmacy clubfarma --dry-run   # prova una farmacia senza scrivere data.json
python -m http.server 8000                    # dashboard su http://localhost:8000
```

`python scraper.py --pharmacy <id>` **senza** `--dry-run` è una scansione parziale: aggiorna solo quella farmacia e conserva
le offerte delle altre (vedi README, "Pubblicazione dei dati e scansioni parziali").

## Farmacie monitorate

L'elenco aggiornato delle farmacie `ACTIVE` è in `pharmacies.json` e nella sezione *Mercato* della dashboard.
Per aggiungerne altre: `CLAUDE.md → Aggiungere una farmacia`. Per toglierne una: `python pharmacy_discovery.py demote <dominio>`
(le sue offerte escono subito dal carrello).

## Da ricordare

- Il confronto riguarda solo le farmacie monitorate; prezzi e disponibilità vanno ricontrollati sul sito della farmacia.
- Il progetto non dà consigli medici.
- Aggiorna `config.json → http.user_agent` con l'URL del tuo repository.
