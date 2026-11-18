"""Scoperta settimanale di nuovi rivenditori online -> pharmacies.json.

Regole:
- un sito scoperto entra come DISCOVERED; la verifica automatica lo porta al massimo a
  UNDER_REVIEW (raggiungibile, robots ok, piattaforma, sitemap, prodotti candidati);
  NON diventa mai ACTIVE da solo (attivazione manuale: scraper.py --pharmacy X --activate);
- l'autorizzazione alla vendita online di MEDICINALI (elenchi ministeriali/regionali) non implica
  la vendita di dermocosmetici: viene registrata come tale, senza attribuire altre certificazioni;
- i prezzi dei comparatori servono solo a scoprire rivenditori: non entrano mai nei dati;
- robots.txt rispettato; fonti bloccate (403/challenge) documentate e saltate;
- lista di esclusione domini (marketplace, comparatori, ...) in pharmacies.json -> excluded_domains.

Fonti (configurabili in pharmacies.json -> discovery):
  seed_domains        elenco curato di farmacie online note, da verificare
  puglia_open_data    open data Regione Puglia: farmacie/parafarmacie autorizzate alla vendita online OTC
  ministry_list       elenco del Ministero della Salute (oggi protetto da verifica JavaScript: solo manuale)
  comparators         comparatori di prezzo (Trovaprezzi/idealo: oggi 403 per i bot -> saltati)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import logging
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from storage import ROOT, now_utc_iso, read_json, today_rome, write_json_atomic

log = logging.getLogger("tracker.discovery")
REGISTRY_PATH = ROOT / "pharmacies.json"

DEFAULT_DISCOVERY = {
    "batch_size": 15,
    "seed_domains": [
        {"domain": "farmae.it", "name": "Farmaè"},
        {"domain": "amicafarmacia.com", "name": "Amica Farmacia"},
        {"domain": "1000farmacie.it", "name": "1000Farmacie"},
        {"domain": "farmacialoreto.it", "name": "Farmacia Loreto"},
        {"domain": "farmacosmo.it", "name": "Farmacosmo"},
        {"domain": "tuttofarma.it", "name": "Tuttofarma"},
        {"domain": "docpeter.it", "name": "DocPeter"},
        {"domain": "drmax.it", "name": "Dr. Max"},
        {"domain": "benufarmacia.it", "name": "BENU Farmacia"},
    ],
    "sources": {
        "puglia_open_data": {
            "enabled": True,
            "packages": [
                "https://dati.puglia.it/ckan/api/3/action/package_show?id=farmacie-autorizzate-alla-vendita-online-di-medicinali-sop-otc-per-uso-umano",
                "https://dati.puglia.it/ckan/api/3/action/package_show?id=open-data-set-origine-esercizi-commerciali-25",
            ],
        },
        "ministry_list": {
            "enabled": True,
            "url": "https://www.salute.gov.it/portale/temi/soggettoAutorizzatoVenditaOnlineFarmaciMDS.jsp",
            "note": "Il 18/11/2026 il sito risponde con una verifica JavaScript anti-bot: consultazione solo manuale.",
        },
        "comparators": {
            "enabled": True,
            "search_urls": [
                "https://www.trovaprezzi.it/categoria.aspx?libera={q}&id=-1",
            ],
            "note": "Trovaprezzi e idealo rispondono 403/challenge ai bot (18/11/2026): saltati. 'iFarma' non risulta "
                    "un comparatore di prezzi (ifarma.net è una rivista): aggiungi qui l'URL corretto se esiste.",
        },
    },
}
PLATFORM_MARKERS = [
    ("Shopify", re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I)),
    ("PrestaShop", re.compile(r"prestashop", re.I)),
    ("Shopware 6", re.compile(r"shopware", re.I)),
    ("Magento 2", re.compile(r"Magento_|mage/cookies|x-magento", re.I)),
    ("OpenCart", re.compile(r"catalog/view/(?:theme|javascript)", re.I)),
    ("WooCommerce", re.compile(r"woocommerce", re.I)),
]


def registrable(domain: str) -> str:
    d = domain.strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0].split(":")[0]
    return d[4:] if d.startswith("www.") else d


def slug_id(domain: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", registrable(domain).rsplit(".", 1)[0])


def is_excluded(domain: str, excluded: list[str]) -> bool:
    d = registrable(domain)
    return any(d == e or d.endswith("." + e) for e in excluded)


def new_entry(domain: str, name: str | None, source: str, extra: dict | None = None) -> dict:
    d = registrable(domain)
    entry = {
        "id": slug_id(d), "name": name or d, "domain": d, "base_url": f"https://{d}", "country": "IT",
        "ships_to_italy": None, "ships_to_italy_source": None,
        "status": "DISCOVERED", "source": source, "discovered_at": now_utc_iso(),
        "collection_method": None, "platform": None, "robots": None, "sitemaps": [], "structured_data": None,
        "tos": None, "last_verified": None, "scraper_status": "NO_SCRAPER",
        "products_candidate_from_sitemap": [], "products_verified_on_page": [], "errors": [],
        "otc_online_authorization": None,
        "notes": "Scoperto automaticamente: da verificare. Non viene mai attivato in automatico.",
    }
    if extra:
        entry.update(extra)
    return entry


# --------------------------------------------------------------------------- fonti

def source_seeds(cfg: dict, session) -> tuple[list[dict], dict]:
    found = [{"domain": s["domain"], "name": s.get("name"), "source": "elenco iniziale curato (da verificare)"}
             for s in cfg.get("seed_domains", [])]
    return found, {"status": "OK", "found": len(found)}


def _parse_csv_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        delim = ";" if line.count(";") >= line.count(",") and line.count(";") > 2 else ","
        rows.extend(csv.reader(io.StringIO(line), delimiter=delim))
    return rows


def source_puglia(cfg: dict, session) -> tuple[list[dict], dict]:
    found, errors = [], []
    for pkg_url in cfg.get("packages", []):
        try:
            res = session.get(pkg_url, accept="application/json")
            meta = json.loads(res.text)["result"]
            csv_urls = [r["url"] for r in meta.get("resources", []) if (r.get("format") or "").upper() == "CSV"]
            for cu in csv_urls[:1]:
                text = session.get(cu).content.decode("utf-8", errors="replace")
                rows = _parse_csv_rows(text)
                header = [h.strip().lower() for h in rows[0]]
                web_i = next((i for i, h in enumerate(header) if "web" in h or "website" in h), None)
                name_i = next((i for i, h in enumerate(header) if "denominazione" in h), 0)
                city_i = next((i for i, h in enumerate(header) if "comune" in h), None)
                if web_i is None:
                    continue
                for row in rows[1:]:
                    if len(row) <= web_i:
                        continue
                    site = row[web_i].strip()
                    if not re.search(r"[a-z0-9-]+\.[a-z]{2,}", site, re.I):
                        continue
                    found.append({
                        "domain": site, "name": row[name_i].strip(),
                        "source": "open data Regione Puglia (dati.puglia.it)",
                        "extra": {"otc_online_authorization": {
                            "listed_in": "elenco regionale Puglia dei soggetti autorizzati alla vendita online di medicinali OTC/SOP",
                            "dataset": meta.get("name"), "dataset_modified": meta.get("metadata_modified"),
                            "warning": "Autorizzazione alla vendita online di MEDICINALI: non implica la vendita di dermocosmetici."},
                            "city": row[city_i].strip() if city_i is not None and len(row) > city_i else None},
                    })
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{pkg_url}: {exc}")
    return found, {"status": "OK" if found else "ERRORE", "found": len(found), "errors": errors}


def source_ministry(cfg: dict, session) -> tuple[list[dict], dict]:
    from scrapers.http import DomainBlocked, FetchError

    url = cfg.get("url")
    try:
        if not session.is_allowed(url):
            return [], {"status": "VIETATO DA ROBOTS"}
        res = session.get(url)
        return [], {"status": f"HTTP {res.status}: parser non disponibile, consultazione manuale",
                    "note": cfg.get("note")}
    except DomainBlocked as exc:
        return [], {"status": "BLOCCATO", "detail": str(exc), "note": cfg.get("note")}
    except FetchError as exc:
        return [], {"status": "ERRORE", "detail": str(exc)}


def source_comparators(cfg: dict, session, products: list[dict]) -> tuple[list[dict], dict]:
    """Legge solo i NOMI/domini dei negozi dalle pagine risultati consentite (mai i link /goto/)."""
    from scrapers.extract import soup_of
    from scrapers.http import DomainBlocked, DomainRateLimited, FetchError
    from urllib.parse import quote_plus

    found, statuses = [], []
    for tmpl in cfg.get("search_urls", []):
        for p in products:
            url = tmpl.format(q=quote_plus(p["search_queries"][0]))
            try:
                if not session.is_allowed(url):
                    statuses.append(f"{urlsplit(url).netloc}: vietato da robots")
                    break
                res = session.get(url)
                soup = soup_of(res.text)
                for el in soup.select("[data-merchant-name], .merchant_name, .listing_item_merchant_name"):
                    name = (el.get("data-merchant-name") or el.get_text(" ", strip=True)).strip()
                    m = re.search(r"([a-z0-9-]+\.(?:it|com|eu|net))\b", name, re.I)
                    if m:
                        found.append({"domain": m.group(1), "name": name, "source": f"comparatore {urlsplit(url).netloc}"})
            except (DomainBlocked, DomainRateLimited) as exc:
                statuses.append(f"{urlsplit(url).netloc}: BLOCCATO ({exc})")
                break
            except FetchError as exc:
                statuses.append(f"{urlsplit(url).netloc}: errore {exc}")
    return found, {"status": "; ".join(statuses) or "OK", "found": len(found), "note": cfg.get("note")}


# --------------------------------------------------------------------------- verifica

def audit_site(session, base_url: str, products: list[dict], max_sitemaps: int = 6) -> dict:
    """Controlli leggeri e rispettosi: robots, homepage, piattaforma, sitemap, prodotti candidati."""
    from scrapers.extract import soup_of
    from scrapers.http import DomainBlocked, DomainRateLimited, FetchError
    from scrapers.sitemap import fetch_sitemap_urls, keyword_prefilter, rank_candidates

    out: dict = {"checked_at": now_utc_iso(), "reachable": False, "errors": []}
    try:
        pol = session.robots_policy(base_url)
        out["robots"] = {"status": pol.source_status, "mode": pol.mode, "note": pol.note, "sitemaps": pol.sitemaps}
        if not session.is_allowed(base_url + "/"):
            out["errors"].append("homepage vietata da robots.txt")
            out["verdict"] = "BLOCKED"
            return out
        res = session.get(base_url + "/")
        out["reachable"] = res.ok
        out["final_url"] = res.final_url
        html = res.text
        out["platform"] = next((name for name, rx in PLATFORM_MARKERS if rx.search(html)), "sconosciuta")
        soup = soup_of(html)
        out["json_ld_on_home"] = bool(soup.find("script", attrs={"type": "application/ld+json"}))
        out["title"] = soup.title.string.strip() if soup.title and soup.title.string else None
        sitemaps = pol.sitemaps or [base_url + "/sitemap.xml"]
        out["sitemaps"] = sitemaps
        cands: dict[str, list[str]] = {}
        try:
            urls, errs = fetch_sitemap_urls(lambda u: session.get(u).content, sitemaps[:1],
                                            prefilter=keyword_prefilter(products), max_children=max_sitemaps)
            out["sitemap_errors"] = errs[:3]
            for p in products:
                c = rank_candidates(urls, p, limit=3)
                if c:
                    cands[p["id"]] = c
        except FetchError as exc:
            out["sitemap_errors"] = [str(exc)]
        out["product_candidates"] = cands
        out["verdict"] = "UNDER_REVIEW" if res.ok else "INACTIVE"
    except (DomainBlocked, DomainRateLimited) as exc:
        out["errors"].append(str(exc))
        out["verdict"] = "BLOCKED"
    except FetchError as exc:
        out["errors"].append(str(exc))
        out["verdict"] = "INACTIVE"
    return out


def apply_audit(entry: dict, audit: dict) -> None:
    entry["last_verified"] = audit["checked_at"]
    entry["audit"] = audit
    entry["errors"] = audit.get("errors", [])
    entry["platform"] = audit.get("platform") or entry.get("platform")
    entry["sitemaps"] = audit.get("sitemaps") or entry.get("sitemaps") or []
    entry["products_candidate_from_sitemap"] = sorted((audit.get("product_candidates") or {}).keys())
    new_status = audit.get("verdict", "UNDER_REVIEW")
    if new_status == "ACTIVE":  # difesa: la scoperta non attiva mai
        new_status = "UNDER_REVIEW"
    if entry.get("status") != new_status:
        entry.setdefault("status_history", []).append({"at": audit["checked_at"], "status": new_status,
                                                       "reason": "verifica automatica della scoperta"})
    entry["status"] = new_status


def probe_new_sites(registry: dict, session, config: dict, *, limit: int, now: str | None = None) -> list[dict]:
    """Probe JSON-LD automatico sui siti appena scoperti/verificati (mai promossi).

    Massimo `limit` domini per esecuzione (default 5: resta nei limiti di GitHub Actions).
    Esclusi i siti con uno scraper dedicato, che hanno il loro percorso di attivazione.
    """
    from scrapers import REGISTRY
    from scrapers.generic_jsonld import apply_probe, probe, write_probe_markdown

    now = now or now_utc_iso()
    queue = [p for p in registry.get("pharmacies", [])
             if p.get("status") in ("DISCOVERED", "UNDER_REVIEW") and not p.get("probe") and p["id"] not in REGISTRY]
    queue.sort(key=lambda p: (0 if p.get("status") == "UNDER_REVIEW" else 1,
                              -len(p.get("products_candidate_from_sitemap") or []), p.get("discovered_at") or ""))
    todo = queue[: max(0, limit)]
    out = []
    with ThreadPoolExecutor(max_workers=min(5, len(todo) or 1)) as pool:
        results = list(pool.map(lambda e: (e, probe(e, session, config, config["products"], now)), todo))
    for entry, (report, _offers) in results:
        apply_probe(entry, report)
        write_probe_markdown(report, config["products"], ROOT / "docs" / "probe")
        out.append({"domain": entry["domain"], "compatibility": report["compatibility"],
                    "products_found": report["products_found"], "requests": report["requests"]})
    return out


def check_promotable(entry: dict, session, config: dict) -> list[str]:
    """Motivi per cui il dominio NON può essere promosso (lista vuota = promuovibile)."""
    from scrapers.generic_jsonld import COMPAT_FULL, COMPAT_PARTIAL, settings
    from storage import days_since

    problems = []
    if entry.get("status") == "ACTIVE":
        problems.append("il dominio è già ACTIVE")
    if entry.get("status") == "BLOCKED":
        problems.append("il dominio è BLOCKED")
    compat = entry.get("jsonld_compatibility")
    if compat not in (COMPAT_FULL, COMPAT_PARTIAL):
        problems.append(f"compatibilità JSON-LD '{compat}': servono FULL o PARTIAL (esegui `python scraper.py --probe {entry['domain']}`)")
    pr = entry.get("probe") or {}
    age = days_since(pr.get("at"))
    max_age = float(settings(config)["probe_max_age_days"])
    if age is None or age > max_age:
        problems.append(f"ultimo probe assente o più vecchio di {max_age:.0f} giorni: rilancia il probe")
    if not problems:
        urls = list(pr.get("urls_fetched") or [])
        if pr.get("search_url_template"):
            tmpl = pr["search_url_template"]
            urls.append(tmpl.replace("{q}", "ceramol") if tmpl.startswith("http") else entry["base_url"].rstrip("/") + tmpl.replace("{q}", "ceramol"))
        for u in urls:
            if not session.is_allowed(u):
                problems.append(f"robots.txt oggi vieta un percorso usato dal probe: {u}")
    return problems


def promote(registry: dict, shipping_cfg: dict, domain: str, session, config: dict, *, input_fn=input,
            now: str | None = None) -> tuple[bool, str]:
    """Promozione MANUALE ad ACTIVE con scraper generico, dopo conferma esplicita a terminale."""
    now = now or now_utc_iso()
    entry = next((p for p in registry.get("pharmacies", []) if registrable(p.get("domain", "")) == registrable(domain)), None)
    if entry is None:
        return False, f"{domain} non è nel registro"
    problems = check_promotable(entry, session, config)
    if problems:
        return False, "promozione rifiutata: " + "; ".join(problems)
    pr = entry["probe"]
    print(f"\nDominio: {entry['domain']}  compatibilità: {entry['jsonld_compatibility']}  probe: {pr['at']}")
    for ex in pr.get("examples") or []:
        print(f"  esempio {ex['product_id']}: {ex['price']} € {ex['availability']} ({ex['match_level']}) {ex['url']}")
    answer = input_fn(f"Confermi la promozione ad ACTIVE (le sue offerte entreranno nel carrello)? "
                      f"Digita il dominio '{entry['domain']}' per confermare: ")
    if (answer or "").strip().lower() != entry["domain"].lower():
        return False, "promozione annullata: conferma non data"
    entry["status"] = "ACTIVE"
    entry["scraper"] = "generic_jsonld"
    entry["scraper_status"] = "OK"
    entry["promoted_at"] = now
    ex = (pr.get("examples") or [None])[0]
    entry["activation_evidence"] = ({"product_id": ex["product_id"], "url": ex["url"], "price": ex["price"],
                                     "match": [ex["match_level"]], "scraped_at": pr["at"], "source": "probe JSON-LD"}
                                    if ex else {"source": "probe JSON-LD", "price": None, "note": "compatibilità PARTIAL: nessun esempio con prezzo"})
    entry.setdefault("status_history", []).append(
        {"at": now, "status": "ACTIVE", "reason": f"promozione manuale (probe {pr['at']}, {entry['jsonld_compatibility']})"})
    ships = shipping_cfg.setdefault("pharmacies", {})
    if entry["id"] not in ships or ships[entry["id"]].get("standard_cost_eur") is None:
        hints = [o.get("shipping_jsonld") for o in pr.get("offers") or [] if o.get("shipping_jsonld")]
        ships[entry["id"]] = {
            "standard_cost_eur": None, "free_threshold_eur": None, "free_threshold_inclusive": None,
            "free_threshold_verified": False, "extra_costs": [], "cod_available": None, "cod_cost_eur": None,
            "pickup_available": None, "pickup_cost_eur": None, "pickup_locations": [], "source_url": None,
            "source_quote": None, "verified_at": None, "reliability": "NON_VERIFICATA", "auto_check": None,
            "jsonld_shipping_hint": hints[0] if hints else None,
            "notes": "Creato alla promozione: spedizione NON verificata (non è gratis). Verificala sul sito e compila questa voce.",
        }
    return True, f"{entry['domain']} promosso ad ACTIVE (scraper generic_jsonld)"


def demote(registry: dict, domain: str, *, now: str | None = None) -> tuple[bool, str]:
    now = now or now_utc_iso()
    entry = next((p for p in registry.get("pharmacies", []) if registrable(p.get("domain", "")) == registrable(domain)), None)
    if entry is None:
        return False, f"{domain} non è nel registro"
    if entry.get("status") != "ACTIVE":
        return False, f"{domain} non è ACTIVE (stato {entry.get('status')})"
    entry["status"] = "UNDER_REVIEW"
    entry.setdefault("status_history", []).append({"at": now, "status": "UNDER_REVIEW", "reason": "retrocessione manuale (demote)"})
    return True, f"{entry['domain']} riportato a UNDER_REVIEW"


def discover(registry: dict, session, products: list[dict], *, verify: bool = True, batch: int | None = None,
             recheck_blocked: bool = True, config: dict | None = None, probe_limit: int | None = None) -> dict:
    cfg = registry.setdefault("discovery", json.loads(json.dumps(DEFAULT_DISCOVERY)))
    excluded = [registrable(d) for d in registry.get("excluded_domains", [])]
    known = {registrable(p["domain"]): p for p in registry.get("pharmacies", [])}
    report = {"at": now_utc_iso(), "sources": {}, "added": [], "verified": [], "rechecked_blocked": []}
    src = cfg.get("sources", {})
    runs = [("seed_domains", lambda: source_seeds(cfg, session))]
    if src.get("puglia_open_data", {}).get("enabled"):
        runs.append(("puglia_open_data", lambda: source_puglia(src["puglia_open_data"], session)))
    if src.get("ministry_list", {}).get("enabled"):
        runs.append(("ministry_list", lambda: source_ministry(src["ministry_list"], session)))
    if src.get("comparators", {}).get("enabled"):
        runs.append(("comparators", lambda: source_comparators(src["comparators"], session, products)))
    for name, fn in runs:
        found, status = fn()
        report["sources"][name] = status
        for f in found:
            d = registrable(f["domain"])
            if not d or is_excluded(d, excluded) or d in known:
                continue
            entry = new_entry(d, f.get("name"), f["source"], f.get("extra"))
            ids = {p["id"] for p in registry["pharmacies"]}
            if entry["id"] in ids:
                entry["id"] = f"{entry['id']}_{len(ids)}"
            registry["pharmacies"].append(entry)
            known[d] = entry
            report["added"].append(d)
    if verify:
        size = batch or int(cfg.get("batch_size", 15))
        queue = [p for p in registry["pharmacies"] if p.get("status") == "DISCOVERED"]
        queue.sort(key=lambda p: (0 if "curato" in (p.get("source") or "") else 1, p.get("discovered_at") or ""))
        todo = queue[:size]
        with ThreadPoolExecutor(max_workers=min(8, len(todo) or 1)) as pool:
            audits = list(pool.map(lambda p: audit_site(session, p["base_url"], products), todo))
        for p, a in zip(todo, audits):
            apply_audit(p, a)
            report["verified"].append({"domain": p["domain"], "status": p["status"],
                                       "candidates": p["products_candidate_from_sitemap"]})
    if config is not None:
        from scrapers.generic_jsonld import settings as generic_settings
        limit = probe_limit if probe_limit is not None else int(generic_settings(config)["probe_per_discovery_run"])
        report["probed"] = probe_new_sites(registry, session, config, limit=limit, now=report["at"])
    if recheck_blocked:
        # Riprova gentile: UNA richiesta a robots.txt per i siti bloccati; mai oltre UNDER_REVIEW.
        for p in [p for p in registry["pharmacies"] if p.get("status") == "BLOCKED"]:
            try:
                from scrapers.http import PoliteSession  # noqa: F401
                pol = session.robots_policy(p["base_url"])
                if pol.mode != "disallow_all" and (pol.source_status or 0) < 400:
                    p["status"] = "UNDER_REVIEW"
                    p.setdefault("status_history", []).append(
                        {"at": report["at"], "status": "UNDER_REVIEW", "reason": "robots.txt di nuovo raggiungibile: da riverificare"})
                    report["rechecked_blocked"].append({"domain": p["domain"], "result": "sbloccato -> UNDER_REVIEW"})
                else:
                    report["rechecked_blocked"].append({"domain": p["domain"], "result": f"ancora bloccato ({pol.note})"})
            except Exception as exc:  # noqa: BLE001
                report["rechecked_blocked"].append({"domain": p["domain"], "result": f"ancora bloccato: {str(exc)[:120]}"})
    registry["last_discovery"] = report
    registry["updated_at"] = report["at"]
    return report


SHIPPING_PATH = ROOT / "shipping_config.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scoperta di nuovi rivenditori (mai attivati in automatico)")
    ap.add_argument("--no-verify", action="store_true", help="solo raccolta, senza verifica dei siti")
    ap.add_argument("--batch", type=int, help="quanti siti DISCOVERED verificare in questa esecuzione")
    ap.add_argument("--no-recheck-blocked", action="store_true")
    ap.add_argument("--no-probe", action="store_true", help="non lanciare il probe JSON-LD sui nuovi siti")
    ap.add_argument("--probe-limit", type=int, help="massimo domini da valutare con il probe (default config: 5)")
    ap.add_argument("--dry-run", action="store_true", help="non scrive pharmacies.json")
    sub = ap.add_subparsers(dest="cmd")
    p_prom = sub.add_parser("promote", help="promuove a mano un dominio ad ACTIVE (scraper generic_jsonld), con conferma")
    p_prom.add_argument("domain")
    p_dem = sub.add_parser("demote", help="riporta un dominio ACTIVE a UNDER_REVIEW")
    p_dem.add_argument("domain")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from scrapers.http import PoliteSession

    config = read_json(ROOT / "config.json", required=True)
    registry = read_json(REGISTRY_PATH, required=True)
    session = PoliteSession(config["http"])

    if args.cmd in ("promote", "demote"):
        if args.cmd == "promote":
            shipping_cfg = read_json(SHIPPING_PATH, default={"schema_version": 1, "pharmacies": {}})
            ok, msg = promote(registry, shipping_cfg, args.domain, session, config)
            if ok:
                write_json_atomic(SHIPPING_PATH, shipping_cfg)
        else:
            ok, msg = demote(registry, args.domain)
        print(msg)
        if ok:
            write_json_atomic(REGISTRY_PATH, registry)
            if args.cmd == "demote":
                print(recompute_data_after_status_change(registry, config, f"demote {args.domain}"))
            else:
                _refresh_candidates_in_data(registry)
        return 0 if ok else 1

    report = discover(registry, session, config["products"], verify=not args.no_verify, batch=args.batch,
                      recheck_blocked=not args.no_recheck_blocked, config=None if args.no_probe else config,
                      probe_limit=args.probe_limit)
    print(json.dumps({k: report.get(k) for k in ("sources", "added", "verified", "probed", "rechecked_blocked")},
                     ensure_ascii=False, indent=1)[:6000])
    if not args.dry_run:
        write_json_atomic(REGISTRY_PATH, registry)
        _refresh_candidates_in_data(registry)
        print(f"pharmacies.json aggiornato ({today_rome()}): {len(registry['pharmacies'])} farmacie nel registro")
    return 0


def recompute_data_after_status_change(registry: dict, config: dict, reason: str, *, path=None) -> str:
    """Dopo che una farmacia esce da ACTIVE: le sue offerte non sono più acquistabili.

    data.json viene ricalcolato subito (migliori prezzi, carrelli, statistiche) e le sue offerte
    passano tra le candidate NON_VERIFICATO; lo storico resta intatto.
    """
    from physical_pharmacies import load_physical
    from snapshot import recompute

    path = path or ROOT / "data.json"
    data = read_json(path, default=None)
    if not data or data.get("offers") is None:
        return "data.json assente: niente da ricalcolare"
    shipping_cfg = read_json(SHIPPING_PATH, default={"pharmacies": {}})
    summary = recompute(data, config=config, registry=registry, shipping_cfg=shipping_cfg,
                        physical=load_physical(config), now=now_utc_iso(), reason=reason)
    write_json_atomic(path, data)
    return (f"data.json ricalcolato: {summary['offers_kept']} offerte valide restano, "
            f"{summary['offers_moved_to_candidates']} passate tra le candidate non verificate")


def _refresh_candidates_in_data(registry: dict) -> None:
    """Riallinea data.json -> candidate_offers al registro (le offerte vere restano invariate)."""
    from scrapers.generic_jsonld import candidate_offers_from_registry
    path = ROOT / "data.json"
    data = read_json(path, default=None)
    if not data or data.get("status") == "MAI_ESEGUITO":
        return
    data["candidate_offers"] = candidate_offers_from_registry(registry)
    data.setdefault("stats", {})["candidate_offers"] = len(data["candidate_offers"])
    write_json_atomic(path, data)


if __name__ == "__main__":
    sys.exit(main())
