"""Orchestratore giornaliero: scraping delle farmacie ACTIVE -> ottimizzazione -> data.json + storico.

Esempi:
  python scraper.py                              # tutte le farmacie ACTIVE
  python scraper.py --pharmacy clubfarma --dry-run
  python scraper.py --pharmacy farmasave --activate   # attiva SOLO se estrae un'offerta autentica (match per ID)
  python scraper.py --refresh-candidates         # rilegge sitemap/ricerche
  python scraper.py --pharmacy clubfarma          # scansione PARZIALE: le altre farmacie restano invariate

Pubblicazione (mai a metà): data.json, storico, registro e cache URL vengono preparati in
memoria, validati (validation.py) e poi pubblicati insieme con lo stesso `run_id`.

Codici di uscita: 0 = almeno un'offerta valida; 2 = nessuna offerta valida (dati precedenti conservati);
3 = validazione o scrittura fallita (dati precedenti conservati).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from optimizer import optimize
from physical_pharmacies import load_physical
from scrapers import REGISTRY, UrlCache
from scrapers.generic_jsonld import (CANDIDATE_RELIABILITY, GenericJsonLdScraper, apply_probe,
                                     candidate_offers_from_registry, probe, write_probe_markdown)
from scrapers.base import (STATUS_BLOCKED, STATUS_ERROR, STATUS_MATCH_UNRELIABLE, STATUS_OK,
                           STATUS_PRICE_UNVERIFIED, STATUS_RATE_LIMITED, STATUS_ROBOTS, Offer, resolve_shared_urls)
from scrapers.http import PoliteSession
from shipping import summary as shipping_summary
from snapshot import best_prices, carried_offers, offer_stats, product_view, refresh_single_item_shipping
from storage import (ROME, ROOT, StorageError, history_entry, now_utc_iso, prepare_history, read_json, today_rome,
                     write_json_atomic, write_many_atomic)
from validation import history_problems, valid_price, validate_data

log = logging.getLogger("tracker")
PATHS = {
    "config": ROOT / "config.json",
    "pharmacies": ROOT / "pharmacies.json",
    "shipping": ROOT / "shipping_config.json",
    "urls": ROOT / "product_urls.json",
    "data": ROOT / "data.json",
    "history": ROOT / "price_history.json",
}
MAX_CONSECUTIVE_FAILURES = 3


def setup_logging(verbose: bool = False) -> Path:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    path = logs / f"scrape_{datetime.now(ROME):%Y-%m-%d}.log"
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return path


def select_pharmacies(registry: dict, only: list[str] | None) -> list[dict]:
    phs = registry.get("pharmacies", [])
    if only:
        chosen = [p for p in phs if p["id"] in only]
        missing = set(only) - {p["id"] for p in chosen}
        if missing:
            raise SystemExit(f"Farmacie sconosciute: {sorted(missing)}")
        return chosen
    return [p for p in phs if p.get("status") == "ACTIVE"]


def make_scraper(entry: dict, session: PoliteSession, config: dict, cache: UrlCache, now: str):
    """Dispatcher: scraper dedicato se esiste, altrimenti generico JSON-LD se il registro lo indica."""
    cls = REGISTRY.get(entry["id"])
    if cls is not None:
        return cls(session, config, cache, now_iso=now)
    if entry.get("scraper") == "generic_jsonld":
        return GenericJsonLdScraper(entry, session, config, cache, now_iso=now)
    return None


def run_pharmacy(entry: dict, session: PoliteSession, config: dict, cache: UrlCache, now: str,
                 products: list[dict], refresh: bool) -> tuple[str, list[Offer], dict]:
    started = time.monotonic()
    scraper = make_scraper(entry, session, config, cache, now)
    if scraper is None:
        offers = [Offer(p["id"], entry["id"], STATUS_ERROR, scraped_at=now, notes=["nessuno scraper registrato"])
                  for p in products]
        return entry["id"], offers, {"duration_s": 0, "error": "scraper mancante"}
    try:
        offers = scraper.run(products, refresh=refresh,
                             refresh_days=float(config.get("scraping", {}).get("candidate_refresh_days", 7)))
    except Exception as exc:  # noqa: BLE001 - una farmacia che fallisce non ferma le altre
        log.exception("[%s] errore inatteso", entry["id"])
        offers = [Offer(p["id"], entry["id"], STATUS_ERROR, scraped_at=now, notes=[f"errore inatteso: {exc}"])
                  for p in products]
    info = {"duration_s": round(time.monotonic() - started, 1), "fatal": scraper.fatal,
            "requests": session.domain_status(scraper.base_url or entry.get("base_url", ""))["requests"]}
    return entry["id"], offers, info


def update_registry(registry: dict, results: dict[str, tuple[list[Offer], dict]], now: str, *, activate: bool) -> list[str]:
    """Aggiorna lo stato scraper nel registro. Ritorna le farmacie attivate in questa esecuzione."""
    activated = []
    by_id = {p["id"]: p for p in registry.get("pharmacies", [])}
    for pid, (offers, info) in results.items():
        p = by_id[pid]
        counts = Counter(o.status for o in offers)
        ok = [o for o in offers if o.status == STATUS_OK]
        p["last_run"] = now
        p["last_run_summary"] = dict(counts)
        p["products_found"] = sorted({o.product_id for o in offers if o.status in (STATUS_OK, STATUS_PRICE_UNVERIFIED)})
        p["products_with_verified_price"] = sorted({o.product_id for o in ok})
        p["errors"] = sorted({n for o in offers if o.status in (STATUS_ERROR, STATUS_BLOCKED, STATUS_RATE_LIMITED, STATUS_ROBOTS)
                              for n in o.notes if n})[:10]
        if counts.get(STATUS_BLOCKED):
            p["scraper_status"] = "BLOCKED"
        elif counts.get(STATUS_RATE_LIMITED):
            p["scraper_status"] = "RATE_LIMITED"
        elif ok:
            p["scraper_status"] = "OK"
        elif counts.get(STATUS_PRICE_UNVERIFIED) or counts.get(STATUS_MATCH_UNRELIABLE):
            p["scraper_status"] = "NOT_VERIFIED"
        else:
            p["scraper_status"] = "ERROR"
        if ok:
            p["consecutive_failures"] = 0
            p["last_success"] = now
        else:
            p["consecutive_failures"] = int(p.get("consecutive_failures") or 0) + 1
        if p["scraper_status"] == "BLOCKED" and p.get("status") in ("ACTIVE", "VERIFIED", "UNDER_REVIEW"):
            p["status"] = "BLOCKED"
            p.setdefault("status_history", []).append({"at": now, "status": "BLOCKED", "reason": "; ".join(p["errors"][:2])})
        elif p.get("status") == "ACTIVE" and p["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
            p["status"] = "INACTIVE"
            p.setdefault("status_history", []).append(
                {"at": now, "status": "INACTIVE", "reason": f"{p['consecutive_failures']} esecuzioni consecutive senza offerte valide"})
        # --activate vale solo per gli scraper DEDICATI: i siti generici si promuovono a mano
        # con `pharmacy_discovery.py promote`, dopo un probe valido e una conferma esplicita.
        if activate and p.get("status") in ("VERIFIED", "UNDER_REVIEW") and pid in REGISTRY:
            evidence = [o for o in ok if o.match_level == "ID"]
            if evidence:
                e = evidence[0]
                p["status"] = "ACTIVE"
                p["activated_at"] = now
                p["activation_evidence"] = {"product_id": e.product_id, "url": e.url, "price": e.price,
                                            "match": e.match_reasons[:1], "scraped_at": e.scraped_at}
                p.setdefault("status_history", []).append({"at": now, "status": "ACTIVE", "reason": "attivazione manuale (--activate) con offerta autentica"})
                activated.append(pid)
            else:
                p.setdefault("status_history", []).append(
                    {"at": now, "status": p.get("status"), "reason": "--activate rifiutato: nessuna offerta autentica con match per ID"})
    registry["updated_at"] = now
    return activated


def market_summary(registry: dict, since_days: int = 7) -> dict:
    from storage import days_since

    phs = registry.get("pharmacies", [])
    st = Counter(p.get("status") for p in phs)
    return {
        "total": len(phs),
        "discovered": sum(1 for p in phs if p.get("status") == "DISCOVERED"),
        "under_review": st.get("UNDER_REVIEW", 0),
        "verified": sum(1 for p in phs if p.get("status") in ("VERIFIED", "ACTIVE")),
        "active": st.get("ACTIVE", 0),
        "blocked": st.get("BLOCKED", 0),
        "inactive": st.get("INACTIVE", 0),
        "unreachable": sum(1 for p in phs if p.get("scraper_status") in ("BLOCKED", "RATE_LIMITED", "ERROR")),
        "new_last_7_days": sum(1 for p in phs if (days_since(p.get("discovered_at")) or 999) <= since_days),
    }


def build_data(config: dict, registry: dict, shipping_cfg: dict, offers: list[Offer], results: dict,
               physical: dict | None, now: str, started: float, dry_run: bool, *,
               carried: list[dict] | None = None, previous: dict | None = None, partial: bool = False) -> dict:
    """Snapshot di questa esecuzione.

    Con una scansione parziale (`partial`, es. --pharmacy) `carried` contiene le offerte dello
    snapshot precedente delle farmacie ACTIVE non scansionate: entrano in carrelli e migliori
    prezzi (con la loro data di rilevazione) ma MAI nello storico di oggi.
    """
    products = config["products"]
    fmt = {p["id"]: p["format"]["label"] for p in products}
    names = {p["id"]: p.get("name", p["id"]) for p in registry.get("pharmacies", [])}
    status_of = {p["id"]: p.get("status") for p in registry.get("pharmacies", [])}
    resolve_shared_urls(offers)  # rete di sicurezza: una pagina = un solo prodotto
    # Solo le farmacie ACTIVE alimentano carrello, migliori prezzi e storico. Le offerte di siti
    # DISCOVERED/UNDER_REVIEW/VERIFIED finiscono in candidate_offers, marcate NON_VERIFICATO.
    run_candidates = []
    for o in [o for o in offers if status_of.get(o.pharmacy_id) != "ACTIVE"]:
        if o.status == STATUS_OK and o.price is not None:
            d = o.to_dict()
            d.update({"format": fmt.get(o.product_id), "pharmacy_name": names.get(o.pharmacy_id, o.pharmacy_id),
                      "pharmacy_status": status_of.get(o.pharmacy_id), "reliability": CANDIDATE_RELIABILITY,
                      "usable": False, "availability_uncertain": False, "source": "esecuzione"})
            run_candidates.append(d)
    offers = [o for o in offers if status_of.get(o.pharmacy_id) == "ACTIVE"]
    seen = {(c["pharmacy_id"], c["product_id"]) for c in run_candidates}
    candidate_offers = run_candidates + [c for c in candidate_offers_from_registry(registry)
                                         if (c["pharmacy_id"], c["product_id"]) not in seen]
    run_dicts = []
    for o in offers:
        d = o.to_dict()
        d["format"] = fmt.get(o.product_id)  # formato cercato in QUESTA esecuzione
        d["pharmacy_name"] = names.get(o.pharmacy_id, o.pharmacy_id)
        run_dicts.append(d)
    offer_dicts = [c for c in carried or [] if status_of.get(c.get("pharmacy_id")) == "ACTIVE"] + run_dicts
    refresh_single_item_shipping(offer_dicts, shipping_cfg)
    carts = optimize(products, offer_dicts, shipping_cfg, physical, names)
    errors = [{"pharmacy_id": o.pharmacy_id, "product_id": o.product_id, "status": o.status,
               "message": "; ".join(n for n in o.notes if n)[:300]}
              for o in offers if o.status not in (STATUS_OK,)]
    usable_run = sum(1 for o in offers if o.usable)
    active_results = {pid: r for pid, r in results.items() if status_of.get(pid) == "ACTIVE"}
    attempted = len(active_results)
    ok_pharmacies = sum(1 for _, (offs, _) in active_results.items() if any(o.status == STATUS_OK for o in offs))
    status = "OK" if usable_run and ok_pharmacies == attempted else ("PARZIALE" if usable_run else "ERRORE")
    carried_ids = sorted({c["pharmacy_id"] for c in offer_dicts if c.get("carried_from")})
    prev_scope = (previous or {}).get("scope") or {}
    scope = {
        "type": "parziale" if partial else "completa",
        "pharmacies_scanned": sorted(active_results),
        "pharmacies_carried": carried_ids,
        "carried_from": (previous or {}).get("generated_at") if partial else None,
        "last_full_run_at": (prev_scope.get("last_full_run_at") or (previous or {}).get("generated_at")) if partial else now,
    }
    ph_view = {}
    for p in registry.get("pharmacies", []):
        info = results.get(p["id"], (None, {}))[1]
        ph_view[p["id"]] = {k: p.get(k) for k in ("name", "domain", "base_url", "status", "scraper_status", "platform",
                                                   "collection_method", "last_run", "last_success", "products_found",
                                                   "products_with_verified_price", "errors", "notes", "last_verified")}
        ph_view[p["id"]]["run"] = info or None
    return {
        "schema_version": 2,
        "generated_at": now,
        "generated_at_rome": datetime.now(ROME).strftime("%d/%m/%Y %H:%M"),
        "status": status,
        "scope": scope,
        "disclaimer": config["app"]["disclaimer"],
        "run": {"duration_s": round(time.monotonic() - started, 1), "pharmacies_attempted": attempted,
                "pharmacies_with_offers": ok_pharmacies, "dry_run": dry_run, "offers_valid_this_run": usable_run},
        "products": product_view(products),
        "offers": offer_dicts,
        "candidate_offers": candidate_offers,
        "best_prices": best_prices(offer_dicts, products),
        "shipping": shipping_summary(shipping_cfg),
        "pharmacies": ph_view,
        "market": market_summary(registry),
        "carts": carts,
        "physical": physical,
        "stats": offer_stats(offer_dicts, candidate_offers, shipping_cfg, set(results) | set(carried_ids)),
        "errors": errors,
        "last_attempt": {"at": now, "status": status},
    }


def history_entries(offer_dicts: list[dict], today: str) -> list[dict]:
    """Voci di storico di OGGI: solo prezzi verificati in EUR rilevati in questa esecuzione.

    Le offerte riportate da uno snapshot precedente (scansione parziale) non entrano mai: il loro
    prezzo non è stato rilevato oggi. Si registrano anche i prezzi con disponibilità da
    verificare (il prezzo è verificato; la disponibilità resta annotata nella voce).
    """
    out = []
    for d in offer_dicts:
        if d.get("carried_from") or not (d.get("usable") or d.get("availability_uncertain")):
            continue
        if not valid_price(d.get("price")) or str(d.get("currency") or "EUR").upper() != "EUR":
            continue
        out.append(history_entry(today, d["product_id"], d["pharmacy_id"], d["price"], d.get("single_item_shipping"),
                                 d.get("single_item_total"), d.get("availability"), d.get("match_level"),
                                 d.get("format")))
    return out


def find_entry(registry: dict, domain: str) -> dict | None:
    from pharmacy_discovery import registrable
    d = registrable(domain)
    return next((p for p in registry.get("pharmacies", []) if registrable(p.get("domain", "")) == d or p["id"] == domain), None)


def refresh_candidate_offers(registry: dict) -> None:
    """Aggiorna solo la sezione candidate_offers di data.json (senza toccare offerte/carrelli/storico)."""
    data = read_json(PATHS["data"], default=None)
    if not data or data.get("status") == "MAI_ESEGUITO":
        return
    data["candidate_offers"] = candidate_offers_from_registry(registry)
    data.setdefault("stats", {})["candidate_offers"] = len(data["candidate_offers"])
    write_json_atomic(PATHS["data"], data)


def run_probes(entries: list[dict], registry: dict, session: PoliteSession, config: dict, now: str) -> list[dict]:
    """Probe JSON-LD su più domini (in parallelo tra domini, una richiesta alla volta per dominio)."""
    products = config["products"]
    docs = ROOT / "docs" / "probe"
    reports = []
    with ThreadPoolExecutor(max_workers=min(6, len(entries) or 1)) as pool:
        futures = {pool.submit(probe, e, session, config, products, now): e for e in entries}
        for fut in as_completed(futures):
            entry = futures[fut]
            try:
                report, _ = fut.result()
            except Exception as exc:  # noqa: BLE001 - un dominio che fallisce non ferma gli altri
                log.exception("[probe %s] errore inatteso", entry["domain"])
                entry.setdefault("errors", []).append(f"probe fallito: {exc}")
                continue
            apply_probe(entry, report)
            path = write_probe_markdown(report, products, docs)
            reports.append(report)
            log.info("[probe %s] %s: %s | trovati %s | richieste %d | %s", report["domain"], report["compatibility"],
                     report["reason"], report["products_found"] or "nessuno", report["requests"], path.name)
            for ex in report["examples"]:
                log.info("   esempio %s %s %.2f € %s %s", ex["product_id"], ex["match_level"], ex["price"],
                         ex["availability"], ex["url"])
    registry["updated_at"] = now
    return reports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scraping prezzi dermocosmetici (farmacie ACTIVE)")
    ap.add_argument("--pharmacy", action="append",
                    help="limita a una o più farmacie (anche non ACTIVE). Senza --dry-run è una scansione PARZIALE: "
                         "le offerte delle altre farmacie ACTIVE restano quelle della scansione precedente")
    ap.add_argument("--product", action="append",
                    help="limita a uno o più prodotti (es. P003). Solo con --dry-run: data.json resta completo")
    ap.add_argument("--dry-run", action="store_true", help="non scrive data.json né price_history.json")
    ap.add_argument("--refresh-candidates", action="store_true", help="rilegge sitemap/ricerche")
    ap.add_argument("--activate", action="store_true",
                    help="porta ad ACTIVE le farmacie indicate SOLO se estraggono un'offerta autentica (match per ID)")
    ap.add_argument("--probe", action="append", metavar="DOMINIO",
                    help="valuta un dominio del registro con lo scraper generico JSON-LD (report in docs/probe/)")
    ap.add_argument("--probe-all", action="store_true", help="probe di tutti i siti DISCOVERED/UNDER_REVIEW senza scraper dedicato")
    ap.add_argument("--probe-limit", type=int, help="con --probe-all: numero massimo di domini")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    log_path = setup_logging(args.verbose)
    started = time.monotonic()
    now = now_utc_iso()
    run_id = "run-" + now.replace("-", "").replace(":", "")  # identificativo comune dei file scritti

    config = read_json(PATHS["config"], required=True)
    registry = read_json(PATHS["pharmacies"], required=True)
    shipping_cfg = read_json(PATHS["shipping"], default={"pharmacies": {}})
    cache = UrlCache(read_json(PATHS["urls"], default=None))
    session = PoliteSession(config["http"])
    products = config["products"]
    if args.probe or args.probe_all:
        if args.probe_all:
            entries = [p for p in registry.get("pharmacies", [])
                       if p.get("status") in ("DISCOVERED", "UNDER_REVIEW") and p["id"] not in REGISTRY]
            entries = entries[: args.probe_limit] if args.probe_limit else entries
        else:
            entries = []
            for d in args.probe:
                e = find_entry(registry, d)
                if e is None:
                    ap.error(f"dominio non presente in pharmacies.json: {d} (aggiungilo con la scoperta o a mano)")
                entries.append(e)
        log.info("Probe JSON-LD su %d domini (mai promossi in automatico)", len(entries))
        run_probes(entries, registry, session, config, now)
        write_json_atomic(PATHS["pharmacies"], registry)
        refresh_candidate_offers(registry)
        return 0
    if args.product:
        if not args.dry_run:
            ap.error("--product richiede --dry-run (un data.json con un solo prodotto sarebbe incompleto)")
        unknown = set(args.product) - {p["id"] for p in products}
        if unknown:
            ap.error(f"prodotti sconosciuti: {sorted(unknown)}")
        products = [p for p in products if p["id"] in args.product]
        config = {**config, "products": products}
    targets = select_pharmacies(registry, args.pharmacy)
    if not targets:
        log.error("Nessuna farmacia ACTIVE: niente da fare. Attivale con --pharmacy <id> --activate.")
        return 2
    log.info("Avvio scraping: %d farmacie, %d prodotti (log: %s)", len(targets), len(products), log_path)

    results: dict[str, tuple[list[Offer], dict]] = {}
    with ThreadPoolExecutor(max_workers=min(12, len(targets))) as pool:
        futures = [pool.submit(run_pharmacy, t, session, config, cache, now, products, args.refresh_candidates)
                   for t in targets]
        for fut in as_completed(futures):
            pid, offers, info = fut.result()
            results[pid] = (offers, info)
            c = Counter(o.status for o in offers)
            log.info("[%s] completata in %ss: %s", pid, info.get("duration_s"), dict(c))

    offers = [o for pid in sorted(results) for o in results[pid][0]]
    # Con --product la corsa copre solo alcuni prodotti: i riepiloghi per farmacia sarebbero falsati.
    activated = [] if args.product else update_registry(registry, results, now, activate=args.activate)
    if activated:
        log.info("Farmacie attivate: %s", activated)
    physical = load_physical(config)
    active_ids = {p["id"] for p in registry.get("pharmacies", []) if p.get("status") == "ACTIVE"}
    # Scansione parziale: alcune farmacie ACTIVE non sono state lette. Le loro offerte precedenti
    # vengono conservate (merge), mai cancellate; lo storico riceve solo i prezzi letti oggi.
    partial = bool(args.pharmacy) and not active_ids <= set(results)
    previous, carried = None, []
    if partial and not args.dry_run:
        previous = read_json(PATHS["data"], default=None) or {}
        carried = carried_offers(previous, active_ids - set(results), config["products"])
        log.info("Scansione parziale: conservo %d offerte precedenti di %d farmacie non scansionate",
                 len(carried), len({c["pharmacy_id"] for c in carried}))
    data = build_data(config, registry, shipping_cfg, offers, results, physical, now, started, args.dry_run,
                      carried=carried, previous=previous, partial=partial)
    data["run_id"] = run_id

    for o in offers:
        if o.status == STATUS_OK:
            log.info("OFFERTA %s %-18s %7.2f € %-15s %s %s", o.product_id, o.pharmacy_id, o.price, o.availability,
                     o.match_level, o.url)
        else:
            log.info("------- %s %-18s %s: %s", o.product_id, o.pharmacy_id, o.status, "; ".join(o.notes)[:160])
    carts = data["carts"] or {}
    best = carts.get("best_overall")
    if best:
        partial = "" if carts.get("complete_possible") else f" [PARZIALE: mancano {', '.join(carts['products_without_offers'])}]"
        log.info("MIGLIOR CARRELLO: %.2f € (%s) da %s%s", best["total"], best["mode_label"], ", ".join(best["pharmacies"]), partial)
    for note in carts.get("notes", []):
        log.info("Nota ottimizzatore: %s", note)

    # Registro e cache URL sono metadati (stato dei siti), non prezzi: si salvano sempre,
    # anche in --dry-run, nello stesso passaggio di pubblicazione dei dati.
    registry["updated_by_run"] = run_id
    cache.data["updated_by_run"] = run_id
    metadata: dict[Path, dict] = {PATHS["urls"]: cache.data}
    if not args.product:
        metadata[PATHS["pharmacies"]] = registry
    valid_now = data["run"]["offers_valid_this_run"]
    try:
        if args.dry_run:
            written = write_many_atomic(metadata)
            log.info("--dry-run: data.json e price_history.json NON scritti (offerte valide: %d). Scritti: %s",
                     valid_now, _names(written))
            return 0 if valid_now else 2
        if valid_now == 0:
            metadata[PATHS["data"]] = _keep_previous(data, now, "Nessuna offerta valida in questa esecuzione: "
                                                     "dati precedenti conservati.", data["errors"][:50])
            written = write_many_atomic(metadata)
            log.error("Nessuna offerta valida: storico NON aggiornato, dati precedenti conservati. Scritti: %s",
                      _names(written))
            return 2
        entries = history_entries(data["offers"], today_rome())
        old_hist, new_hist = prepare_history(PATHS["history"], entries, products=config["products"])
        problems = validate_data(data, active_ids=active_ids)
        if new_hist is not None:
            problems += history_problems(old_hist, new_hist)
        if problems:
            for p in problems[:20]:
                log.error("Validazione: %s", p)
            metadata[PATHS["data"]] = _keep_previous(data, now, "Validazione fallita: dati precedenti conservati.",
                                                     [{"message": p} for p in problems[:20]])
            written = write_many_atomic(metadata)
            log.error("Dati NON pubblicati (validazione fallita). Scritti solo: %s", _names(written))
            return 3
        files: dict[Path, dict] = {}
        if new_hist is not None:
            files[PATHS["history"]] = new_hist
        files[PATHS["data"]] = data
        files.update(metadata)
        written = write_many_atomic(files)
        scope = data["scope"]
        log.info("Esecuzione %s pubblicata: %s. data.json %s, scansione %s%s. Storico: %s.", run_id, _names(written),
                 data["status"], scope["type"],
                 f" (conservate le offerte di {', '.join(scope['pharmacies_carried'])})" if scope["pharmacies_carried"] else "",
                 f"{len(new_hist['entries'])} voci (+{len(entries)} di oggi)" if new_hist else "invariato")
    except (StorageError, ValueError, TypeError) as exc:
        log.error("Errore di scrittura: %s", exc)
        return 3
    return 0


def _names(paths) -> str:
    return ", ".join(Path(p).name for p in paths) or "nessun file"


def _keep_previous(data: dict, now: str, message: str, errors: list) -> dict:
    """data.json precedente con l'esito dell'ultimo tentativo; se non esiste, il nuovo snapshot."""
    previous = read_json(PATHS["data"], default={}) or {}
    if not previous.get("offers"):
        return data
    previous["last_attempt"] = {"at": now, "status": "ERRORE", "message": message, "errors": errors}
    return previous


if __name__ == "__main__":
    sys.exit(main())
