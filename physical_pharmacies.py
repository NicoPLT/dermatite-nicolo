"""Farmacie fisiche vicine alla località dell'utente e costi di spostamento (stime).

Fonti (gratuite, OpenStreetMap, © OpenStreetMap contributors, licenza ODbL):
- Nominatim per geocodificare la località (città o CAP, MAI un indirizzo privato);
- Overpass API per le farmacie (amenity=pharmacy) entro la distanza massima.

robots.txt di Nominatim e Overpass esclude i crawler dagli endpoint API: qui li usiamo come API,
nei limiti delle rispettive usage policy (User-Agent identificabile, 1 geocodifica in cache,
1 interrogazione Overpass alla settimana). Per evitare del tutto Nominatim si possono indicare
lat/lon in config.json -> location.

Disponibilità in negozio: i prezzi e le giacenze delle farmacie fisiche non sono pubblicati online,
quindi di default sono "non verificata". Valori possibili:
  confermata | ritiro prenotabile | non verificata | non disponibile
Prezzi rilevati di persona si possono inserire in store_prices.json (con data e fonte).

Il costo del viaggio è SEMPRE una stima, etichettata come tale, e il ritiro non è mai gratis di default.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from typing import Any
from urllib.parse import urlencode

from storage import ROOT, days_since, now_utc_iso, read_json, write_json_atomic

log = logging.getLogger("tracker.physical")
PHYSICAL_PATH = ROOT / "physical_pharmacies.json"
STORE_PRICES_PATH = ROOT / "store_prices.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Istanza principale + mirror pubblici elencati sul wiki OSM (usati solo se la principale fallisce).
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
]
OVERPASS = OVERPASS_ENDPOINTS[0]
AVAILABILITY_VALUES = ("confermata", "ritiro prenotabile", "non verificata", "non disponibile")
TRAVEL_MODES = ("auto", "mezzi_pubblici", "piedi", "bici")


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def travel_estimate(distance_km: float | None, travel: dict) -> dict:
    """Costo andata/ritorno STIMATO verso un luogo. None se i parametri mancano."""
    mode = travel.get("mode", "mezzi_pubblici")
    out: dict[str, Any] = {"mode": mode, "distance_km": None if distance_km is None else round(distance_km, 2),
                           "estimated": True, "cost": None,
                           "method": "stima: distanza in linea d'aria × fattore strada, andata e ritorno"}
    if distance_km is None:
        out["notes"] = "distanza sconosciuta"
        return out
    factor = float(travel.get("road_factor") or 1.3)
    road_km = round(distance_km * factor, 2)
    out["road_km_round_trip"] = round(2 * road_km, 2)
    if mode == "auto":
        per_km, parking = travel.get("car_cost_per_km_eur"), travel.get("car_parking_eur")
        if per_km is None or parking is None:
            out["notes"] = "parametri auto mancanti (car_cost_per_km_eur / car_parking_eur)"
            return out
        out["cost"] = round(2 * road_km * float(per_km) + float(parking), 2)
        out["breakdown"] = f"{2 * road_km:.1f} km × {float(per_km):.2f} €/km + parcheggio {float(parking):.2f} €"
    elif mode == "mezzi_pubblici":
        fare = travel.get("public_transport_round_trip_eur")
        if fare is None:
            out["notes"] = "costo dei mezzi pubblici non configurato"
            return out
        out["cost"] = round(float(fare), 2)
        out["breakdown"] = f"biglietti andata e ritorno {float(fare):.2f} €"
    elif mode in ("piedi", "bici"):
        cost = travel.get("walk_bike_cost_eur")
        out["cost"] = None if cost is None else round(float(cost), 2)
        out["breakdown"] = "a piedi/bici: costo monetario scelto dall'utente (tempo non conteggiato)"
    else:
        out["notes"] = f"mezzo '{mode}' non supportato"
    return out


# --------------------------------------------------------------------------- rete

def geocode(session, location: dict) -> dict:
    if location.get("lat") is not None and location.get("lon") is not None:
        return {"lat": float(location["lat"]), "lon": float(location["lon"]), "source": "config.json",
                "display_name": location.get("label")}
    params = {"format": "jsonv2", "limit": 1, "countrycodes": location.get("country_code") or "it"}
    if location.get("postcode"):
        params["postalcode"] = str(location["postcode"])
    if location.get("city"):
        params["city"] = location["city"]
    if "postalcode" not in params and "city" not in params:
        raise ValueError("config.location deve contenere city o postcode")
    res = session.get(NOMINATIM + "?" + urlencode(params), check_robots=False, accept="application/json")
    if not res.ok:
        raise RuntimeError(f"Nominatim HTTP {res.status}")
    data = json.loads(res.text)
    if not data:
        raise RuntimeError(f"Località non trovata: {params}")
    hit = data[0]
    return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "display_name": hit.get("display_name"),
            "source": "Nominatim (© OpenStreetMap contributors)", "geocoded_at": now_utc_iso()}


def geocode_address(session, addr: dict) -> dict | None:
    """Geocodifica l'indirizzo di un PUNTO VENDITA (dato commerciale pubblico)."""
    if not addr.get("city"):
        return None
    params = {"format": "jsonv2", "limit": 1, "countrycodes": "it", "city": addr["city"]}
    if addr.get("address"):
        params["street"] = addr["address"]
    if addr.get("postcode"):
        params["postalcode"] = addr["postcode"]
    res = session.get(NOMINATIM + "?" + urlencode(params), check_robots=False, accept="application/json")
    data = json.loads(res.text) if res.ok else []
    if not data:
        return None
    return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}


def overpass_pharmacies(session, lat: float, lon: float, radius_m: int) -> list[dict]:
    """Farmacie OSM in un riquadro attorno al centro (il filtro per distanza si fa dopo).

    Le query `around` via GET sono andate in timeout su tutte le istanze il 18/11/2026;
    bbox via POST risponde in ~35 s ed è più leggera per il server.
    """
    from scrapers.http import FetchError

    r_km = radius_m / 1000.0
    dlat = r_km / 111.32
    dlon = r_km / (111.32 * max(0.1, math.cos(math.radians(lat))))
    bbox = f"{lat - dlat:.5f},{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f}"
    query = f"[out:json][timeout:60];nwr[\"amenity\"=\"pharmacy\"]({bbox});out center tags;"
    errors = []
    elements = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            res = session.post_api(endpoint, {"data": query}, accept="application/json", timeout=90, max_attempts=2)
            if res.ok:
                elements = json.loads(res.text).get("elements", [])
                log.info("Overpass: %d elementi da %s", len(elements), endpoint)
                break
            errors.append(f"{endpoint}: HTTP {res.status}")
        except (FetchError, ValueError) as exc:
            errors.append(f"{endpoint}: {exc}")
    if elements is None:
        raise RuntimeError("Overpass non disponibile: " + " | ".join(errors))
    out = []
    for el in elements:
        tags = el.get("tags") or {}
        plat = el.get("lat") or (el.get("center") or {}).get("lat")
        plon = el.get("lon") or (el.get("center") or {}).get("lon")
        if plat is None or plon is None:
            continue
        street = " ".join(x for x in (tags.get("addr:street"), tags.get("addr:housenumber")) if x)
        city = tags.get("addr:city")
        out.append({
            "id": f"osm:{el['type']}/{el['id']}",
            "name": tags.get("name") or tags.get("operator") or "Farmacia (nome non indicato in OSM)",
            "address": ", ".join(x for x in (street, tags.get("addr:postcode"), city) if x) or None,
            "lat": float(plat), "lon": float(plon),
            "website": tags.get("website") or tags.get("contact:website"),
            "opening_hours": tags.get("opening_hours"),
            "kind": "parafarmacia" if tags.get("dispensing") == "no" else "farmacia",
            "osm_url": f"https://www.openstreetmap.org/{el['type']}/{el['id']}",
            "source": "OpenStreetMap (Overpass API, ODbL)",
        })
    return out


# --------------------------------------------------------------------------- dati

def refresh(config: dict, session, *, force: bool = False) -> dict:
    """Aggiorna physical_pharmacies.json (al massimo una volta a settimana se non forzato)."""
    cache = read_json(PHYSICAL_PATH, default={}) or {}
    loc_cfg = config.get("location") or {}
    travel = config.get("travel") or {}
    loc_key = json.dumps({k: loc_cfg.get(k) for k in ("city", "postcode", "lat", "lon")}, sort_keys=True)
    radius_km = float(travel.get("max_distance_km") or 3)
    fresh = (cache.get("location_key") == loc_key and (days_since(cache.get("fetched_at")) or 99) < 7
             and float(cache.get("radius_km") or 0) >= radius_km)
    if fresh and not force:
        log.info("Farmacie fisiche in cache ancora valide (%s)", cache.get("fetched_at"))
        return cache
    if cache.get("location_key") == loc_key and cache.get("location"):
        center = cache["location"]
    else:
        center = geocode(session, loc_cfg)
    pharmacies = overpass_pharmacies(session, center["lat"], center["lon"], int(radius_km * 1000))
    shipping = read_json(ROOT / "shipping_config.json", default={}) or {}
    pickup_points = []
    old_pp = {(p["pharmacy_id"], p.get("address"), p.get("city")): p for p in cache.get("pickup_points") or []}
    for pid, ph in (shipping.get("pharmacies") or {}).items():
        if not ph.get("pickup_available"):
            continue
        for loc in ph.get("pickup_locations") or []:
            prev = old_pp.get((pid, loc.get("address"), loc.get("city")))
            coords = ({"lat": loc["lat"], "lon": loc["lon"]} if loc.get("lat") is not None else
                      ({"lat": prev["lat"], "lon": prev["lon"]} if prev and prev.get("lat") is not None else None))
            if coords is None and loc.get("city"):
                try:
                    coords = geocode_address(session, loc)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Geocodifica punto di ritiro %s fallita: %s", pid, exc)
            pickup_points.append({
                "pharmacy_id": pid, "name": loc.get("name"), "address": loc.get("address"), "city": loc.get("city"),
                "province": loc.get("province"), "postcode": loc.get("postcode"),
                "lat": coords["lat"] if coords else None, "lon": coords["lon"] if coords else None,
                "pickup_cost_eur": ph.get("pickup_cost_eur"),
                "availability": loc.get("availability") or "ritiro prenotabile",
            })
    data = {
        "schema_version": 1,
        "fetched_at": now_utc_iso(),
        "location_key": loc_key,
        "location": {"label": loc_cfg.get("label") or loc_cfg.get("city") or loc_cfg.get("postcode"),
                     "city": loc_cfg.get("city"), "postcode": loc_cfg.get("postcode"),
                     "lat": round(center["lat"], 5), "lon": round(center["lon"], 5),
                     "source": center.get("source"), "display_name": center.get("display_name")},
        "radius_km": radius_km,
        "attribution": "© OpenStreetMap contributors (ODbL)",
        "pharmacies_raw": pharmacies,
        "pickup_points": pickup_points,
    }
    write_json_atomic(PHYSICAL_PATH, data)
    return data


def load_physical(config: dict, cache: dict | None = None) -> dict | None:
    """Vista per l'ottimizzatore/dashboard, ricalcolata con i parametri di viaggio ATTUALI."""
    cache = cache if cache is not None else read_json(PHYSICAL_PATH, default=None)
    if not cache:
        return None
    travel = dict(config.get("travel") or {})
    max_km = float(travel.get("max_distance_km") or 3)
    loc = cache.get("location") or {}
    products = [p["id"] for p in config.get("products", [])]
    store_prices = (read_json(STORE_PRICES_PATH, default={}) or {}).get("entries") or []
    priced = {(s.get("store_id"), s.get("product_id")): s for s in store_prices}
    stores = []
    for ph in cache.get("pharmacies_raw") or []:
        d = haversine_km(loc["lat"], loc["lon"], ph["lat"], ph["lon"])
        if d > max_km:
            continue
        avail = {}
        for pid in products:
            sp = priced.get((ph["id"], pid))
            if sp is None:
                avail[pid] = "non verificata"
            else:
                avail[pid] = "confermata" if sp.get("available", True) else "non disponibile"
        stores.append({**ph, "distance_km": round(d, 2), "travel": travel_estimate(d, travel), "availability": avail})
    stores.sort(key=lambda s: s["distance_km"])
    stores = stores[: int(travel.get("max_physical_pharmacies") or 25)]
    pickups = []
    for pp in cache.get("pickup_points") or []:
        d = haversine_km(loc["lat"], loc["lon"], pp["lat"], pp["lon"]) if pp.get("lat") is not None else None
        pickups.append({**pp, "distance_km": None if d is None else round(d, 2),
                        "within_max_distance": d is not None and d <= max_km,
                        "location_key": f"pickup:{pp['pharmacy_id']}:{pp.get('address')}",
                        "travel": travel_estimate(d, travel)})
    return {
        "location": loc,
        "fetched_at": cache.get("fetched_at"),
        "attribution": cache.get("attribution"),
        "travel": travel,
        "pharmacies": stores,
        "pickup_points": pickups,
        "store_prices": [s for s in store_prices if s.get("price") is not None and s.get("available", True)],
        "availability_values": list(AVAILABILITY_VALUES),
        "notes": [
            "Prezzi e giacenze delle farmacie fisiche non sono pubblicati online: disponibilità 'non verificata'.",
            "Costi di viaggio = STIMA (distanza in linea d'aria × fattore strada, andata e ritorno).",
        ],
    }


def set_location(config: dict, *, city: str | None, postcode: str | None, mode: str | None,
                 max_km: float | None) -> dict:
    """Aggiorna località/viaggio in config.json. Solo città o CAP: mai un indirizzo."""
    loc = config.setdefault("location", {})
    if city is not None or postcode is not None:
        if postcode and not re.fullmatch(r"\d{5}", postcode.strip()):
            raise ValueError("CAP non valido: servono 5 cifre")
        if city and re.search(r"\d|\b(via|viale|piazza|corso|largo)\b", city, re.I):
            raise ValueError("Indica solo la città (niente indirizzo né numeri civici)")
        loc["city"] = (city or "").strip() or None
        loc["postcode"] = (postcode or "").strip() or None
        loc["label"] = loc["city"] or loc["postcode"]
        loc.pop("lat", None)
        loc.pop("lon", None)
    travel = config.setdefault("travel", {})
    if mode:
        if mode not in TRAVEL_MODES:
            raise ValueError(f"Mezzo non valido: {mode} (ammessi: {', '.join(TRAVEL_MODES)})")
        travel["mode"] = mode
    if max_km is not None:
        if not 0.2 <= max_km <= 50:
            raise ValueError("Distanza massima fuori intervallo (0,2-50 km)")
        travel["max_distance_km"] = max_km
    return config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Aggiorna le farmacie fisiche vicine alla località")
    ap.add_argument("--force", action="store_true", help="ignora la cache settimanale")
    ap.add_argument("--city", help="nuova città (solo città, mai indirizzo)")
    ap.add_argument("--postcode", help="nuovo CAP (5 cifre)")
    ap.add_argument("--mode", choices=TRAVEL_MODES, help="mezzo di trasporto")
    ap.add_argument("--max-km", type=float, help="distanza massima in km")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from scrapers.http import PoliteSession
    config = read_json(ROOT / "config.json", required=True)
    if any(v is not None for v in (args.city, args.postcode, args.mode, args.max_km)):
        set_location(config, city=args.city, postcode=args.postcode, mode=args.mode, max_km=args.max_km)
        write_json_atomic(ROOT / "config.json", config)
        print("config.json aggiornato:", config["location"], {k: config["travel"].get(k) for k in ("mode", "max_distance_km")})
    session = PoliteSession(config["http"])
    session.set_domain_delay(NOMINATIM, 2.0)  # policy Nominatim: max 1 richiesta/s
    data = refresh(config, session, force=args.force)
    view = load_physical(config, data)
    print(f"Località: {view['location'].get('label')} ({view['location']['lat']}, {view['location']['lon']})")
    print(f"Farmacie entro {view['travel'].get('max_distance_km')} km: {len(view['pharmacies'])}")
    for s in view["pharmacies"][:10]:
        print(f"  {s['distance_km']:5.2f} km  {s['name']}  {s.get('address') or ''}  viaggio≈{s['travel'].get('cost')} €")
    for p in view["pickup_points"]:
        print(f"  ritiro {p['pharmacy_id']}: {p.get('city')} distanza {p.get('distance_km')} km")
    return 0


if __name__ == "__main__":
    sys.exit(main())
