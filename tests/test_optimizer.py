"""Test dell'ottimizzatore: combinazioni, spedizioni/soglie, ritiro multiplo, viaggio, dati mancanti.

Tutte le offerte qui sono SIMULATE.
"""
import itertools
import random

import pytest

from optimizer import optimize
from physical_pharmacies import haversine_km, set_location, travel_estimate

PRODUCTS = [{"id": f"P00{i}", "name": f"Prodotto {i}"} for i in range(1, 6)]


def offer(pid, ph, price, avail="DISPONIBILE", match="ID", usable=True):
    return {"product_id": pid, "pharmacy_id": ph, "price": price, "availability": avail, "match_level": match,
            "usable": usable, "url": f"https://{ph}.test/{pid}", "status": "OK"}


def ship(std, thr=None, incl=True, rel="alta"):
    return {"standard_cost_eur": std, "free_threshold_eur": thr, "free_threshold_inclusive": incl,
            "free_threshold_verified": thr is not None, "reliability": rel}


def test_single_pharmacy_with_free_shipping_beats_split():
    # A: tutto a 10 € con spedizione gratis da 50 € (totale 50). B: prodotti a 9,50 ma spedizione 6 €.
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS] + [offer(p["id"], "B", 9.5) for p in PRODUCTS]
    cfg = {"pharmacies": {"A": ship(6.0, 50.0), "B": ship(6.0)}}
    r = optimize(PRODUCTS, offers, cfg)
    best = r["best_overall"]
    assert best["total"] == 50.0 and best["pharmacies"] == ["A"] and best["mode"] == "A"
    assert r["best_single_pharmacy"]["total"] == 50.0
    assert r["savings_vs_single"]["amount"] == 0.0


def test_split_order_when_cheaper():
    offers = [offer("P001", "A", 5.0), offer("P001", "B", 30.0)] + \
             [offer(p["id"], "B", 10.0) for p in PRODUCTS[1:]] + [offer(p["id"], "A", 20.0) for p in PRODUCTS[1:]]
    cfg = {"pharmacies": {"A": ship(3.0), "B": ship(3.0)}}
    r = optimize(PRODUCTS, offers, cfg)
    best = r["best_overall"]
    assert best["total"] == 5.0 + 40.0 + 3.0 + 3.0
    assert best["mode"] == "E" and best["n_pharmacies"] == 2
    assert r["best_single_pharmacy"]["total"] == min(5 + 80 + 3, 30 + 40 + 3)
    assert r["savings_vs_single"]["amount"] == round(73 - 51, 2)
    assert r["best_max_two_pharmacies"]["total"] == 51.0


def test_threshold_pushes_product_to_same_pharmacy():
    # P005 costa 1 € in più su A, ma comprarlo lì fa scattare la spedizione gratuita (>= 40).
    offers = [offer(p["id"], "A", 9.0) for p in PRODUCTS[:4]] + [offer("P005", "A", 5.0), offer("P005", "B", 4.0)]
    cfg = {"pharmacies": {"A": ship(5.0, 40.0), "B": ship(5.0)}}
    best = optimize(PRODUCTS, offers, cfg)["best_overall"]
    assert best["pharmacies"] == ["A"] and best["total"] == 41.0


def test_unknown_shipping_is_never_a_winner():
    offers = [offer(p["id"], "cheap", 1.0) for p in PRODUCTS] + [offer(p["id"], "known", 10.0) for p in PRODUCTS]
    cfg = {"pharmacies": {"cheap": ship(None), "known": ship(4.0)}}
    r = optimize(PRODUCTS, offers, cfg)
    assert r["best_overall"]["pharmacies"] == ["known"] and r["best_overall"]["complete"]
    inc = r["incomplete_alternatives"]
    assert inc and inc[0]["total"] is None and inc[0]["known_cost"] == 5.0
    assert any("costo NOTO inferiore" in n for n in r["notes"])


def test_only_incomplete_data_gives_no_winner():
    offers = [offer(p["id"], "x", 2.0) for p in PRODUCTS]
    r = optimize(PRODUCTS, offers, {"pharmacies": {"x": ship(None)}})
    assert r["best_overall"] is None and r["incomplete_alternatives"]


def test_missing_product_gives_partial_cart():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS[:4]] + [offer("P005", "A", 10.0, avail="NON DISPONIBILE", usable=False)]
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5.0)}})
    assert not r["complete_possible"] and r["products_without_offers"] == ["P005"]
    assert r["best_overall"]["total"] == 45.0
    assert any("PARZIALI" in n for n in r["notes"])


def test_unusable_offers_are_ignored():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS] + [offer("P001", "B", 1.0, usable=False)]
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(0.5), "B": ship(0.5)}})
    assert r["best_overall"]["pharmacies"] == ["A"]


def test_reliability_levels():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5.0)}})
    assert r["best_overall"]["reliability"] == "alta"
    offers[0]["match_level"] = "NOME_FORMATO"
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5.0)}})
    assert r["best_overall"]["reliability"] == "media"
    r = optimize(PRODUCTS, [offer(p["id"], "A", 10.0) for p in PRODUCTS], {"pharmacies": {"A": ship(5.0, rel="media")}})
    assert r["best_overall"]["reliability"] == "media"


# --------------------------------------------------------------------------- ritiro fisico e viaggio
def physical(pickups=(), stores=(), store_prices=(), max_km=5):
    return {"travel": {"max_distance_km": max_km}, "pickup_points": list(pickups), "pharmacies": list(stores),
            "store_prices": list(store_prices)}


def test_multi_product_pickup_counts_travel_once():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    pp = {"pharmacy_id": "A", "name": "Punto A", "distance_km": 1.0, "pickup_cost_eur": 0.0,
          "location_key": "pickup:A", "travel": {"cost": 3.0, "distance_km": 1.0, "estimated": True}}
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(8.0)}}, physical(pickups=[pp]))
    best = r["best_overall"]
    assert best["mode"] == "B" and best["total"] == 53.0 and best["travel_cost"] == 3.0
    assert len(best["travel"]) == 1  # un solo viaggio anche con 5 prodotti
    assert r["rankings"]["domicilio"][0]["total"] == 58.0
    assert best["reliability"] == "media"  # il viaggio è sempre una stima


def test_pickup_with_unknown_cost_is_incomplete():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    pp = {"pharmacy_id": "A", "distance_km": 1.0, "pickup_cost_eur": None, "location_key": "k",
          "travel": {"cost": 0.5}}
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(8.0)}}, physical(pickups=[pp]))
    assert r["best_overall"]["mode"] == "A"  # il ritiro non è mai gratis di default
    assert any(c["mode"] == "B" for c in r["incomplete_alternatives"])


def test_pickup_beyond_max_distance_is_excluded():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    pp = {"pharmacy_id": "A", "distance_km": 106.5, "pickup_cost_eur": 0.0, "location_key": "k", "travel": {"cost": 0.0}}
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(8.0)}}, physical(pickups=[pp], max_km=3))
    assert r["rankings"]["ritiro_fisico"] == []


def test_mixed_mode_and_store_prices():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    store = {"id": "osm:node/1", "name": "Farmacia Sotto Casa", "distance_km": 0.4, "travel": {"cost": 0.0, "distance_km": 0.4}}
    prices = [{"store_id": "osm:node/1", "product_id": "P001", "price": 4.0, "available": True}]
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(2.0)}}, physical(stores=[store], store_prices=prices))
    best = r["best_overall"]
    assert best["mode"] == "D" and best["total"] == 4.0 + 40.0 + 2.0
    assert "C" not in r["by_mode"]  # tutto in negozio impossibile: prezzi in negozio solo per P001


def test_without_store_prices_mode_c_is_explained():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    store = {"id": "s1", "name": "S", "distance_km": 0.5, "travel": {"cost": 4.4}}
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5.0)}}, physical(stores=[store]))
    assert any("store_prices.json" in n for n in r["notes"])
    pc = r["physical_comparison"]
    assert pc["available"] and pc["break_even_store_total"] == round(55.0 - 4.4, 2)


def test_channel_comparison_groups_ties_and_prices_against_reported_winner():
    from optimizer import comparison_display
    totals = {"A": 25.55, "E": 25.55, "B": 27.04, "C": 34.62, "BC": 31.50, "D": 30.10}
    modes = {mode: {"mode": mode, "total": total} for mode, total in totals.items()}
    comparison = comparison_display({"by_mode": modes, "best_overall": modes["E"]}, None)
    assert comparison["channel_comparison"] == {
        "home": {"mode": "E", "difference": 0.0}, "pickup": {"mode": "B", "difference": 1.49},
        "store": {"mode": "C", "difference": 9.07}, "mixed": {"mode": "D", "difference": 4.55},
    }
    assert comparison["travel_previews"] == {}


@pytest.mark.parametrize("with_offers", [False, True])
def test_local_travel_preview_uses_only_known_costs(with_offers):
    nearby = {"id": "s1", "name": "Farmacia di prova", "distance_km": 0.6,
              "travel": {"cost": 2.4, "estimated": True, "mode": "auto"}}
    ph = physical(stores=[nearby])
    ph["travel"].update(public_transport_round_trip_eur=4.5, walk_bike_cost_eur=None)
    offers = [offer(p["id"], "A", 10) for p in PRODUCTS] if with_offers else []
    result = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5)}}, ph)
    channels = {"home": {"mode": "A", "difference": 0.0}} if with_offers else {}
    assert result["channel_comparison"] == channels
    previews = result["travel_previews"]["s1"]
    assert previews["auto"] == {"cost": 2.4, "threshold": 52.6 if with_offers else None}
    assert previews["mezzi_pubblici"] == {"cost": 4.5, "threshold": 50.5 if with_offers else None}
    assert previews["piedi"] == previews["bici"] == {"cost": None, "threshold": None}


def test_same_pharmacy_cannot_mix_home_and_pickup():
    offers = [offer(p["id"], "A", 10.0) for p in PRODUCTS]
    pp = {"pharmacy_id": "A", "distance_km": 1.0, "pickup_cost_eur": 0.0, "location_key": "k", "travel": {"cost": 1.0}}
    r = optimize(PRODUCTS, offers, {"pharmacies": {"A": ship(5.0)}}, physical(pickups=[pp]))
    for c in r["rankings"]["misto"]:
        assert not ({"A"} == set(c["pharmacies"]) and c["n_pharmacies"] == 1)
    assert r["rankings"]["misto"] == []


@pytest.mark.parametrize("mode,expected", [
    ("auto", round(2 * 2.0 * 1.3 * 0.35 + 2.0, 2)), ("mezzi_pubblici", 4.4), ("piedi", 0.0), ("bici", 0.0)])
def test_travel_cost_estimates(mode, expected):
    travel = {"mode": mode, "road_factor": 1.3, "car_cost_per_km_eur": 0.35, "car_parking_eur": 2.0,
              "public_transport_round_trip_eur": 4.4, "walk_bike_cost_eur": 0.0}
    t = travel_estimate(2.0, travel)
    assert t["cost"] == expected and t["estimated"] is True


def test_travel_cost_missing_parameters_is_none():
    assert travel_estimate(2.0, {"mode": "auto"})["cost"] is None
    assert travel_estimate(None, {"mode": "mezzi_pubblici", "public_transport_round_trip_eur": 4.4})["cost"] is None


def test_haversine_known_distance():
    # Milano Duomo -> Milano Centrale: circa 2,4 km in linea d'aria
    assert 2.0 < haversine_km(45.4642, 9.1900, 45.4859, 9.2035) < 2.8


def test_set_location_rejects_addresses():
    cfg = {"location": {"city": "Milano"}, "travel": {}}
    set_location(cfg, city="Torino", postcode=None, mode="auto", max_km=5)
    assert cfg["location"]["city"] == "Torino" and cfg["travel"]["mode"] == "auto"
    with pytest.raises(ValueError):
        set_location(cfg, city="Via Roma 10", postcode=None, mode=None, max_km=None)
    with pytest.raises(ValueError):
        set_location(cfg, city=None, postcode="2012", mode=None, max_km=None)


# --------------------------------------------------------------------------- cambio formato
def test_optimizer_ignores_offers_of_previous_format():
    products = [dict(p, format={"label": "1000 ml"}) for p in PRODUCTS]
    products[2] = {"id": "P003", "name": "Lipocrema", "format": {"label": "100 ml"},
                   "format_history": [{"label": "50 ml", "until": "2026-11-18"}]}
    offers = [dict(offer(p["id"], "A", 10.0), format=p["format"]["label"]) for p in products if p["id"] != "P003"]
    offers.append(dict(offer("P003", "A", 1.0), format="50 ml"))     # vecchio formato: ignorata
    offers.append(offer("P003", "B", 2.0))                            # senza formato, prodotto cambiato: ignorata
    r = optimize(products, offers, {"pharmacies": {"A": ship(5.0), "B": ship(1.0)}})
    assert r["products_without_offers"] == ["P003"] and not r["complete_possible"]
    assert any("formato non più monitorato" in n for n in r["notes"])
    offers.append(dict(offer("P003", "A", 18.0), format="100 ml"))   # formato corrente: usata
    r = optimize(products, offers, {"pharmacies": {"A": ship(5.0), "B": ship(1.0)}})
    assert r["complete_possible"] and r["best_overall"]["total"] == 40.0 + 18.0 + 5.0


def test_offers_without_format_ok_when_format_never_changed():
    from optimizer import offer_matches_current_format
    prods = [{"id": "P001", "format": {"label": "1000 ml"}}]
    assert offer_matches_current_format({"product_id": "P001"}, prods)
    assert not offer_matches_current_format({"product_id": "P001", "format": "500 ml"}, prods)


# --------------------------------------------------------------------------- controllo incrociato
def naive_best(products, offers, ship_cfg):
    """Algoritmo indipendente e ingenuo (solo domicilio) per verificare l'ottimizzatore."""
    from shipping import quote
    by_p = {p["id"]: [o for o in offers if o["product_id"] == p["id"] and o["usable"]] for p in products}
    best = None
    for combo in itertools.product(*by_p.values()):
        orders = {}
        for o in combo:
            orders[o["pharmacy_id"]] = orders.get(o["pharmacy_id"], 0) + o["price"]
        total, ok = sum(o["price"] for o in combo), True
        for ph, sub in orders.items():
            q = quote(ship_cfg, ph, round(sub, 2))
            if q.cost is None:
                ok = False
                break
            total += q.cost
        if ok and (best is None or round(total, 2) < best):
            best = round(total, 2)
    return best


@pytest.mark.parametrize("seed", range(12))
def test_matches_naive_bruteforce(seed):
    rnd = random.Random(seed)
    phs = [f"F{i}" for i in range(rnd.randint(2, 5))]
    offers = []
    for p in PRODUCTS:
        for ph in phs:
            if rnd.random() < 0.8:
                offers.append(offer(p["id"], ph, round(rnd.uniform(5, 30), 2)))
    for p in PRODUCTS:  # garantisce almeno un'offerta per prodotto
        if not any(o["product_id"] == p["id"] for o in offers):
            offers.append(offer(p["id"], phs[0], 12.34))
    cfg = {"pharmacies": {ph: ship(round(rnd.uniform(3, 7), 2), rnd.choice([None, 39.9, 59.0, 79.9]), rnd.random() < 0.5)
                          for ph in phs}}
    r = optimize(PRODUCTS, offers, cfg)
    assert r["best_overall"]["total"] == naive_best(PRODUCTS, offers, cfg)
    assert r["evaluated_combinations"] > 0
