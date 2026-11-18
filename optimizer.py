"""Ottimizzatore esaustivo del carrello: 5 prodotti, ciascuno comprato una sola volta.

Canali di acquisto per ogni prodotto:
- "domicilio":    ordine online consegnato a casa      -> prezzo + spedizione dell'ordine
- "ritiro":       ordine online con ritiro in farmacia -> prezzo + costo ritiro + viaggio
- "negozio":      acquisto in farmacia fisica          -> prezzo rilevato in negozio + viaggio

Costo totale = prodotti + spedizioni (una per ordine, con soglie verificate) + costi di ritiro
+ viaggi (UNO per luogo visitato, anche se lì si ritirano più prodotti).
Componenti sconosciute => carrello "incompleto": mai vincitore certo, elencato a parte.
Il carrello verificato usa solo offerte con disponibilità CONFERMATA; le offerte con prezzo
verificato ma disponibilità sconosciuta formano a parte le "alternative potenziali".

Modalità:
  A = tutto online a domicilio da UNA farmacia
  E = tutto online a domicilio da PIÙ farmacie (multi-farmacia)
  B = tutto online con ritiro in farmacia
  C = tutto in farmacia fisica (acquisto in negozio)
  D = misto (domicilio + ritiro/negozio)
"""
from __future__ import annotations

import argparse
import heapq
import itertools
import json
import logging
import math
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

from validation import normalize_format, valid_price

log = logging.getLogger("tracker.optimizer")
LARGE_SEARCH = 2_000_000  # oltre questa soglia la ricerca esaustiva resta esatta ma viene segnalata

HOME, PICKUP, STORE = "domicilio", "ritiro", "negozio"
MODE_LABELS = {
    "A": "Tutto online a domicilio da una sola farmacia",
    "E": "Tutto online a domicilio da più farmacie",
    "B": "Tutto online con ritiro in farmacia",
    "C": "Tutto in farmacia fisica",
    "D": "Misto: domicilio + ritiro/negozio",
    "BC": "Ritiro e acquisto in farmacie fisiche",
}
RANKING_SIZE = 10
INCOMPLETE_SIZE = 5


@dataclass
class Channel:
    key: str
    kind: str                 # HOME / PICKUP / STORE
    seller: str               # id farmacia online o id negozio
    seller_name: str
    location_key: str | None = None   # luogo fisico da raggiungere (viaggio contato una volta)
    location: dict | None = None
    pickup_cost: float | None = None
    travel: dict | None = None        # {"cost":..., "distance_km":..., "estimated": True, ...}
    shipping_fn: Callable[[float], Any] | None = None
    shipping_reliability: str | None = None


@dataclass
class Option:
    product_id: str
    channel: Channel
    price: float
    offer: dict


@dataclass
class Cart:
    total: float
    complete: bool
    items: list[Option]
    orders: list[dict] = field(default_factory=list)
    travel: list[dict] = field(default_factory=list)
    products_cost: float = 0.0
    shipping_cost: float = 0.0
    pickup_cost: float = 0.0
    travel_cost: float = 0.0
    missing: list[str] = field(default_factory=list)
    mode: str = ""
    sellers: tuple = ()

    @property
    def n_pharmacies(self) -> int:
        return len(self.sellers)


# --------------------------------------------------------------------------- costruzione canali

def offer_matches_current_format(offer: dict, products: list[dict]) -> bool:
    """True se l'offerta è del formato oggi monitorato per quel prodotto.

    - offerta con `format` -> deve coincidere con il formato corrente;
    - offerta senza `format` (scritta prima dell'introduzione del campo) -> accettata solo se il
      prodotto non ha mai cambiato formato; altrimenti è considerata del vecchio formato.
    """
    prod = next((p for p in products if p["id"] == offer.get("product_id")), None)
    if prod is None:
        return False
    current = (prod.get("format") or {}).get("label")
    if offer.get("format"):
        return offer["format"] == current
    return not prod.get("format_history")

def build_options(products: list[dict], offers: list[dict], shipping_cfg: dict, physical: dict | None,
                  pharmacy_names: dict[str, str]) -> tuple[dict[str, list[Option]], list[Channel], list[str]]:
    """Crea le opzioni (prodotto -> canale) solo da offerte utilizzabili e verificate."""
    from shipping import quote

    notes: list[str] = []
    channels: dict[str, Channel] = {}
    options: dict[str, list[Option]] = {p["id"]: [] for p in products}
    # Difesa anche per snapshot scritti prima della regola "solo disponibilità confermata".
    # `_uncertain` marca le copie interne usate SOLO per le alternative potenziali (mai nel carrello verificato).
    usable = [o for o in offers if o.get("usable") and offer_matches_current_format(o, products)
              and valid_price(o.get("price")) and (o.get("availability") == "DISPONIBILE" or o.get("_uncertain"))]
    dropped = sum(1 for o in offers if o.get("usable") and not offer_matches_current_format(o, products))
    if dropped:
        notes.append(f"{dropped} offerte di un formato non più monitorato sono state ignorate.")
    max_km = None
    pickup_by_seller: dict[str, list[dict]] = {}
    if physical:
        max_km = (physical.get("travel") or {}).get("max_distance_km")
        for pp in physical.get("pickup_points") or []:
            pickup_by_seller.setdefault(pp["pharmacy_id"], []).append(pp)

    def home_channel(pid: str) -> Channel:
        key = f"{HOME}:{pid}"
        if key not in channels:
            ph = (shipping_cfg.get("pharmacies") or {}).get(pid) or {}
            channels[key] = Channel(key, HOME, pid, pharmacy_names.get(pid, pid),
                                    shipping_fn=lambda sub, _pid=pid: quote(shipping_cfg, _pid, sub),
                                    shipping_reliability=ph.get("reliability") or "sconosciuta")
        return channels[key]

    for o in usable:
        if o["product_id"] not in options:
            continue
        pid = o["pharmacy_id"]
        options[o["product_id"]].append(Option(o["product_id"], home_channel(pid), float(o["price"]), o))
        for i, pp in enumerate(pickup_by_seller.get(pid, [])):
            dist = pp.get("distance_km")
            if dist is None or (max_km is not None and dist > max_km):
                continue
            key = f"{PICKUP}:{pid}:{i}"
            if key not in channels:
                channels[key] = Channel(key, PICKUP, pid, pharmacy_names.get(pid, pid),
                                        location_key=pp.get("location_key") or f"pickup:{pid}:{i}", location=pp,
                                        pickup_cost=pp.get("pickup_cost_eur"), travel=pp.get("travel"))
            options[o["product_id"]].append(Option(o["product_id"], channels[key], float(o["price"]), o))

    # Negozi fisici: SOLO con prezzi rilevati (inseriti manualmente con data e fonte), già validati
    # da physical_pharmacies.split_store_prices; qui si ricontrollano prezzo, formato e disponibilità.
    stores = {s["id"]: s for s in (physical or {}).get("pharmacies") or []}
    current_format = {p["id"]: (p.get("format") or {}).get("label") for p in products}
    for sp in (physical or {}).get("store_prices") or []:
        st = stores.get(sp.get("store_id"))
        if st is None or not valid_price(sp.get("price")) or sp.get("product_id") not in options:
            continue
        if sp.get("available") is False:
            continue
        cur = current_format.get(sp["product_id"])
        if sp.get("format") and cur and normalize_format(sp["format"]) != normalize_format(cur):
            continue
        if max_km is not None and (st.get("distance_km") is None or st["distance_km"] > max_km):
            continue
        key = f"{STORE}:{st['id']}"
        if key not in channels:
            channels[key] = Channel(key, STORE, st["id"], st.get("name") or st["id"],
                                    location_key=f"store:{st['id']}", location=st, pickup_cost=0.0, travel=st.get("travel"))
        offer = {"product_id": sp["product_id"], "pharmacy_id": st["id"], "price": sp["price"], "url": st.get("website"),
                 "match_level": "RILEVAZIONE_MANUALE", "availability": "DISPONIBILE",
                 "observed_at": sp.get("observed_at"), "source": sp.get("source")}
        options[sp["product_id"]].append(Option(sp["product_id"], channels[key], float(sp["price"]), offer))
    if not (physical or {}).get("store_prices"):
        notes.append("Nessun prezzo rilevato in farmacia fisica: i prezzi in negozio non sono pubblicati online, "
                      "quindi l'acquisto in negozio (modalità C) non è calcolabile. Puoi inserirli in store_prices.json.")
    excluded = (physical or {}).get("store_prices_excluded") or []
    if excluded:
        notes.append(f"{len(excluded)} prezzi rilevati in negozio esclusi dal calcolo (scaduti, di un altro formato, "
                     "senza data o fonte, non validi o non disponibili): restano in store_prices.json.")
    return options, list(channels.values()), notes


# --------------------------------------------------------------------------- valutazione

def evaluate(assignment: tuple[Option, ...]) -> Cart | None:
    groups: dict[str, list[Option]] = {}
    for opt in assignment:
        groups.setdefault(opt.channel.key, []).append(opt)
    # un solo ordine per farmacia online: niente domicilio + ritiro sulla stessa farmacia
    sellers_online: dict[str, str] = {}
    for opts in groups.values():
        ch = opts[0].channel
        if ch.kind in (HOME, PICKUP):
            if sellers_online.setdefault(ch.seller, ch.key) != ch.key:
                return None
    products_cost = round(sum(o.price for o in assignment), 2)
    cart = Cart(total=0.0, complete=True, items=list(assignment), products_cost=products_cost)
    visited: dict[str, Channel] = {}
    for key, opts in groups.items():
        ch = opts[0].channel
        subtotal = round(sum(o.price for o in opts), 2)
        order = {"channel": ch.kind, "seller": ch.seller, "seller_name": ch.seller_name, "subtotal": subtotal,
                 "products": [o.product_id for o in opts]}
        if ch.kind == HOME:
            q = ch.shipping_fn(subtotal)
            order.update({"shipping": q.cost, "free_shipping": q.free_shipping_applied, "shipping_notes": q.notes,
                          "shipping_reliability": q.reliability})
            if q.cost is None or not q.complete:
                cart.complete = False
                cart.missing.append(f"spedizione {ch.seller_name}: " + "; ".join(q.notes))
            else:
                cart.shipping_cost += q.cost
        else:
            order["pickup_cost"] = ch.pickup_cost
            if ch.pickup_cost is None:
                cart.complete = False
                cart.missing.append(f"costo del ritiro presso {ch.seller_name} sconosciuto")
            else:
                cart.pickup_cost += ch.pickup_cost
            visited.setdefault(ch.location_key, ch)
        cart.orders.append(order)
    for loc_key, ch in visited.items():  # un viaggio per luogo, anche con più prodotti
        tr = dict(ch.travel or {})
        tr.update({"location_key": loc_key, "name": ch.seller_name})
        if tr.get("cost") is None:
            cart.complete = False
            cart.missing.append(f"costo del viaggio verso {ch.seller_name} non stimabile")
        else:
            cart.travel_cost += tr["cost"]
        cart.travel.append(tr)
    cart.shipping_cost = round(cart.shipping_cost, 2)
    cart.travel_cost = round(cart.travel_cost, 2)
    cart.pickup_cost = round(cart.pickup_cost, 2)
    cart.total = round(products_cost + cart.shipping_cost + cart.pickup_cost + cart.travel_cost, 2)
    kinds = {opts[0].channel.kind for opts in groups.values()}
    cart.sellers = tuple(sorted({opts[0].channel.seller for opts in groups.values()}))
    if kinds == {HOME}:
        cart.mode = "A" if len(cart.sellers) == 1 else "E"
    elif kinds == {PICKUP}:
        cart.mode = "B"
    elif kinds == {STORE}:
        cart.mode = "C"
    elif HOME in kinds:
        cart.mode = "D"
    else:
        cart.mode = "BC"
    return cart


def reliability(cart: Cart) -> str:
    if not cart.complete:
        return "incompleta"
    levels = []
    for it in cart.items:
        levels.append("alta" if it.offer.get("match_level") == "ID" else "media")
        if it.offer.get("availability") not in ("DISPONIBILE",):
            levels.append("media")
    for o in cart.orders:
        if o["channel"] == HOME and o.get("shipping_reliability") != "alta":
            levels.append("media")
    if cart.travel:
        levels.append("media")  # il viaggio è sempre una stima
    return "alta" if all(l == "alta" for l in levels) else "media"


def cart_to_dict(cart: Cart | None, products_by_id: dict[str, dict]) -> dict | None:
    if cart is None:
        return None
    warnings = []
    for it in cart.items:
        if it.offer.get("match_level") != "ID":
            warnings.append(f"{it.product_id}: prodotto identificato per nome+formato (nessun EAN/Minsan in pagina)")
        if it.offer.get("availability") != "DISPONIBILE":
            warnings.append(f"{it.product_id}: disponibilità {it.offer.get('availability', 'SCONOSCIUTA').lower()}")
    if cart.travel:
        warnings.append("Il costo del viaggio è una STIMA (distanza in linea d'aria × fattore strada).")
    return {
        "total": cart.total if cart.complete else None,
        "known_cost": cart.total,
        "complete": cart.complete,
        "reliability": reliability(cart),
        "mode": cart.mode,
        "mode_label": MODE_LABELS.get(cart.mode, cart.mode),
        "n_pharmacies": cart.n_pharmacies,
        "pharmacies": list(cart.sellers),
        "products_cost": cart.products_cost,
        "shipping_cost": cart.shipping_cost,
        "pickup_cost": cart.pickup_cost,
        "travel_cost": cart.travel_cost,
        "items": [{
            "product_id": it.product_id,
            "product_name": (products_by_id.get(it.product_id) or {}).get("name"),
            "channel": it.channel.kind,
            "seller": it.channel.seller,
            "seller_name": it.channel.seller_name,
            "price": it.price,
            "url": it.offer.get("url"),
            "match_level": it.offer.get("match_level"),
            "availability": it.offer.get("availability"),
            "scraped_at": it.offer.get("scraped_at") or it.offer.get("observed_at"),
        } for it in sorted(cart.items, key=lambda x: x.product_id)],
        "orders": cart.orders,
        "travel": cart.travel,
        "missing": cart.missing,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------- ricerca esaustiva

class _Top:
    """Mantiene i migliori N carrelli (min-total) senza conservarli tutti in memoria."""

    def __init__(self, n: int):
        self.n, self.heap, self.counter = n, [], itertools.count()

    def push(self, cart: Cart) -> None:
        item = (-cart.total, next(self.counter), cart)
        if len(self.heap) < self.n:
            heapq.heappush(self.heap, item)
        elif -self.heap[0][0] > cart.total:
            heapq.heapreplace(self.heap, item)

    def sorted(self) -> list[Cart]:
        return [c for _, _, c in sorted(self.heap, key=lambda x: (-x[0], x[1]))]

    def best(self) -> Cart | None:
        s = self.sorted()
        return s[0] if s else None


def optimize(products: list[dict], offers: list[dict], shipping_cfg: dict, physical: dict | None = None,
             pharmacy_names: dict[str, str] | None = None) -> dict:
    pharmacy_names = pharmacy_names or {}
    products_by_id = {p["id"]: p for p in products}
    options, channels, notes = build_options(products, offers, shipping_cfg, physical, pharmacy_names)
    missing_products = [pid for pid, opts in options.items() if not opts]
    target = [pid for pid in options if options[pid]]
    estimate = math.prod(len(options[pid]) for pid in target) if target else 0
    result: dict[str, Any] = {
        "complete_possible": not missing_products,
        "products_without_offers": missing_products,
        "products_considered": target,
        "options_per_product": {pid: len(opts) for pid, opts in options.items()},
        "evaluated_combinations": 0,
        "combinations_estimate": estimate,
        "uncertain_alternatives": [],
        "notes": notes,
    }
    if estimate > LARGE_SEARCH:
        log.warning("Ricerca esaustiva ampia: %d combinazioni", estimate)
        notes.append(f"Ricerca esaustiva su {estimate:,} combinazioni: il calcolo resta esatto ma è lento.".replace(",", "."))
    if missing_products:
        result["notes"].append("Impossibile comprare tutti e 5 i prodotti: nessuna offerta verificata e disponibile per "
                               + ", ".join(missing_products) + ". I carrelli sotto sono PARZIALI.")
    if not target:
        result.update({"best_overall": None, "best_single_pharmacy": None, "best_max_two_pharmacies": None,
                       "best_any_number": None, "by_mode": {}, "rankings": {"domicilio": [], "ritiro_fisico": [], "misto": []},
                       "incomplete_alternatives": [], "savings_vs_single": None})
        _add_uncertain(result, products, offers, shipping_cfg, physical, pharmacy_names, None, 0)
        result.update(comparison_display(result, physical))
        return result

    best_by_n = {1: _Top(1), 2: _Top(1), 99: _Top(1)}
    by_mode: dict[str, _Top] = {m: _Top(1) for m in MODE_LABELS}
    rank = {"domicilio": _Top(RANKING_SIZE), "ritiro_fisico": _Top(RANKING_SIZE), "misto": _Top(RANKING_SIZE)}
    incomplete = _Top(INCOMPLETE_SIZE)
    count = 0
    for assignment in itertools.product(*(options[pid] for pid in target)):
        cart = evaluate(assignment)
        if cart is None:
            continue
        count += 1
        if not cart.complete:
            incomplete.push(cart)
            continue
        n = cart.n_pharmacies
        if n == 1:
            best_by_n[1].push(cart)
        if n <= 2:
            best_by_n[2].push(cart)
        best_by_n[99].push(cart)
        by_mode[cart.mode].push(cart)
        if cart.mode in ("A", "E"):
            rank["domicilio"].push(cart)
        elif cart.mode in ("B", "C", "BC"):
            rank["ritiro_fisico"].push(cart)
        else:
            rank["misto"].push(cart)
    result["evaluated_combinations"] = count
    best = best_by_n[99].best()
    single = best_by_n[1].best()
    to_d = lambda c: cart_to_dict(c, products_by_id)  # noqa: E731
    result.update({
        "best_overall": to_d(best),
        "best_single_pharmacy": to_d(single),
        "best_max_two_pharmacies": to_d(best_by_n[2].best()),
        "best_any_number": to_d(best),
        "by_mode": {m: to_d(t.best()) for m, t in by_mode.items() if t.best() is not None},
        "rankings": {k: [to_d(c) for c in t.sorted()] for k, t in rank.items()},
        "incomplete_alternatives": [to_d(c) for c in incomplete.sorted()],
        "savings_vs_single": None,
    })
    if best is not None and single is not None:
        result["savings_vs_single"] = {
            "amount": round(single.total - best.total, 2),
            "single_total": single.total,
            "best_total": best.total,
            "single_pharmacy": single.sellers[0],
        }
    if best is None and result["incomplete_alternatives"]:
        result["notes"].append("Nessun carrello con tutti i costi verificati: vedi le alternative incomplete "
                               "(costo noto parziale, non vincitrici certe).")
    if result["incomplete_alternatives"] and best is not None:
        cheaper = [c for c in result["incomplete_alternatives"] if c["known_cost"] < best.total]
        if cheaper:
            result["notes"].append(
                f"{len(cheaper)} carrelli incompleti hanno un costo NOTO inferiore al migliore verificato: "
                "potrebbero convenire ma mancano dati fondamentali (es. spedizione sconosciuta).")
    _add_uncertain(result, products, offers, shipping_cfg, physical, pharmacy_names, best, len(target))
    result["physical_comparison"] = physical_comparison(result, physical)
    result.update(comparison_display(result, physical))
    return result


def _add_uncertain(result: dict, products: list[dict], offers: list[dict], shipping_cfg: dict, physical: dict | None,
                   names: dict[str, str], best: Cart | None, verified_coverage: int) -> None:
    alts = uncertain_alternatives(products, offers, shipping_cfg, physical, names, best, verified_coverage)
    result["uncertain_alternatives"] = alts
    if alts:
        what = "1 alternativa potenziale usa" if len(alts) == 1 else f"{len(alts)} alternative potenziali usano"
        result["notes"].append(f"{what} offerte con disponibilità da verificare: non sono vincitrici certe "
                               "(controlla la disponibilità sul sito della farmacia).")


def uncertain_alternatives(products: list[dict], offers: list[dict], shipping_cfg: dict, physical: dict | None,
                           names: dict[str, str], best: Cart | None, verified_coverage: int) -> list[dict]:
    """Carrelli che includono almeno un'offerta con prezzo verificato ma disponibilità SCONOSCIUTA.

    Stesse regole del carrello verificato (soglie, spedizioni, viaggi); si mostrano solo se costano
    meno del migliore verificato o coprono più prodotti. Mai vincitori certi.
    """
    extra = [dict(o, usable=True, _uncertain=True) for o in offers
             if o.get("availability_uncertain") and not o.get("usable") and valid_price(o.get("price"))]
    if not extra:
        return []
    options, _, _ = build_options(products, [o for o in offers if o.get("usable")] + extra, shipping_cfg, physical, names)
    target = [pid for pid in options if options[pid]]
    top = _Top(INCOMPLETE_SIZE)
    for assignment in itertools.product(*(options[pid] for pid in target)):
        if not any(opt.offer.get("_uncertain") for opt in assignment):
            continue
        cart = evaluate(assignment)
        if cart is None or not cart.complete:
            continue
        if best is not None and len(target) <= verified_coverage and cart.total >= best.total:
            continue
        top.push(cart)
    products_by_id = {p["id"]: p for p in products}
    out = []
    for cart in top.sorted():
        d = cart_to_dict(cart, products_by_id)
        d["reliability"] = "da verificare"
        d["label"] = "Alternativa potenziale: disponibilità da verificare"
        d["uncertain_products"] = sorted(it.product_id for it in cart.items if it.offer.get("_uncertain"))
        d["products_covered"] = len(target)
        d["covers_more_products"] = len(target) > verified_coverage
        out.append(d)
    return out


def comparison_display(result: dict, physical: dict | None) -> dict:
    """Differenze tra modalità e stime locali per la dashboard statica."""
    from physical_pharmacies import TRAVEL_MODES, travel_estimate

    modes = result.get("by_mode") or {}
    best = result.get("best_overall") or {}
    comparison = {}
    for channel, keys in (("home", ("A", "E")), ("pickup", ("B",)),
                          ("store", ("C",)), ("mixed", ("BC", "D"))):
        carts = [modes[k] for k in keys if (modes.get(k) or {}).get("total") is not None]
        if carts:
            cart = min(carts, key=lambda c: (c["total"], c["mode"] != best.get("mode")))
            comparison[channel] = {
                "mode": cart["mode"],
                "difference": round(cart["total"] - best["total"], 2) if best.get("total") is not None else None,
            }
    physical = physical or {}
    online = (result.get("physical_comparison") or {}).get("online_best_total")
    previews = {}
    for store in physical.get("pharmacies") or []:
        if not store.get("id"):
            continue
        trip = store.get("travel") or {}
        by_transport = {}
        for mode in TRAVEL_MODES:
            settings = {**(physical.get("travel") or {}), "mode": mode}
            # La stima originale usa la distanza prima dell'arrotondamento per la UI.
            if trip.get("mode") == mode and "cost" in trip:
                cost = trip["cost"]
            else:
                cost = travel_estimate(store.get("distance_km"), settings)["cost"]
            by_transport[mode] = {"cost": cost, "threshold": round(online - cost, 2)
                                  if online is not None and cost is not None else None}
        previews[store["id"]] = by_transport
    return {"channel_comparison": comparison, "travel_previews": previews}


def physical_comparison(result: dict, physical: dict | None) -> dict:
    """'Conviene online o in farmacia?' anche senza prezzi in negozio: soglia di convenienza."""
    out: dict[str, Any] = {"available": False}
    online = (result.get("by_mode") or {}).get("A") or None
    best_home = (result.get("rankings") or {}).get("domicilio") or []
    online = best_home[0] if best_home else online
    if not physical:
        out["reason"] = "Dati sulle farmacie fisiche non disponibili (esegui physical_pharmacies.py)."
        return out
    stores = sorted([s for s in physical.get("pharmacies") or [] if s.get("distance_km") is not None],
                    key=lambda s: s["distance_km"])
    if not stores:
        out["reason"] = "Nessuna farmacia fisica trovata entro la distanza massima."
        return out
    nearest = stores[0]
    tr = nearest.get("travel") or {}
    out.update({
        "available": True,
        "nearest_store": {k: nearest.get(k) for k in ("id", "name", "address", "distance_km", "website", "source")},
        "travel_estimate": tr,
        "online_best_total": online["total"] if online else None,
        "online_best_label": online["mode_label"] if online else None,
        "store_prices_known": bool(physical.get("store_prices")),
    })
    best_physical = (result.get("rankings") or {}).get("ritiro_fisico") or []
    if best_physical:
        out["physical_best_total"] = best_physical[0]["total"]
    if online and tr.get("cost") is not None:
        it = lambda x, d=2: f"{x:.{d}f}".replace(".", ",")  # noqa: E731 - formato italiano
        n = len(result.get("products_considered") or []) or 5
        what = "i 5 prodotti" if n == 5 else f"i {n} prodotti acquistabili online (carrello parziale)"
        out["products_count"] = n
        out["break_even_store_total"] = round(online["total"] - tr["cost"], 2)
        out["explanation"] = (
            f"Andare alla farmacia più vicina ({nearest.get('name') or 'senza nome'}, {it(nearest['distance_km'], 1)} km) costa "
            f"circa {it(tr['cost'])} € (stima). Conviene solo se {what} in negozio costano in totale meno di "
            f"{it(out['break_even_store_total'])} €, contro {it(online['total'])} € online a domicilio.")
    else:
        out["explanation"] = "Costo del viaggio non stimabile con i parametri attuali o nessuna offerta online completa."
    return out


def main(argv: list[str] | None = None) -> int:
    """Ricalcola i carrelli dall'ultimo data.json (senza nuovo scraping)."""
    from storage import ROOT, read_json, write_json_atomic

    ap = argparse.ArgumentParser(description="Ricalcola i carrelli ottimali da data.json")
    ap.add_argument("--dry-run", action="store_true", help="stampa senza scrivere data.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    data = read_json(ROOT / "data.json", required=True)
    if data.get("offers") is None:
        print("data.json non contiene offerte: esegui prima scraper.py")
        return 1
    config = read_json(ROOT / "config.json", required=True)
    from physical_pharmacies import load_physical
    from snapshot import recompute
    from storage import now_utc_iso
    # Stesso ricalcolo di demote/cambio formato: registro, spedizioni e parametri di viaggio ATTUALI.
    summary = recompute(data, config=config, registry=read_json(ROOT / "pharmacies.json", default=None),
                        shipping_cfg=read_json(ROOT / "shipping_config.json", default={}),
                        physical=load_physical(config), now=now_utc_iso(), reason="ricalcolo manuale (optimizer.py)")
    res = data["carts"]
    if args.dry_run:
        print(json.dumps({k: res[k] for k in ("best_overall", "savings_vs_single", "notes")}, ensure_ascii=False, indent=2))
        return 0
    write_json_atomic(ROOT / "data.json", data)
    print("Carrelli ricalcolati:", res["evaluated_combinations"], "combinazioni;", summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
