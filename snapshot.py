"""Costruzione e ricalcolo coerente di data.json (lo "snapshot" pubblicato).

Un unico punto calcola migliori prezzi, carrelli e statistiche a partire dalle offerte, così
scraper.py, optimizer.py, maintenance.py e pharmacy_discovery.py producono lo stesso risultato.

`recompute()` serve quando cambia qualcosa che invalida lo snapshot SENZA un nuovo scraping:
- una farmacia esce da ACTIVE (demote, blocco, inattività): le sue offerte non sono più
  acquistabili e passano tra le offerte candidate (NON_VERIFICATO);
- cambia il formato di un prodotto: le offerte del vecchio formato escono dal calcolo;
- cambiano spedizioni o parametri di viaggio.
Il ricalcolo avviene anche quando non resta nessuna offerta (carrello vuoto ma coerente).
"""
from __future__ import annotations

from collections import Counter

from validation import valid_price

PRODUCT_FIELDS = ("id", "brand", "name", "format", "ean", "minsan", "format_history")
BEST_PRICE_FIELDS = ("pharmacy_id", "pharmacy_name", "price", "url", "availability", "match_level",
                     "single_item_shipping", "single_item_total", "scraped_at")
MAX_RECOMPUTATIONS_LOGGED = 20


def normalize_offer_flags(d: dict) -> dict:
    """Ricalcola `usable`/`availability_uncertain` di un'offerta già serializzata con le regole attuali
    (snapshot scritti prima della regola "solo disponibilità confermata" o della verifica della valuta)."""
    priced = (d.get("status") == "OK" and bool(d.get("verified")) and valid_price(d.get("price"))
              and str(d.get("currency") or "EUR").upper() == "EUR")
    d["usable"] = priced and d.get("availability") == "DISPONIBILE"
    d["availability_uncertain"] = priced and d.get("availability") == "SCONOSCIUTA"
    return d


def carried_offers(previous: dict | None, pharmacy_ids: set[str], products: list[dict]) -> list[dict]:
    """Offerte dello snapshot precedente delle farmacie NON scansionate in una scansione parziale."""
    from optimizer import offer_matches_current_format

    out = []
    for o in (previous or {}).get("offers") or []:
        if o.get("pharmacy_id") in pharmacy_ids and offer_matches_current_format(o, products):
            d = normalize_offer_flags(dict(o))
            d["carried_from"] = d.get("carried_from") or (previous or {}).get("generated_at")
            out.append(d)
    return out


def product_view(products: list[dict]) -> list[dict]:
    return [{k: p.get(k) for k in PRODUCT_FIELDS} for p in products]


def best_prices(offer_dicts: list[dict], products: list[dict]) -> dict:
    """Miglior prezzo per prodotto tra le sole offerte utilizzabili (disponibilità confermata)."""
    out = {}
    for p in products:
        cands = [d for d in offer_dicts if d.get("product_id") == p["id"] and d.get("usable")]
        out[p["id"]] = {k: min(cands, key=lambda d: d["price"]).get(k) for k in BEST_PRICE_FIELDS} if cands else None
    return out


def refresh_single_item_shipping(offer_dicts: list[dict], shipping_cfg: dict) -> None:
    """Spedizione per l'acquisto del singolo prodotto, con la configurazione ATTUALE."""
    from shipping import quote

    for d in offer_dicts:
        if not (d.get("usable") or d.get("availability_uncertain")) or not valid_price(d.get("price")):
            continue
        q = quote(shipping_cfg, d["pharmacy_id"], d["price"])
        d["single_item_shipping"] = q.cost
        d["single_item_total"] = None if q.cost is None else round(d["price"] + q.cost, 2)
        d["single_item_shipping_status"] = q.status


def offer_stats(offer_dicts: list[dict], candidate_offers: list[dict], shipping_cfg: dict,
                pharmacy_ids) -> dict:
    from scrapers.base import (STATUS_BLOCKED, STATUS_ERROR, STATUS_MATCH_UNRELIABLE, STATUS_NOT_FOUND, STATUS_OK,
                               STATUS_PRICE_UNVERIFIED, STATUS_RATE_LIMITED, STATUS_ROBOTS)
    from shipping import unverified

    counts = Counter(o.get("status") for o in offer_dicts)
    return {
        "offers_total": len(offer_dicts),
        "offers_valid": sum(1 for o in offer_dicts if o.get("usable")),
        "offers_uncertain": sum(1 for o in offer_dicts if o.get("availability_uncertain")),
        "ok": counts.get(STATUS_OK, 0),
        "not_found": counts.get(STATUS_NOT_FOUND, 0),
        "price_unverified": counts.get(STATUS_PRICE_UNVERIFIED, 0),
        "match_unreliable": counts.get(STATUS_MATCH_UNRELIABLE, 0),
        "blocked": counts.get(STATUS_BLOCKED, 0),
        "rate_limited": counts.get(STATUS_RATE_LIMITED, 0),
        "robots": counts.get(STATUS_ROBOTS, 0),
        "errors": counts.get(STATUS_ERROR, 0),
        "candidate_offers": len(candidate_offers),
        "shipping_unverified": unverified(shipping_cfg, set(pharmacy_ids)),
    }


def as_candidate(offer: dict, status: str | None, name: str | None) -> dict:
    """Offerta di una farmacia non (più) ACTIVE: solo segnalazione, mai nel carrello o nello storico."""
    from scrapers.generic_jsonld import CANDIDATE_RELIABILITY

    d = dict(offer)
    d.update({"usable": False, "availability_uncertain": False, "reliability": CANDIDATE_RELIABILITY,
              "pharmacy_status": status, "pharmacy_name": d.get("pharmacy_name") or name or d.get("pharmacy_id"),
              "source": d.get("source") or "snapshot precedente: farmacia non più ACTIVE"})
    return d


def recompute(data: dict, *, config: dict, registry: dict | None, shipping_cfg: dict, physical: dict | None,
              now: str, reason: str) -> dict:
    """Ricalcola sul posto offerte valide, candidate, migliori prezzi, carrelli e statistiche.

    Con `registry` assente non filtra per stato (es. test o registro non disponibile).
    Restituisce un riepilogo di cosa è cambiato.
    """
    from optimizer import offer_matches_current_format, optimize
    from shipping import summary as shipping_summary

    products = config["products"]
    phs = (registry or {}).get("pharmacies")
    status_of = {p["id"]: p.get("status") for p in phs} if phs else None
    names = {pid: (v or {}).get("name") or pid for pid, v in (data.get("pharmacies") or {}).items()}
    names.update({p["id"]: p.get("name") or p["id"] for p in phs or []})

    kept, moved, old_format = [], [], 0
    for o in data.get("offers") or []:
        normalize_offer_flags(o)
        if not offer_matches_current_format(o, products):
            old_format += 1
            continue
        if status_of is not None and status_of.get(o.get("pharmacy_id")) != "ACTIVE":
            if o.get("status") == "OK" and valid_price(o.get("price")):
                moved.append(as_candidate(o, status_of.get(o.get("pharmacy_id")), names.get(o.get("pharmacy_id"))))
            continue
        kept.append(o)
    refresh_single_item_shipping(kept, shipping_cfg)

    candidates: dict[tuple, dict] = {}
    for c in list(data.get("candidate_offers") or []) + moved:
        if status_of is not None and status_of.get(c.get("pharmacy_id")) == "ACTIVE":
            continue  # promossa: le sue offerte arriveranno verificate dal prossimo scraping
        if not offer_matches_current_format(c, products):
            continue
        candidates[(c.get("pharmacy_id"), c.get("product_id"))] = c

    data["offers"] = kept
    data["candidate_offers"] = list(candidates.values())
    data["products"] = product_view(products)
    data["best_prices"] = best_prices(kept, products)
    data["shipping"] = shipping_summary(shipping_cfg)
    data["physical"] = physical
    data["carts"] = optimize(products, kept, shipping_cfg, physical, names)
    if status_of is not None:
        for pid, view in (data.get("pharmacies") or {}).items():
            if isinstance(view, dict) and pid in status_of:
                view["status"] = status_of[pid]
    data.setdefault("stats", {}).update(offer_stats(kept, data["candidate_offers"], shipping_cfg,
                                                    {o["pharmacy_id"] for o in kept}))
    data["errors"] = [e for e in data.get("errors") or []
                      if status_of is None or status_of.get(e.get("pharmacy_id")) == "ACTIVE"]
    summary = {"at": now, "reason": reason, "offers_kept": len(kept), "offers_moved_to_candidates": len(moved),
               "offers_removed_old_format": old_format}
    data["recomputations"] = (list(data.get("recomputations") or []) + [summary])[-MAX_RECOMPUTATIONS_LOGGED:]
    return summary
