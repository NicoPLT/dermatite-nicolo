"""Test dello scraper generico JSON-LD, del probe e della promozione manuale.

Tutto offline: un finto sito (https://www.farmatest.it) servito da tests/fakes.py, con pagine
SIMULATE in tests/fixtures/jsonld/. La PoliteSession è quella reale (robots, ritmo, tetto).
"""
import json
from urllib.parse import quote_plus

import pytest

from fakes import FakeResp, make_session
from scrapers.base import STATUS_NO_STRUCTURED, STATUS_OK, STATUS_PRICE_UNVERIFIED, UrlCache
from scrapers.extract import AVAILABLE, UNAVAILABLE, extract_page, ld_variants, loads_jsonld, main_product_ld, iter_jsonld, soup_of
from scrapers.generic_jsonld import (CANDIDATE_RELIABILITY, COMPAT_BLOCKED, COMPAT_FULL, COMPAT_NONE, COMPAT_PARTIAL,
                                     GenericJsonLdScraper, apply_probe, candidate_offers_from_registry, compatibility,
                                     opensearch_to_template, probe, probe_markdown)
from scrapers.matcher import MATCH_ID, MATCH_NAME
from conftest import FIXTURES

JL = FIXTURES / "jsonld"
BASE = "https://www.farmatest.it"
ROBOTS = (b"User-agent: *\nDisallow: /privato/\nDisallow: /ricerca\n"
          b"Sitemap: https://www.farmatest.it/sitemap_index.xml\n")


def body(name: str) -> bytes:
    return (JL / name).read_bytes()


def route(b: bytes, status: int = 200, headers=None):
    return lambda: FakeResp(status, b, dict(headers or {}))


def entry(**kw):
    e = {"id": "farmatest", "name": "Farmacia Test", "domain": "farmatest.it", "base_url": BASE, "status": "DISCOVERED",
         "source": "test", "scraper_status": "NO_SCRAPER", "errors": []}
    e.update(kw)
    return e


def site_routes(**override):
    r = {
        f"{BASE}/robots.txt": route(ROBOTS),
        f"{BASE}/sitemap_index.xml": route(body("sitemap_index.xml")),
        f"{BASE}/sitemap_products.xml": route(body("sitemap_products.xml")),
        f"{BASE}/ceramol-crema-mani-311-100-ml": route(body("simple_offer.html")),
        f"{BASE}/ceramol-lipocrema-311-gruppo": route(body("product_group.html")),
        f"{BASE}/uriage-xemose-olio-detergente-1-l": route(body("out_of_stock.html")),
        f"{BASE}/lipikar-baume-ap-m-400-ml": route(body("no_structured.html")),
        f"{BASE}/anthelios-uvmune-400-fluido-invisibile-50-ml": route(body("unperfumed.html")),
    }
    r.update(override)
    return r


def cfg_with(config, **generic):
    c = json.loads(json.dumps(config))
    c["generic_jsonld"] = {**c.get("generic_jsonld", {}), **generic}
    return c


def scraper_for(config, routes=None):
    s, fake, _ = make_session(routes or {})
    return GenericJsonLdScraper(entry(), s, config, UrlCache(), now_iso="2026-11-18T10:00:00Z"), fake


def evaluate_fixture(config, product, name, url=f"{BASE}/pagina"):
    sc, _ = scraper_for(config)
    page = sc.parse((JL / name).read_text(encoding="utf-8"), url)
    return sc.evaluate(product, page, url)


# =========================================================== parsing JSON-LD robusto
def test_malformed_jsonld_is_tolerated():
    soup = soup_of((JL / "malformed.html").read_text(encoding="utf-8"))
    items = list(iter_jsonld(soup))  # il secondo blocco è irrecuperabile: ignorato senza eccezioni
    prod = main_product_ld(items, f"{BASE}/x")
    assert prod["gtin13"] == "8029408093058"
    assert prod["offers"]["url"] == "https://www.farmatest.it/a//b"  # "//" dentro una stringa NON è un commento
    assert loads_jsonld('{"a": 1,}') == {"a": 1}
    assert loads_jsonld("{ rotto") is None


def test_graph_itemlist_main_product_is_not_the_related_one():
    page = extract_page((JL / "graph_itemlist.html").read_text(encoding="utf-8"), f"{BASE}/uriage-xemose-olio-detergente-1-l",
                        allow_text_fallback=False)
    assert page.eans == {"3661434003370"} and page.price_sources == {"json_ld": 16.99}


def test_product_group_variants_extracted():
    soup = soup_of((JL / "product_group.html").read_text(encoding="utf-8"))
    vs = ld_variants(main_product_ld(list(iter_jsonld(soup)), f"{BASE}/g"))
    assert [(v.name, v.price, sorted(v.eans)) for v in vs] == [
        ("Ceramol Lipocrema 311 50 ml", 11.28, ["8029408107021"]), ("Ceramol Lipocrema 311 100 ml", 16.10, ["8029408107014"])]


def test_opensearch_template_cleanup():
    t = opensearch_to_template("https://www.farmatest.it/cerca?lang={language?}&testo={searchTerms}&pagina={startPage?}")
    assert t == "https://www.farmatest.it/cerca?testo={q}"


# =========================================================== valutazione pagina (fixture)
def test_simple_offer_comma_price_and_shipping(config, products):
    o = evaluate_fixture(config, products["P004"], "simple_offer.html")
    assert o.status == STATUS_OK and o.price == 12.90 and o.match_level == MATCH_ID and o.availability == AVAILABLE
    assert o.shipping_jsonld == [{"cost": 4.9, "currency": "EUR", "country": "IT"}]


def test_price_specification(config, products):
    o = evaluate_fixture(config, products["P003"], "price_specification.html")
    assert o.status == STATUS_OK and o.price == 18.73


def test_member_tier_price_is_not_the_public_price():
    """Struttura osservata su 1000farmacie.it: prezzo pubblico + prezzo riservato agli iscritti."""
    from scrapers.extract import ld_price_and_availability
    offer = {"@type": "Offer", "priceSpecification": [
        {"@type": "PriceSpecification", "priceCurrency": "EUR", "price": "15.89"},
        {"@type": "PriceSpecification", "priceCurrency": "EUR", "price": "14.78",
         "validForMemberTier": {"@type": "MemberProgramTier", "@id": "https://x.it/care-plan"}}]}
    assert ld_price_and_availability({"offers": offer})[0] == 15.89
    listino = {"@type": "Offer", "priceSpecification": [
        {"@type": "UnitPriceSpecification", "price": "19.90", "priceType": "https://schema.org/StrikethroughPrice"},
        {"@type": "UnitPriceSpecification", "price": "15.90"}]}
    assert ld_price_and_availability({"offers": listino})[0] == 15.90
    two_public = {"@type": "Offer", "priceSpecification": [{"price": "15.89"}, {"price": "12.00"}]}
    assert ld_price_and_availability({"offers": two_public})[0] is None  # ambiguo: mai indovinare


def test_product_group_picks_the_100ml_variant(config, products):
    o = evaluate_fixture(config, products["P003"], "product_group.html")
    # meta e h1 indicano la variante preselezionata 50 ml (11,28 €): non vanno usati
    assert o.status == STATUS_OK and o.price == 16.10 and o.match_level == MATCH_ID
    assert o.variant == "Ceramol Lipocrema 311 100 ml"


def test_variants_without_ean_chosen_by_format(config, products):
    o = evaluate_fixture(config, products["P003"], "product_group_no_ean.html")
    assert o.status == STATUS_OK and o.price == 16.10 and o.match_level == MATCH_NAME and o.availability == UNAVAILABLE
    assert not o.usable  # esaurito: mai nel carrello


def test_aggregate_offer_single_vs_range(config, products):
    single = evaluate_fixture(config, products["P005"], "aggregate_single.html")
    assert single.status == STATUS_OK and single.price == 13.49
    rng = evaluate_fixture(config, products["P005"], "aggregate_range.html")
    assert rng.status == STATUS_PRICE_UNVERIFIED and rng.price is None  # lowPrice di un'altra variante: niente ripiego su meta


def test_non_eur_is_discarded(config, products):
    o = evaluate_fixture(config, products["P004"], "non_eur.html")
    assert o.status == STATUS_PRICE_UNVERIFIED and o.price is None and any("CHF" in n for n in o.notes)


def test_ean_mismatch_multipack_perfumed_rejected(config, products):
    assert not evaluate_fixture(config, products["P004"], "ean_mismatch.html").usable
    assert evaluate_fixture(config, products["P004"], "ean_mismatch.html").match_level == "ID_DIVERSO"
    assert evaluate_fixture(config, products["P004"], "multipack.html").match_level == "MULTIPACK_O_BUNDLE"
    assert not evaluate_fixture(config, products["P005"], "perfumed.html").usable
    ok = evaluate_fixture(config, products["P005"], "unperfumed.html")
    assert ok.status == STATUS_OK and ok.match_level == MATCH_NAME


def test_out_of_stock(config, products):
    o = evaluate_fixture(config, products["P001"], "out_of_stock.html")
    assert o.status == STATUS_OK and o.availability == UNAVAILABLE and not o.usable


def test_no_structured_data_never_reads_visible_text(config, products):
    o = evaluate_fixture(config, products["P002"], "no_structured.html")
    assert o.status == STATUS_NO_STRUCTURED and o.price is None  # il "€ 22,90" visibile è ignorato


# =========================================================== probe su sito finto
def test_probe_full_site(config):
    s, fake, _ = make_session(site_routes())
    rep, offers = probe(entry(), s, config, config["products"], "2026-11-18T10:00:00Z")
    by = {o.product_id: o for o in offers}
    assert rep["compatibility"] == COMPAT_FULL
    assert by["P004"].price == 12.90 and by["P003"].price == 16.10 and by["P005"].match_level == MATCH_NAME
    assert by["P001"].availability == UNAVAILABLE and by["P002"].status == STATUS_NO_STRUCTURED
    assert rep["products_found"] == ["P001", "P003", "P004", "P005"]
    assert rep["requests"] <= 25 and len(fake.calls) == rep["requests"]
    assert f"{BASE}/sitemap_blog.xml" not in fake.calls                     # sitemap non di prodotto saltata
    assert f"{BASE}/ceramol-lipocrema-311-50-ml" not in fake.calls           # formato sbagliato già dall'URL
    assert not any("/privato/" in c for c in fake.calls)                     # vietato da robots: mai richiesto
    md = probe_markdown(rep, config["products"])
    assert "Compatibilità: FULL" in md and "16,10 €" in md


def test_robots_disallowed_product_is_never_requested(config, products):
    sm = b"<urlset><url><loc>https://www.farmatest.it/privato/ceramol-crema-mani-311-100-ml-riservato</loc></url></urlset>"
    routes = site_routes(**{f"{BASE}/sitemap_index.xml": route(sm)})
    s, fake, _ = make_session(routes)
    rep, offers = probe(entry(), s, config, [products["P004"]], "2026-11-18T10:00:00Z")
    assert not any("/privato/" in c for c in fake.calls)
    assert offers[0].status != STATUS_OK


def test_request_budget_is_respected(config):
    c = cfg_with(config, max_requests_per_domain=4)
    s, fake, _ = make_session(site_routes())
    rep, _ = probe(entry(), s, c, c["products"], "2026-11-18T10:00:00Z")
    assert len(fake.calls) <= 4 and rep["budget_exhausted"]


@pytest.mark.parametrize("resp", [
    FakeResp(403, b"<html><title>Access Denied</title> errors.edgesuite.net</html>"),
    FakeResp(429, b"local_rate_limited", {"retry-after": "3600"}),
])
def test_403_or_429_means_blocked(config, resp):
    s, fake, _ = make_session({f"{BASE}/robots.txt": lambda: FakeResp(resp.status_code, resp.content, dict(resp.headers))})
    e = entry()
    rep, offers = probe(e, s, config, config["products"], "2026-11-18T10:00:00Z")
    assert rep["compatibility"] == COMPAT_BLOCKED and len(fake.calls) <= 3
    apply_probe(e, rep)
    assert e["status"] == "BLOCKED" and e["jsonld_compatibility"] == COMPAT_BLOCKED


def test_search_via_opensearch_and_template_saved(config, products):
    p4 = products["P004"]
    q = quote_plus(p4["search_queries"][0])
    routes = {
        f"{BASE}/robots.txt": route(b"User-agent: *\nDisallow:\n"),
        f"{BASE}/": route(body("home_opensearch.html")),
        f"{BASE}/opensearch.xml": route(body("opensearch.xml")),
        f"{BASE}/cerca?testo={q}": route(body("search_results.html")),
        f"{BASE}/p/123": route(body("simple_offer.html")),
    }
    s, fake, _ = make_session(routes)
    e = entry()
    rep, offers = probe(e, s, config, [p4], "2026-11-18T10:00:00Z")
    assert rep["search_url_template"] == f"{BASE}/cerca?testo={{q}}"
    assert offers[0].status == STATUS_OK and offers[0].url == f"{BASE}/p/123"
    assert "https://altro-sito.it/p/9" not in fake.calls and f"{BASE}/p/124" not in fake.calls
    apply_probe(e, rep)
    assert e["search_url_template"] == f"{BASE}/cerca?testo={{q}}" and e["status"] == "UNDER_REVIEW"


def test_disallowed_search_pattern_is_skipped(config, products):
    routes = {
        f"{BASE}/robots.txt": route(b"User-agent: *\nDisallow: /search\nDisallow: /cerca\n"),
        f"{BASE}/": route(body("home_opensearch.html")),
        f"{BASE}/opensearch.xml": route(body("opensearch.xml")),
    }
    s, fake, _ = make_session(routes)
    probe(entry(), s, config, [products["P004"]], "2026-11-18T10:00:00Z")
    assert not any("/cerca?" in c or "/search?" in c for c in fake.calls)


def test_compatibility_levels():
    from scrapers.base import Offer
    full = Offer("P004", "x", STATUS_OK, price=10.0, verified=True, match_level=MATCH_ID, availability=AVAILABLE)
    name = Offer("P005", "x", STATUS_OK, price=10.0, verified=True, match_level=MATCH_NAME, availability=AVAILABLE)
    nodata = Offer("P002", "x", STATUS_NO_STRUCTURED)
    assert compatibility([full, nodata], None)[0] == COMPAT_FULL
    assert compatibility([name], None)[0] == COMPAT_PARTIAL
    assert compatibility([nodata], None)[0] == COMPAT_NONE
    assert compatibility([full], "HTTP 403")[0] == COMPAT_BLOCKED


# =========================================================== registro, candidate_offers, dispatcher
def _probed_entry(config, compat=COMPAT_FULL, at=None):
    from storage import now_utc_iso
    s, _, _ = make_session(site_routes())
    e = entry(status="UNDER_REVIEW")
    rep, _ = probe(e, s, config, config["products"], at or now_utc_iso())
    apply_probe(e, rep)
    e["jsonld_compatibility"] = compat
    return e


def test_candidate_offers_only_from_non_active(config):
    e = _probed_entry(config)
    reg = {"pharmacies": [e]}
    cands = candidate_offers_from_registry(reg)
    assert cands and all(c["reliability"] == CANDIDATE_RELIABILITY and c["usable"] is False for c in cands)
    e["status"] = "ACTIVE"
    assert candidate_offers_from_registry(reg) == []


def test_build_data_keeps_non_active_out_of_cart_and_history(config):
    import scraper as orch
    from scrapers.base import Offer
    reg = {"pharmacies": [{"id": "attiva", "name": "Attiva", "status": "ACTIVE"},
                          {"id": "nuova", "name": "Nuova", "status": "UNDER_REVIEW"}]}
    offers = [Offer("P004", "attiva", STATUS_OK, price=12.0, verified=True, match_level=MATCH_ID, availability=AVAILABLE),
              Offer("P004", "nuova", STATUS_OK, price=5.0, verified=True, match_level=MATCH_ID, availability=AVAILABLE)]
    results = {"attiva": ([offers[0]], {}), "nuova": ([offers[1]], {})}
    data = orch.build_data(config, reg, {"pharmacies": {}}, offers, results, None, "2026-11-18T10:00:00Z", 0.0, True)
    assert [o["pharmacy_id"] for o in data["offers"]] == ["attiva"]
    assert data["best_prices"]["P004"]["pharmacy_id"] == "attiva"           # 5 € non verificati NON vincono
    assert [c["pharmacy_id"] for c in data["candidate_offers"]] == ["nuova"]
    assert data["candidate_offers"][0]["reliability"] == CANDIDATE_RELIABILITY
    assert all(h["pharmacy_id"] == "attiva" for h in orch.history_entries(data["offers"], "2026-11-18"))


def test_dispatcher(config):
    import scraper as orch
    s, _, _ = make_session({})
    assert type(orch.make_scraper({"id": "clubfarma"}, s, config, UrlCache(), "t")).__name__ == "ClubfarmaScraper"
    g = orch.make_scraper(entry(scraper="generic_jsonld", status="ACTIVE"), s, config, UrlCache(), "t")
    assert isinstance(g, GenericJsonLdScraper)
    assert orch.make_scraper(entry(), s, config, UrlCache(), "t") is None


# =========================================================== promozione manuale
def test_promote_refused_without_valid_probe(config):
    from pharmacy_discovery import promote
    s, _, _ = make_session(site_routes())
    reg = {"pharmacies": [entry(status="UNDER_REVIEW")]}
    ok, msg = promote(reg, {"pharmacies": {}}, "farmatest.it", s, config, input_fn=lambda _: "farmatest.it")
    assert not ok and "compatibilità" in msg
    reg = {"pharmacies": [_probed_entry(config, at="2026-01-01T00:00:00Z")]}  # probe vecchio
    ok, msg = promote(reg, {"pharmacies": {}}, "farmatest.it", s, config, input_fn=lambda _: "farmatest.it")
    assert not ok and "giorni" in msg
    reg = {"pharmacies": [_probed_entry(config, compat=COMPAT_NONE)]}
    ok, msg = promote(reg, {"pharmacies": {}}, "farmatest.it", s, config, input_fn=lambda _: "farmatest.it")
    assert not ok and reg["pharmacies"][0]["status"] == "UNDER_REVIEW"


def test_promote_requires_explicit_confirmation_and_creates_unverified_shipping(config):
    from pharmacy_discovery import demote, promote
    s, _, _ = make_session(site_routes())
    e = _probed_entry(config)
    reg, ship = {"pharmacies": [e]}, {"pharmacies": {}}
    ok, msg = promote(reg, ship, "farmatest.it", s, config, input_fn=lambda _: "no")
    assert not ok and e["status"] == "UNDER_REVIEW"
    ok, msg = promote(reg, ship, "farmatest.it", s, config, input_fn=lambda _: "farmatest.it")
    assert ok and e["status"] == "ACTIVE" and e["scraper"] == "generic_jsonld"
    sh = ship["pharmacies"]["farmatest"]
    assert sh["standard_cost_eur"] is None and sh["reliability"] == "NON_VERIFICATA"
    assert sh["jsonld_shipping_hint"] == [{"cost": 4.9, "currency": "EUR", "country": "IT"}]  # solo indizio, non usato
    ok, _ = demote(reg, "farmatest.it")
    assert ok and e["status"] == "UNDER_REVIEW"


def test_promote_refused_if_robots_now_forbids(config):
    from pharmacy_discovery import promote
    e = _probed_entry(config)
    s2, _, _ = make_session({f"{BASE}/robots.txt": route(b"User-agent: *\nDisallow: /\n")})
    ok, msg = promote({"pharmacies": [e]}, {"pharmacies": {}}, "farmatest.it", s2, config, input_fn=lambda _: "farmatest.it")
    assert not ok and "robots.txt" in msg


def test_discovery_probes_at_most_m_new_sites(config, monkeypatch):
    import pharmacy_discovery as pd
    calls = []

    def fake_probe(e, session, cfg, products, now):
        calls.append(e["domain"])
        return ({"domain": e["domain"], "pharmacy_id": e["id"], "at": now, "compatibility": COMPAT_NONE, "reason": "test",
                 "requests": 1, "budget": 25, "budget_exhausted": False, "search_url_template": None, "sitemaps_used": [],
                 "paths_used": [], "urls_fetched": [], "products_found": [], "products_not_found": [], "examples": [],
                 "offers": [], "errors": []}, [])
    monkeypatch.setattr("scrapers.generic_jsonld.probe", fake_probe)
    monkeypatch.setattr("scrapers.generic_jsonld.write_probe_markdown", lambda *a, **k: None)
    reg = {"pharmacies": [entry(id=f"s{i}", domain=f"s{i}.it", base_url=f"https://s{i}.it") for i in range(8)]}
    out = pd.probe_new_sites(reg, None, config, limit=5)
    assert len(calls) == 5 and len(out) == 5
    assert all(p["status"] != "ACTIVE" for p in reg["pharmacies"])  # la scoperta non promuove mai


def test_dashboard_shows_candidates_separately():
    from conftest import ROOT
    market_ui = (ROOT / "assets" / "secondary.js").read_text(encoding="utf-8")
    assert "candidate_offers" in market_ui and "non verificate, non incluse nel carrello" in market_ui and "In valutazione" in market_ui
