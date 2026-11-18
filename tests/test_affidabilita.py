"""Test di affidabilità dei dati: valuta, prezzi in negozio, URL, disponibilità incerta, scansioni
parziali, pubblicazione transazionale, ricalcolo dopo demotion/cambio formato, sicurezza HTTP.

Tutti OFFLINE. Prezzi, farmacie e pagine qui sono SIMULATI.
"""
import gzip
import json
import os
from datetime import date, timedelta

import pytest

import storage
from fakes import FakeResp, make_session
from scrapers.base import (STATUS_OK, STATUS_PRICE_UNVERIFIED, BaseScraper, Offer, UrlCache, _url_key,
                           resolve_shared_urls)
from scrapers.http import FetchError, ResponseTooLarge, UnsafeTarget, safe_gunzip
from scrapers.sitemap import fetch_sitemap_urls
from storage import StorageError, write_json_atomic, write_many_atomic
from validation import history_problems, validate_data

TODAY = date.today()
EAN_P004 = "8029408093058"


# =========================================================================== valuta
class _NoSession:
    def set_domain_delay(self, *a):
        pass


class _Shop(BaseScraper):
    pharmacy_id, name, base_url = "shop", "Shop", "https://www.farmacia-test.it"


def _page(currency=None, meta_currency=None, spec_currency=None, micro_currency=None):
    offer = {"@type": "Offer", "availability": "https://schema.org/InStock"}
    if spec_currency:
        offer["priceSpecification"] = {"@type": "UnitPriceSpecification", "price": 12.5, "priceCurrency": spec_currency}
    else:
        offer["price"] = "12.50"
    if currency:
        offer["priceCurrency"] = currency
    ld = {"@context": "https://schema.org", "@type": "Product", "name": "Ceramol Crema Mani 311 100 ml",
          "brand": {"@type": "Brand", "name": "Ceramol"}, "gtin13": EAN_P004, "offers": offer}
    meta = f'<meta property="product:price:currency" content="{meta_currency}">' if meta_currency else ""
    micro = f'<span itemprop="priceCurrency" content="{micro_currency}"></span>' if micro_currency else ""
    return (f'<html><head>{meta}<script type="application/ld+json">{json.dumps(ld)}</script></head>'
            f'<body><h1>Ceramol Crema Mani 311 100 ml</h1><div itemscope itemtype="https://schema.org/Product">'
            f'{micro}</div></body></html>')


def _evaluate(config, products, html, url="https://www.farmacia-test.it/ceramol-mani-100ml"):
    sc = _Shop(_NoSession(), config, UrlCache(), now_iso="2026-11-18T07:00:00Z")
    return sc.evaluate(products["P004"], sc.parse(html, url), url)


def test_eur_declared_is_usable(config, products):
    o = _evaluate(config, products, _page(currency="EUR", meta_currency="EUR"))
    assert o.status == STATUS_OK and o.usable and o.currency == "EUR"
    assert o.currency_method.startswith("dichiarata")


@pytest.mark.parametrize("kw", [{"currency": "CHF"}, {"currency": "USD"}, {"meta_currency": "USD"},
                                {"micro_currency": "CHF"}, {"spec_currency": "CHF"}])
def test_non_eur_is_never_usable_and_never_converted(config, products, kw):
    o = _evaluate(config, products, _page(**kw))
    assert o.status == STATUS_PRICE_UNVERIFIED and o.price is None and not o.usable
    assert any("non supportata" in n for n in o.notes)


def test_price_specification_currency_eur(config, products):
    o = _evaluate(config, products, _page(spec_currency="EUR"))
    assert o.status == STATUS_OK and o.price == 12.5


def test_discordant_currency_sources(config, products):
    o = _evaluate(config, products, _page(currency="EUR", meta_currency="CHF"))
    assert o.status == STATUS_PRICE_UNVERIFIED and o.currency == "CHF/EUR"
    assert any("fonti discordanti" in n for n in o.notes)


def test_missing_currency_only_on_italian_site(config, products):
    o = _evaluate(config, products, _page())
    assert o.status == STATUS_OK and o.currency_method.startswith("implicita")
    sc = type("Swiss", (BaseScraper,), {"pharmacy_id": "ch", "base_url": "https://www.apotheke-test.ch"})(
        _NoSession(), config, UrlCache(), now_iso="2026-11-18T07:00:00Z")
    url = "https://www.apotheke-test.ch/ceramol"
    o = sc.evaluate(products["P004"], sc.parse(_page(), url), url)
    assert o.status == STATUS_PRICE_UNVERIFIED and not o.usable


# =========================================================================== prezzi in farmacia fisica
def _sp(**kw):
    base = {"store_id": "osm:node/1", "product_id": "P004", "format": "100 ml", "price": 9.9, "available": True,
            "observed_at": (TODAY - timedelta(days=3)).isoformat(), "source": "rilevato di persona"}
    return {**base, **kw}


@pytest.mark.parametrize("entry,reason", [
    (_sp(price=-1), "prezzo non valido"),
    (_sp(price=0), "prezzo non valido"),
    (_sp(price="9,90"), "prezzo non valido"),
    (_sp(format="50 ml"), "formato 50 ml diverso"),
    (_sp(format=None), "formato non indicato"),
    (_sp(observed_at=(TODAY - timedelta(days=45)).isoformat()), "più vecchia di 30 giorni"),
    (_sp(observed_at="ieri"), "observed_at"),
    (_sp(observed_at=(TODAY + timedelta(days=2)).isoformat()), "futuro"),
    (_sp(source=""), "fonte"),
    (_sp(product_id="P999"), "catalogo"),
    (_sp(available=False), "non disponibile"),
])
def test_invalid_store_prices_are_excluded_but_kept(config, entry, reason):
    from physical_pharmacies import split_store_prices
    valid, excluded = split_store_prices([entry], config["products"], max_age_days=30, today=TODAY)
    assert valid == [] and len(excluded) == 1 and reason in excluded[0]["excluded_reason"]
    assert excluded[0]["store_id"] == "osm:node/1"  # la rilevazione resta tracciabile


def test_valid_store_price_accepted(config):
    from physical_pharmacies import split_store_prices
    valid, excluded = split_store_prices([_sp(format="100ml")], config["products"], max_age_days=30, today=TODAY)
    assert len(valid) == 1 and excluded == []


def test_optimizer_ignores_invalid_store_prices_even_if_passed():
    from optimizer import optimize
    products = [{"id": "P001", "format": {"label": "100 ml"}}]
    store = {"id": "s", "name": "S", "distance_km": 0.3, "travel": {"cost": 0.0}}
    phys = {"travel": {"max_distance_km": 5}, "pickup_points": [], "pharmacies": [store],
            "store_prices": [{"store_id": "s", "product_id": "P001", "price": 0, "format": "100 ml"},
                             {"store_id": "s", "product_id": "P001", "price": 3.0, "format": "50 ml"}]}
    r = optimize(products, [], {"pharmacies": {}}, phys)
    assert r["best_overall"] is None and r["products_without_offers"] == ["P001"]


# =========================================================================== normalizzazione URL
def test_url_key_keeps_identifying_params_and_drops_tracking():
    a = "https://www.shop.it/index.php?route=product/product&product_id=1&utm_source=x&gclid=1"
    b = "http://shop.it/index.php?product_id=1&route=product/product&fbclid=2&search=ceramol&page=3"
    assert _url_key(a) == _url_key(b)
    assert _url_key(a) != _url_key(a.replace("product_id=1", "product_id=2"))
    assert _url_key("https://shop.it/p/ceramol/") == _url_key("https://www.shop.it/p/ceramol")
    assert _url_key("https://shop.it/p?sku=1") != _url_key("https://shop.it/p?sku=2")
    assert _url_key("https://shop.it/p?ean=1") != _url_key("https://shop.it/p?minsan=1")
    assert _url_key("https://shop.it/p") != _url_key("https://altro.it/p")


def test_distinct_product_ids_are_not_a_shared_page():
    def off(pid, url):
        return Offer(pid, "f", STATUS_OK, price=10.0, url=url, match_level="NOME_FORMATO", verified=True,
                     availability="DISPONIBILE")
    o3 = off("P003", "https://f.it/index.php?route=product/product&product_id=10")
    o4 = off("P004", "https://f.it/index.php?route=product/product&product_id=11")
    assert resolve_shared_urls([o3, o4]) == [] and o3.usable and o4.usable
    o4b = off("P004", "https://f.it/index.php?product_id=10&route=product/product&utm_medium=x")
    assert resolve_shared_urls([o3, o4b])  # stessa pagina con tracking diverso: esclusa per entrambi
    assert not o3.usable and not o4b.usable


# =========================================================================== disponibilità incerta
def _offer(pid, ph, price, avail="DISPONIBILE"):
    o = Offer(pid, ph, STATUS_OK, price=price, availability=avail, url=f"https://{ph}.it/{pid}", match_level="ID",
              verified=True)
    return o.to_dict()


def _ship(*ids):
    return {"pharmacies": {i: {"standard_cost_eur": 5.0, "reliability": "alta", "verified_at": TODAY.isoformat()}
                           for i in ids}}


PRODS = [{"id": f"P00{i}", "name": f"Prodotto {i}", "format": {"label": "100 ml"}} for i in range(1, 4)]


def test_unknown_availability_is_not_usable_but_uncertain():
    for avail, usable, uncertain in (("DISPONIBILE", True, False), ("SCONOSCIUTA", False, True),
                                     ("NON DISPONIBILE", False, False)):
        d = _offer("P001", "a", 10.0, avail)
        assert (d["usable"], d["availability_uncertain"]) == (usable, uncertain)


def test_verified_cart_never_uses_unknown_availability_and_lists_potential_alternative():
    from optimizer import optimize
    offers = [_offer(p["id"], "a", 10.0) for p in PRODS] + [_offer("P001", "b", 2.0, "SCONOSCIUTA")]
    r = optimize(PRODS, offers, _ship("a", "b"))
    best = r["best_overall"]
    assert best["pharmacies"] == ["a"] and all(i["availability"] == "DISPONIBILE" for i in best["items"])
    alt = r["uncertain_alternatives"]
    assert alt and alt[0]["uncertain_products"] == ["P001"] and alt[0]["reliability"] == "da verificare"
    assert alt[0]["total"] < best["total"]


def test_uncertain_offer_covering_missing_product():
    from optimizer import optimize
    offers = [_offer("P001", "a", 10.0), _offer("P002", "a", 10.0), _offer("P003", "a", 10.0, "SCONOSCIUTA")]
    r = optimize(PRODS, offers, _ship("a"))
    assert r["products_without_offers"] == ["P003"] and r["best_overall"]["total"] == 25.0
    alt = r["uncertain_alternatives"][0]
    assert alt["covers_more_products"] and alt["products_covered"] == 3


def test_legacy_snapshot_usable_with_unknown_availability_is_ignored():
    from optimizer import optimize
    legacy = dict(_offer("P001", "a", 1.0, "SCONOSCIUTA"), usable=True)  # scritto con le regole precedenti
    r = optimize(PRODS[:1], [legacy], _ship("a"))
    assert r["best_overall"] is None


# =========================================================================== scansioni parziali e pubblicazione
def _registry():
    def ph(i):
        return {"id": i, "name": i.title(), "domain": f"{i}.it", "base_url": f"https://{i}.it", "status": "ACTIVE",
                "source": "seed_list_utente", "scraper_status": "OK", "errors": []}
    return {"statuses": ["ACTIVE"], "pharmacies": [ph("alpha"), ph("beta")]}


@pytest.fixture
def orch(tmp_path, monkeypatch, config):
    import scraper as orch
    paths = {k: tmp_path / v.name for k, v in orch.PATHS.items()}
    write_json_atomic(paths["config"], config)
    write_json_atomic(paths["pharmacies"], _registry())
    write_json_atomic(paths["shipping"], _ship("alpha", "beta"))
    monkeypatch.setattr(orch, "PATHS", paths)
    monkeypatch.setattr(orch, "load_physical", lambda cfg: None)
    prices = {"alpha": 10.0, "beta": 12.0}

    def fake_run(entry, session, cfg, cache, now, products, refresh):
        p = prices.get(entry["id"])
        if p is None:
            return entry["id"], [Offer(x["id"], entry["id"], "ERRORE", scraped_at=now, notes=["rete giù"]) for x in products], {}
        return entry["id"], [Offer(x["id"], entry["id"], STATUS_OK, price=p, availability="DISPONIBILE", verified=True,
                                   url=f"https://{entry['id']}.it/{x['id']}", match_level="ID", scraped_at=now)
                             for x in products], {}
    monkeypatch.setattr(orch, "run_pharmacy", fake_run)
    orch.test_paths, orch.test_prices = paths, prices
    return orch


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_full_scan_publishes_everything_with_common_run_id(orch):
    assert orch.main([]) == 0
    p = orch.test_paths
    data, reg, urls = _read(p["data"]), _read(p["pharmacies"]), _read(p["urls"])
    assert data["scope"]["type"] == "completa" and data["run_id"] == reg["updated_by_run"] == urls["updated_by_run"]
    assert len(_read(p["history"])["entries"]) == 10
    assert not validate_data(data, active_ids={"alpha", "beta"})
    assert not [f for f in p["data"].parent.iterdir() if f.name.endswith(".tmp")]


def test_partial_scan_keeps_other_pharmacies_and_history(orch):
    p = orch.test_paths
    assert orch.main([]) == 0
    hist_before = _read(p["history"])["entries"]
    orch.test_prices["alpha"] = 9.0
    assert orch.main(["--pharmacy", "alpha"]) == 0
    data = _read(p["data"])
    assert data["scope"]["type"] == "parziale" and data["scope"]["pharmacies_carried"] == ["beta"]
    by_ph = {(o["pharmacy_id"], o["product_id"]): o for o in data["offers"]}
    assert by_ph[("beta", "P001")]["price"] == 12.0 and by_ph[("beta", "P001")]["carried_from"]
    assert by_ph[("alpha", "P001")]["price"] == 9.0 and not by_ph[("alpha", "P001")].get("carried_from")
    hist = _read(p["history"])["entries"]
    assert len(hist) == len(hist_before)  # stesso giorno: sostituite solo le voci di alpha
    assert {e["price"] for e in hist if e["pharmacy_id"] == "beta"} == {12.0}
    assert {e["price"] for e in hist if e["pharmacy_id"] == "alpha"} == {9.0}


def test_partial_scan_failure_keeps_previous_snapshot(orch):
    p = orch.test_paths
    assert orch.main([]) == 0
    before = _read(p["data"])
    orch.test_prices.pop("alpha")
    assert orch.main(["--pharmacy", "alpha"]) == 2
    after = _read(p["data"])
    assert after["offers"] == before["offers"] and after["last_attempt"]["status"] == "ERRORE"


def test_dry_run_and_product_scan_never_publish_data(orch):
    p = orch.test_paths
    assert orch.main(["--dry-run"]) == 0
    assert not p["data"].exists() and not p["history"].exists()
    with pytest.raises(SystemExit):
        orch.main(["--product", "P003"])  # senza --dry-run è rifiutato
    assert orch.main(["--product", "P003", "--dry-run"]) == 0
    assert not p["data"].exists()


def test_failed_validation_publishes_nothing(orch, monkeypatch):
    p = orch.test_paths
    assert orch.main([]) == 0
    data_before, hist_before = p["data"].read_text(encoding="utf-8"), p["history"].read_text(encoding="utf-8")
    monkeypatch.setattr(orch, "validate_data", lambda data, active_ids=None: ["prezzo non valido (simulato)"])
    orch.test_prices["alpha"] = 1.0
    assert orch.main([]) == 3
    assert p["history"].read_text(encoding="utf-8") == hist_before
    after = _read(p["data"])
    assert after["offers"] == json.loads(data_before)["offers"]
    assert "Validazione fallita" in after["last_attempt"]["message"]


def test_corrupt_history_stops_publication(orch):
    p = orch.test_paths
    assert orch.main([]) == 0
    data_before = p["data"].read_text(encoding="utf-8")
    p["history"].write_text("{corrotto", encoding="utf-8")
    assert orch.main([]) == 3
    assert p["history"].read_text(encoding="utf-8") == "{corrotto"
    assert p["data"].read_text(encoding="utf-8") == data_before


# =========================================================================== scrittura transazionale
def test_write_many_staging_error_changes_nothing(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_json_atomic(a, {"v": 1})
    write_json_atomic(b, {"v": 1})
    with pytest.raises(ValueError):
        write_many_atomic({a: {"v": 2}, b: {"v": float("nan")}})
    assert _read(a) == {"v": 1} and _read(b) == {"v": 1}
    assert sorted(f.name for f in tmp_path.iterdir()) == ["a.json", "b.json"]


def test_write_many_interrupted_publication_reports_what_was_written(tmp_path, monkeypatch):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    write_json_atomic(b, {"v": 1})
    real, calls = os.replace, []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 2:
            raise OSError("disco pieno (simulato)")
        return real(src, dst)
    monkeypatch.setattr(storage.os, "replace", flaky)
    with pytest.raises(StorageError) as exc:
        write_many_atomic({a: {"v": 2}, b: {"v": 2}})
    assert "aggiornati ['a.json']" in str(exc.value) and "b.json" in str(exc.value)
    assert _read(a) == {"v": 2} and _read(b) == {"v": 1}
    assert sorted(f.name for f in tmp_path.iterdir()) == ["a.json", "b.json"]


def test_history_never_shortened():
    old = {"entries": [{"date": "2026-11-17", "product_id": "P001", "pharmacy_id": "a", "price": 1.0, "format": "x"},
                       {"date": "2026-11-18", "product_id": "P001", "pharmacy_id": "a", "price": 1.0, "format": "x"}]}
    assert history_problems(old, {"entries": old["entries"][:1]})
    assert not history_problems(old, old)
    bad = {"entries": [dict(old["entries"][0], price=0)] + old["entries"][1:]}
    assert any("prezzo" in p for p in history_problems(old, bad))


def test_validate_data_rejects_excluded_offers_in_cart():
    off = dict(_offer("P001", "a", 10.0), currency="CHF")
    cart = {"items": [{"product_id": "P001", "seller": "zzz", "channel": "domicilio", "price": 10.0,
                       "availability": "DISPONIBILE"}]}
    problems = validate_data({"offers": [off], "carts": {"best_overall": cart}}, active_ids={"b"})
    text = " ".join(problems)
    assert "EUR" in text and "non ACTIVE" in text and "esclusa" in text


# =========================================================================== demotion e cambio formato
def _snapshot(config):
    from snapshot import recompute
    data = {"offers": [dict(_offer("P001", "alpha", 10.0), format="1000 ml"),
                       dict(_offer("P001", "beta", 8.0), format="1000 ml")], "pharmacies": {}}
    recompute(data, config=config, registry=_registry(), shipping_cfg=_ship("alpha", "beta"), physical=None,
              now="t0", reason="iniziale")
    return data


def test_demotion_removes_offers_from_cart_and_recomputes(config):
    from snapshot import recompute
    data = _snapshot(config)
    assert data["best_prices"]["P001"]["pharmacy_id"] == "beta"
    reg = _registry()
    reg["pharmacies"][1]["status"] = "UNDER_REVIEW"
    s = recompute(data, config=config, registry=reg, shipping_cfg=_ship("alpha", "beta"), physical=None,
                  now="t1", reason="demote beta")
    assert s["offers_moved_to_candidates"] == 1
    assert {o["pharmacy_id"] for o in data["offers"]} == {"alpha"}
    assert data["best_prices"]["P001"]["pharmacy_id"] == "alpha"
    assert data["carts"]["best_overall"]["pharmacies"] == ["alpha"]
    cand = [c for c in data["candidate_offers"] if c["pharmacy_id"] == "beta"]
    assert cand and not cand[0]["usable"] and cand[0]["reliability"]
    assert not validate_data(data, active_ids={"alpha"})
    again = json.dumps(data["carts"]["best_overall"], sort_keys=True)
    recompute(data, config=config, registry=reg, shipping_cfg=_ship("alpha", "beta"), physical=None,
              now="t2", reason="ripetuto")
    assert json.dumps(data["carts"]["best_overall"], sort_keys=True) == again  # idempotente


def test_demote_command_recomputes_data_json(tmp_path, monkeypatch, config):
    import pharmacy_discovery as pd
    data = _snapshot(config)
    path = tmp_path / "data.json"
    write_json_atomic(path, data)
    write_json_atomic(tmp_path / "shipping.json", _ship("alpha", "beta"))
    monkeypatch.setattr(pd, "SHIPPING_PATH", tmp_path / "shipping.json")
    monkeypatch.setattr("physical_pharmacies.load_physical", lambda cfg, cache=None: None)
    reg = _registry()
    assert pd.demote(reg, "beta.it")[0]
    msg = pd.recompute_data_after_status_change(reg, config, "demote beta", path=path)
    assert "1 passate tra le candidate" in msg
    assert {o["pharmacy_id"] for o in _read(path)["offers"]} == {"alpha"}


def test_format_change_recomputes_even_when_no_offer_is_left(config):
    from snapshot import recompute
    cfg = json.loads(json.dumps(config))
    p1 = cfg["products"][0]
    data = _snapshot(cfg)
    p1["format_history"] = [{"label": p1["format"]["label"], "until": "2026-11-18"}]
    p1["format"] = {"label": "500 ml", "quantity": 500, "unit": "ml"}
    s = recompute(data, config=cfg, registry=_registry(), shipping_cfg=_ship("alpha", "beta"), physical=None,
                  now="t1", reason="cambio formato")
    assert s["offers_removed_old_format"] == 2 and data["offers"] == []
    assert data["carts"]["best_overall"] is None and "P001" in data["carts"]["products_without_offers"]
    assert data["best_prices"]["P001"] is None and data["products"][0]["format"]["label"] == "500 ml"


# =========================================================================== sicurezza HTTP e sitemap
def test_redirect_to_other_domain_is_blocked():
    s, fake, _ = make_session({"https://f.it/p": FakeResp(302, b"", headers={"location": "https://evil.example/p"})})
    with pytest.raises(UnsafeTarget):
        s.get("https://f.it/p", check_robots=False)
    assert "https://evil.example/p" not in fake.calls


def test_same_site_redirects_are_followed_and_recorded():
    s, fake, _ = make_session({
        "http://f.it/p": FakeResp(301, b"", headers={"location": "https://www.f.it/p"}),
        "https://www.f.it/p": FakeResp(302, b"", headers={"location": "/prodotto"}),
        "https://www.f.it/prodotto": FakeResp(200, b"<html>ok</html>"),
    })
    res = s.get("http://f.it/p", check_robots=False)
    assert res.ok and res.final_url == "https://www.f.it/prodotto" and len(res.redirects) == 2


def test_redirect_loop_or_too_many_redirects():
    s, _, _ = make_session({"https://f.it/a": FakeResp(302, b"", headers={"location": "https://f.it/b"}),
                            "https://f.it/b": FakeResp(302, b"", headers={"location": "https://f.it/a"})},
                           max_redirects=3)
    with pytest.raises(FetchError, match="troppi redirect"):
        s.get("https://f.it/a", check_robots=False)


@pytest.mark.parametrize("url", ["http://127.0.0.1/x", "http://10.1.2.3/x", "http://[::1]/x", "http://localhost/x",
                                 "https://intranet.farmacia-test.it/x", "file:///etc/passwd"])
def test_private_or_local_targets_are_blocked(url):
    s, fake, _ = make_session({})
    with pytest.raises(UnsafeTarget):
        s.get(url, check_robots=False)
    assert fake.calls == []


def test_too_large_responses_are_rejected():
    s, _, _ = make_session({"https://f.it/big": FakeResp(200, b"x" * 2048),
                            "https://f.it/declared": FakeResp(200, b"x", headers={"content-length": "999999"})},
                           max_response_bytes=1024)
    for url in ("https://f.it/big", "https://f.it/declared"):
        with pytest.raises(ResponseTooLarge):
            s.get(url, check_robots=False)


def test_scraper_never_fetches_outside_its_domain(config):
    sc = _Shop(_NoSession(), config, UrlCache(), now_iso="2026-11-18T07:00:00Z")
    with pytest.raises(UnsafeTarget):
        sc.fetch("https://altro-sito.it/sitemap.xml")


def test_gzip_bomb_is_stopped():
    bomb = gzip.compress(b"0" * 200_000)
    with pytest.raises(ResponseTooLarge):
        safe_gunzip(bomb, limit=10_000)
    assert safe_gunzip(gzip.compress(b"ok")) == b"ok"


def _index(*children):
    return ("<sitemapindex>" + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children) + "</sitemapindex>").encode()


def test_recursive_and_deep_sitemaps_terminate():
    pages = {"https://f.it/a.xml": _index("https://f.it/b.xml"),
             "https://f.it/b.xml": _index("https://f.it/a.xml", "https://f.it/c.xml"),
             "https://f.it/c.xml": b"<urlset><url><loc>https://f.it/ceramol</loc></url></urlset>"}
    urls, errors = fetch_sitemap_urls(pages.__getitem__, ["https://f.it/a.xml"], skip_child=None)
    assert urls == ["https://f.it/ceramol"]
    deep = {f"https://f.it/{i}.xml": _index(f"https://f.it/{i + 1}.xml") for i in range(10)}
    urls, errors = fetch_sitemap_urls(deep.__getitem__, ["https://f.it/0.xml"], skip_child=None, max_depth=3)
    assert urls == [] and any("annidato" in e for e in errors)
    assert len([u for u in deep if u]) == 10  # nessuna lettura oltre la profondità massima
