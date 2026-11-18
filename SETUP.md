# Setup Rapido – Dermocosmetic Price Tracker

## ✅ Fatto automaticamente

- [x] Progetto committato (git init + initial commit)
- [x] 196 test passano
- [x] Scraper funziona (dry-run: 34 offerte da 9 farmacie)
- [x] Dashboard pronta (HTML statica, Tailwind CDN, Chart.js)
- [x] GitHub Actions workflow pronto

## 📋 Prossimi passi (5 minuti)

### 1. Creare il repo su GitHub
```
https://github.com/new
- Nome: dermatite-nicolo
- Pubblico (necessario per Pages)
- NON inizializzare con README
```

### 2. Pushare il codice
```bash
git remote add origin https://github.com/TUO_USERNAME/dermatite-nicolo.git
git branch -M main
git push -u origin main
```

### 3. Abilitare GitHub Pages
**Repo → Settings → Pages**
- Source: `main` (root)
- Salva

🔗 Dashboard live: `https://TUO_USERNAME.github.io/dermatite-nicolo/`

### 4. Configurare GitHub Actions (scraper automatico)

**A. Creare un PAT:**
- GitHub → Settings → Developer settings → Personal access tokens (classic)
- New token: nome `GITHUB_TOKEN_SCRAPER`
- Permessi: `repo` + `workflow`
- Copia il token

**B. Aggiungere il secret al repo:**
- Repo → Settings → Secrets and variables → Actions
- New secret: `GITHUB_TOKEN` = token copiato

**C. Verificare il workflow:**
- Repo → Actions → dovresti vedere `scraper`
- Schedulo: ogni giorno alle 02:00 UTC (modificabile in `.github/workflows/scrape.yml`)

## 🧪 Test locali

```bash
# Tutti i test
python -m pytest -q

# Dry-run dello scraper
python scraper.py --dry-run

# Una sola farmacia
python scraper.py --pharmacy clubfarma --dry-run

# Dashboard locale
python -m http.server 8000
# Apri http://localhost:8000
```

## 📊 Data flow

1. GitHub Actions esegue `python scraper.py` ogni giorno
2. Genera `data.json` + `price_history.json`
3. Committa automaticamente i dati nel branch `main`
4. Dashboard ricarica i dati automaticamente

## ⚠️ Farmacie ACTIVE

Attualmente monitorizzate:
- ✅ alpifarma
- ✅ clubfarma
- ✅ farmaciairisdiana
- ✅ farmasave
- ✅ farmastrike
- ✅ farmavil lage
- ✅ meafarma
- ✅ redcare
- ✅ verafarma

Per aggiungerne altre: vedi `CLAUDE.md → Aggiungere una farmacia`

## 📝 Prima del primo scraping reale

Raccomandato:
1. Verificare che `config.json` abbia i 5 prodotti corretti
2. Testare 1-2 farmacie con `--pharmacy <id> --dry-run`
3. Quando tutto ok, fare `git push` e il primo scraping avverrà entro 24h

## 🎯 Prossimi obiettivi

- [ ] Push su GitHub
- [ ] Abilitare Pages
- [ ] Configurare GitHub Actions
- [ ] Primo scraping automatico ✨
- [ ] Aggiungere/verificare altre farmacie
- [ ] Monitorare P002 (difficile da trovare)
