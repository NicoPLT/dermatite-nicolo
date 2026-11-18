"""Test di estrazione, identificazione prodotto/formato, robots, sitemap, HTTP e flusso scraper."""
import gzip

import pytest

from scrapers.base import (STATUS_BLOCKED, STATUS_MATCH_UNRELIABLE, STATUS_NOT_FOUND, STATUS_OK,
                           STATUS_PRICE_UNVERIFIED, STATUS_RATE_LIMITED, BaseScraper, UrlCache, clean_result_url)
from scrapers.extract import (AVAILABLE, UNAVAILABLE, UNKNOWN, extract_page, gtin_valid, ids_from_url,
                              normalize_availability, normalize_minsan, parse_price, resolve_availability,
                              resolve_price)
from scrapers.http import DomainBlocked, DomainRateLimited, FetchError, PoliteSession, RobotsDisallowed
from scrapers.matcher import (MATCH_ID, MATCH_NAME, REJECT_BUNDLE, REJECT_FORMAT, REJECT_FORMAT_UNKNOWN,
                              REJECT_ID_CONTRADICTION, REJECT_ID_DIFFERENT, REJECT_VARIANT, match_product,
                              normalize, volumes_ml)
from scrapers.extract import PageData
from scrapers.robots import parse_robots, policy_from_response
from scrapers.sitemap import fetch_sitemap_urls, parse_sitemap, rank_candidates


# ============================================================== prezzi
@pytest.mark.parametrize("raw,expected", [
    ("12,09 €", 12.09), ("€ 17,99", 17.99), ("€ 1.234,50", 1234.5), ("12.09", 12.09), (13.59, 13.59),
    ("Prezzo Shop 10,42\xa0€ Prezzo di listino 15,10 €", 10.42), ("13,59 € -10%", 13.59), ("29.90 EUR", 29.9),
])
def test_parse_price_valid(raw, expected):
    assert parse_price(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "gratis", "0", 0, "0,00 €", -3, "N/D", True])
def test_parse_price_never_zero_or_invented(raw):
    assert parse_price(raw) is None


# ============================================================== disponibilità e identificativi
@pytest.mark.parametrize("raw,expected", [
    ("https://schema.org/InStock", AVAILABLE), ("http://schema.org/OutOfStock", UNAVAILABLE), ("instock", AVAILABLE),
    ("Non più disponibile", UNAVAILABLE), ("Prodotto non disponibile", UNAVAILABLE), ("Disponibilità limitata", AVAILABLE),
    ("Quantità Aggiungi al carrello", AVAILABLE), ("Esaurito", UNAVAILABLE), ("PreOrder", UNAVAILABLE), ("", None),
])
def test_normalize_availability(raw, expected):
    assert normalize_availability(raw) == expected


def test_gtin_and_minsan_normalization():
    assert gtin_valid("8029408093058")
    assert not gtin_valid("8029408093059")          # checksum errato
    assert normalize_minsan("IT977658020") == "977658020"
    assert normalize_minsan("77658020") is None       # 8 cifre senza etichetta: rifiutato
    assert normalize_minsan("77658020", labeled=True) == "077658020"
    assert normalize_minsan("IT7270") is None


def test_ids_from_url():
    eans, minsans = ids_from_url("https://x.it/creme/26683-ceramol-crema-mani-100ml-8029408093058.html")
    assert eans == {"8029408093058"} and not minsans
    eans, minsans = ids_from_url("https://www.redcare.it/cosmetici-igiene/IT977658020/ceramol-311-crema-mani.htm")
    assert minsans == {"977658020"}


# ============================================================== JSON-LD / microdata / meta
def test_jsonld_prestashop_extraction(fx):
    page = extract_page(fx("prestashop_product.html"), "https://www.farmacia-test.it/creme/26683-ceramol-crema-mani-100ml.html",
                        price_css=[".current-price"], availability_css=["#product-availability", ".product-add-to-cart"])
    assert page.title == "Ceramol Crema Mani 100ml"
    assert page.eans == {"8029408093058"} and page.minsans == {"977658020"}
    assert page.price_sources == {"json_ld": 13.59, "meta": 13.59, "css": 13.59}
    assert page.availability_sources["json_ld"] == AVAILABLE
    assert page.availability_sources["css"] == AVAILABLE
    assert page.brand == "Ceramol"


def test_jsonld_graph_ambiguous_offers_and_broken_block(fx):
    page = extract_page(fx("graph_productgroup.html"), "https://x.it/p")
    assert "json_ld" not in page.price_sources  # due offerte con prezzi diversi: mai indovinare
    assert any("ambiguo" in n for n in page.notes)
    assert page.eans == {"8029408107021"}


def test_shopware_conflict_majority_wins(fx):
    page = extract_page(fx("shopware_conflict.html"), "https://shop-test.it/ceramol-crema-mani-311-100ml-977658020",
                        price_css=[".product-detail-price"], availability_css=[".delivery-information"])
    assert page.price_sources["json_ld"] == 9.83
    res = resolve_price(page.price_sources)
    assert res.value == 10.42 and res.verified and res.conflict
    assert "json_ld=9.83" in res.method
    assert resolve_availability(page.availability_sources).value == UNAVAILABLE


def test_resolve_price_tie_and_text_only():
    tie = resolve_price({"json_ld": 9.83, "css": 10.42})
    assert tie.value is None and not tie.verified and tie.conflict
    text = resolve_price({"text": 12.0})
    assert text.value == 12.0 and not text.verified
    assert resolve_price({}).value is None


def test_resolve_availability_conflict_is_unknown():
    assert resolve_availability({"meta": AVAILABLE, "css": UNAVAILABLE}).value == UNKNOWN


def test_unavailable_page_wrong_meta(fx):
    page = extract_page(fx("unavailable_no_price.html"), "https://x.it/lipikar",
                        availability_css=["#product-availability", ".product-add-to-cart", ".product-actions"])
    assert page.price_sources == {}
    assert page.availability_sources == {"meta": AVAILABLE, "css": UNAVAILABLE}
    assert resolve_availability(page.availability_sources).value == UNKNOWN


def test_opencart_no_structured_data_reference_minsan(fx):
    page = extract_page(fx("opencart_noid.html"), "https://fv.it/xemose-olio-detergente-1-l",
                        price_css=["ul.price-product .new-price", "ul.price-product li"],
                        availability_css=["#button-cart"], reference_is_minsan=True)
    assert page.price_sources == {"css": 17.99}  # prezzo scontato, non quello barrato
    assert page.minsans == {"927291005"}
    page2 = extract_page(fx("opencart_noid.html"), "https://fv.it/x", price_css=["ul.price-product .new-price"])
    assert page2.minsans == set()  # senza reference_is_minsan il "codice articolo" non è un Minsan


# ============================================================== matcher
def _page(title, eans=(), minsans=(), brand=None):
    return PageData(url="https://x.it/p", titles={"h1": title}, eans=set(eans), minsans=set(minsans), brand=brand)


def test_volumes_and_normalization():
    assert volumes_ml("XEMOSE OLIO DETERGENTE 1 L") == {1000}
    assert volumes_ml("Anthelios UVMune 400 SPF50+ 50 ml") == {50}
    assert volumes_ml("Ceramol Lipocrema 311 50ml") == {50}
    assert volumes_ml("Ceramol 311 Crema Mani") == set()
    assert volumes_ml("uriage-xemose-olio-1-litro") == {1000}
    assert "apm" in normalize("Lipikar Baume AP+M").split()
    assert "apmax" in normalize("Lipikar Baume AP+MAX").split()
    assert "senzaprofumo" in normalize("Fluido SPF50+ Senza Profumo").split()


def test_match_by_ean(products, bundle_terms):
    r = match_product(products["P004"], _page("Ceramol 311 Crema Mani", eans={"8029408093058"}), bundle_terms)
    assert r.level == MATCH_ID and r.accepted and r.reliability == "alta"


def test_match_by_minsan_even_without_name(products, bundle_terms):
    page = _page("La Roche-Posay Lipikar Crema corpo Balsamo riparatore tripla azione 400 ml", minsans={"979265434"})
    assert match_product(products["P002"], page, bundle_terms).level == MATCH_ID


def test_reject_200ml_with_different_minsan(products, bundle_terms):
    page = _page("La Roche-Posay Lipikar Baume AP+M 200 ml", minsans={"981401019"})
    r = match_product(products["P002"], page, bundle_terms)
    assert not r.accepted and r.level == REJECT_ID_DIFFERENT


def test_reject_lipocrema_alternate_minsan(products, bundle_terms):
    # seconda "Lipocrema 100 ml" su verafarma: Minsan diverso, nessun EAN -> conflitto di identificativo
    page = _page("CERAMOL LIPOCREMA 311 100ML", minsans={"977658107"})
    assert match_product(products["P003"], page, bundle_terms).level == REJECT_ID_DIFFERENT


# ------------------------------------------------------------- P003: cambio formato 50 ml -> 100 ml
def test_p003_catalog_is_100ml(products):
    p = products["P003"]
    assert p["format"]["quantity"] == 100 and p["format"]["label"] == "100 ml"
    assert p["ean"] == "8029408107014" and p["minsan"] == "980512762"
    assert p["format_history"][-1]["label"] == "50 ml"


def test_p003_100ml_accepted_by_id(products, bundle_terms, fx):
    page = extract_page(fx("lipocrema_100ml.html"), "https://www.farmacia-test.it/p/50757-ceramol-lipocrema-311-100ml",
                        price_css=[".current-price"])
    r = match_product(products["P003"], page, bundle_terms)
    assert r.level == MATCH_ID and r.accepted and page.price_sources["json_ld"] == 18.73


def test_p003_100ml_without_volume_in_title_needs_id(products, bundle_terms):
    # redcare: titolo "Ceramol LIPOCREMA 311" senza formato -> valido solo con l'identificativo
    assert match_product(products["P003"], _page("Ceramol LIPOCREMA 311", eans={"8029408107014"}), bundle_terms).accepted
    assert match_product(products["P003"], _page("Ceramol LIPOCREMA 311"), bundle_terms).level == REJECT_FORMAT_UNKNOWN


def test_p003_100ml_accepted_by_strict_name(products, bundle_terms):
    r = match_product(products["P003"], _page("Ceramol 311 Lipocrema 100 ml"), bundle_terms)
    assert r.level == MATCH_NAME and r.accepted


@pytest.mark.parametrize("title,ids,expected", [
    ("Ceramol lipocrema 311 50ml", {"eans": {"8029408107021"}, "minsans": {"980512750"}}, REJECT_ID_DIFFERENT),  # ex P003
    ("Ceramol Lipocrema 311 50 ml", {}, REJECT_FORMAT),
    ("Ceramol 311 Lipocrema - Crema riparatrice ed emolliente - 50 ml", {"minsans": {"970539351"}}, REJECT_ID_DIFFERENT),
    ("Ceramol Lipocrema 311 duo pack 2 x 100 ml", {}, REJECT_BUNDLE),
    ("Ceramol Lipocrema 311 gel 30 ml", {}, REJECT_VARIANT),
])
def test_p003_50ml_and_other_formats_rejected(products, bundle_terms, title, ids, expected):
    r = match_product(products["P003"], _page(title, **ids), bundle_terms)
    assert not r.accepted and r.level == expected


def test_lipocrema_100_is_not_crema_mani_100(products, bundle_terms, fx):
    tmpl = fx("ceramol_100ml_no_ids.html")
    lipo = extract_page(tmpl.replace("{{TITOLO}}", "Ceramol 311 Lipocrema 100 ml").replace("{{PREZZO}}", "18,73"), "https://x.it/a")
    mani = extract_page(tmpl.replace("{{TITOLO}}", "Ceramol 311 Crema Mani 100 ml").replace("{{PREZZO}}", "11,79"), "https://x.it/b")
    assert match_product(products["P003"], lipo, bundle_terms).accepted
    assert not match_product(products["P004"], lipo, bundle_terms).accepted
    assert match_product(products["P004"], mani, bundle_terms).accepted
    assert not match_product(products["P003"], mani, bundle_terms).accepted
    # anche con gli identificativi: la Crema Mani (EAN P004) non è mai P003 e viceversa
    assert not match_product(products["P003"], _page("Ceramol 311 Crema Mani 100 ml", eans={"8029408093058"}), bundle_terms).accepted
    assert not match_product(products["P004"], _page("Ceramol lipocrema 311 100ml", eans={"8029408107014"}), bundle_terms).accepted


def test_p003_and_p004_url_candidates_do_not_overlap(products):
    urls = ["https://e.it/ceramol-lipocrema-311-100ml", "https://e.it/ceramol-crema-311-mani-100ml",
            "https://e.it/ceramol-lipocrema-311-50ml", "https://e.it/33488-ceramol-lipocrema-311-100-ml-8029408107014.html"]
    p3, p4 = rank_candidates(urls, products["P003"]), rank_candidates(urls, products["P004"])
    assert p3[0].endswith("8029408107014.html") and "https://e.it/ceramol-lipocrema-311-50ml" not in p3
    assert not set(p3) & set(p4)


def test_same_page_never_assigned_to_two_products():
    from scrapers.base import Offer, resolve_shared_urls
    a = Offer("P003", "f", "OK", price=11.0, url="https://www.f.it/ceramol-100ml", match_level=MATCH_NAME, verified=True)
    b = Offer("P004", "f", "OK", price=11.0, url="https://f.it/ceramol-100ml/", match_level=MATCH_NAME, verified=True)
    notes = resolve_shared_urls([a, b])
    assert len(notes) == 2 and a.status == b.status == STATUS_MATCH_UNRELIABLE and a.price is None and b.price is None
    c = Offer("P003", "f", "OK", price=18.7, url="https://f.it/x", match_level=MATCH_ID, verified=True)
    d = Offer("P004", "f", "OK", price=18.7, url="https://f.it/x", match_level=MATCH_NAME, verified=True)
    resolve_shared_urls([c, d])
    assert c.status == "OK" and c.price == 18.7 and d.status == STATUS_MATCH_UNRELIABLE and "P003" in d.notes[-1]
    e = Offer("P003", "f", "OK", price=1.0, url="https://f.it/y", match_level=MATCH_ID)
    g = Offer("P003", "g", "OK", price=1.0, url="https://f.it/y", match_level=MATCH_ID)  # farmacie diverse: nessun conflitto
    assert resolve_shared_urls([e, g]) == []


def test_reject_contradictory_ids(products, bundle_terms):
    page = _page("Ceramol Crema Mani 100 ml", eans={"8029408093058"}, minsans={"123456789"})
    assert match_product(products["P004"], page, bundle_terms).level == REJECT_ID_CONTRADICTION


def test_reject_bundle_even_with_id(products, bundle_terms):
    page = _page("Ceramol 311 Crema Mani Duo Pack 2 x 100 ml", eans={"8029408093058"})
    r = match_product(products["P004"], page, bundle_terms)
    assert not r.accepted and r.level == REJECT_BUNDLE


def test_reject_set_da_2(products, bundle_terms):
    r = match_product(products["P004"], _page("Ceramol 311 Crema Mani set da 2"), bundle_terms)
    assert r.level == REJECT_BUNDLE


def test_reject_other_format(products, bundle_terms):
    r = match_product(products["P001"], _page("Uriage Xémose Olio Detergente Lenitivo 500 ml"), bundle_terms)
    assert r.level == REJECT_FORMAT


def test_reject_tinted_variant(products, bundle_terms):
    r = match_product(products["P005"], _page("Anthelios UVMune 400 Fluido Invisibile SPF50+ Colorato 50 ml"), bundle_terms)
    assert r.level == REJECT_VARIANT


def test_reject_perfumed_variant(products, bundle_terms):
    r = match_product(products["P005"], _page("Anthelios UVMune 400 Fluido Invisibile SPF50+ Con Profumo 50 ml"), bundle_terms)
    assert not r.accepted


def test_reject_apmax(products, bundle_terms):
    r = match_product(products["P002"], _page("La Roche-Posay Lipikar Baume AP+MAX 400 ml"), bundle_terms)
    assert not r.accepted


def test_accept_strict_name_format(products, bundle_terms, fx):
    page = extract_page(fx("name_only_no_ids.html"), "https://x.it/anthelios")
    r = match_product(products["P005"], page, bundle_terms)
    assert r.level == MATCH_NAME and r.accepted and r.reliability == "media"


def test_reject_name_without_format(products, bundle_terms):
    r = match_product(products["P004"], _page("Ceramol 311 Crema Mani"), bundle_terms)
    assert r.level == REJECT_FORMAT_UNKNOWN and not r.accepted


def test_reject_wrong_brand(products, bundle_terms):
    r = match_product(products["P004"], _page("Ceramol crema mani 100 ml", brand="CeraVe"), bundle_terms)
    assert not r.accepted


def test_description_formats_do_not_confuse(products, bundle_terms, fx):
    page = extract_page(fx("opencart_noid.html"), "https://fv.it/x", price_css=["ul.price-product .new-price"],
                        reference_is_minsan=True)
    r = match_product(products["P001"], page, bundle_terms)
    assert r.level == MATCH_ID  # "500 ml e 200 ml" nella descrizione non contano


# ============================================================== robots
def test_robots_wildcards_and_groups(fx):
    pol = parse_robots(fx("robots_sample.txt"))
    tok_other = "OtherBot"
    assert not pol.is_allowed("https://e.it/categoria?search_query=ceramol", tok_other)
    assert not pol.is_allowed("https://e.it/ricerca", tok_other)
    assert not pol.is_allowed("https://e.it/checkout", tok_other)
    assert pol.is_allowed("https://e.it/checkout/step2", tok_other)       # "$" ancora la fine
    assert not pol.is_allowed("https://e.it/private/x", tok_other)
    assert pol.is_allowed("https://e.it/private/public-page", tok_other)   # vince la regola più lunga
    assert pol.is_allowed("https://e.it/p/123-ceramol.html", tok_other)
    # Gruppo specifico per il nostro UA: sostituisce il gruppo "*"
    assert pol.is_allowed("https://e.it/ricerca", "DermoPriceTracker")
    assert not pol.is_allowed("https://e.it/solo-per-noi/x", "DermoPriceTracker")
    assert pol.sitemaps == ["https://www.example-pharmacy.it/1_index_sitemap.xml"]
    assert pol.is_allowed("https://e.it/robots.txt", tok_other)


def test_robots_rfc_status_handling():
    assert policy_from_response(404, None).is_allowed("https://e.it/x", "t")
    assert not policy_from_response(503, None).is_allowed("https://e.it/x", "t")
    assert not policy_from_response(429, None).is_allowed("https://e.it/x", "t")
    assert not policy_from_response(None, None).is_allowed("https://e.it/x", "t")


# ============================================================== sitemap
def test_parse_sitemap_index_gzip_cdata():
    idx = b"<?xml version='1.0'?><sitemapindex><sitemap><loc><![CDATA[https://e.it/s1.xml]]></loc></sitemap></sitemapindex>"
    is_index, locs = parse_sitemap(idx)
    assert is_index and locs == ["https://e.it/s1.xml"]
    urlset = gzip.compress(b"<urlset><url><loc>https://e.it/a?x=1&amp;y=2</loc></url></urlset>")
    is_index, locs = parse_sitemap(urlset)
    assert not is_index and locs == ["https://e.it/a?x=1&y=2"]


def test_fetch_sitemap_follows_index_and_skips_blog():
    pages = {
        "https://e.it/index.xml": b"<sitemapindex><sitemap><loc>https://e.it/blog.xml</loc></sitemap>"
                                  b"<sitemap><loc>https://e.it/p1.xml</loc></sitemap></sitemapindex>",
        "https://e.it/p1.xml": b"<urlset><url><loc>https://e.it/ceramol-crema-mani-100ml</loc></url>"
                               b"<url><loc>https://e.it/altro</loc></url></urlset>",
    }
    calls = []

    def get(u):
        calls.append(u)
        return pages[u]
    urls, errors = fetch_sitemap_urls(get, ["https://e.it/index.xml"], prefilter=lambda u: "ceramol" in u)
    assert urls == ["https://e.it/ceramol-crema-mani-100ml"] and not errors
    assert "https://e.it/blog.xml" not in calls


def test_rank_candidates(products):
    urls = [
        "https://e.it/lipikar-baume-ap400ml",
        "https://e.it/lipikar-baume-apm-400ml",
        "https://e.it/lipikar-baume-apm-light-400ml",
        "https://e.it/24431-lipikar-baume-apm-200ml-3337875763790.html",   # EAN di un altro formato
        "https://e.it/14566-lipikar-baume-ap-m-400ml-3337875696548.html",  # EAN corretto
    ]
    ranked = rank_candidates(urls, products["P002"])
    assert ranked[0].endswith("3337875696548.html")
    assert "https://e.it/lipikar-baume-apm-light-400ml" not in ranked
    assert all("3337875763790" not in u for u in ranked)
    assert ranked.index("https://e.it/lipikar-baume-apm-400ml") < ranked.index("https://e.it/lipikar-baume-ap400ml")


def test_clean_result_url():
    assert clean_result_url("https://fv.it/4337-sottopiatto?search=xemose%20olio") == "https://fv.it/4337-sottopiatto"
    assert clean_result_url("https://s.it/index.php?route=product/product&product_id=5&search=x") == \
        "https://s.it/index.php?route=product%2Fproduct&product_id=5"


# ============================================================== sessione HTTP (finta, offline)
from fakes import Clock, FakeRequests, FakeResp, make_session  # noqa: E402,F401


def test_accept_header_has_no_json():
    s, fake, _ = make_session({})
    assert "application/json" not in fake.headers["Accept"]
    assert fake.headers["User-Agent"] == "TestUA/1.0"


def test_rate_limit_per_domain_min_delay():
    robots = FakeResp(200, b"User-agent: *\nDisallow:")
    s, fake, clock = make_session({"https://e.it/robots.txt": robots, "https://e.it/a": FakeResp(), "https://e.it/b": FakeResp()})
    s.get("https://e.it/a")
    s.get("https://e.it/b")
    assert fake.calls == ["https://e.it/robots.txt", "https://e.it/a", "https://e.it/b"]
    assert len(clock.sleeps) == 2 and all(x >= 2.5 for x in clock.sleeps)


def test_min_delay_never_below_two_seconds():
    s, _, _ = make_session({}, min_delay_seconds=0.1)
    assert s.min_delay == 2.0


def test_robots_disallow_raises_and_no_request():
    s, fake, _ = make_session({"https://e.it/robots.txt": FakeResp(200, b"User-agent: *\nDisallow: /ricerca")})
    with pytest.raises(RobotsDisallowed):
        s.get("https://e.it/ricerca?q=x")
    assert "https://e.it/ricerca?q=x" not in fake.calls


def test_429_retry_after_then_domain_suspended():
    robots = FakeResp(200, b"User-agent: *\nDisallow:")
    limited = lambda: FakeResp(429, b"local_rate_limited", {"Retry-After": "60"})  # noqa: E731
    s, fake, clock = make_session({"https://e.it/robots.txt": robots, "https://e.it/p": limited})
    with pytest.raises(DomainRateLimited):
        s.get("https://e.it/p")
    assert 60 in clock.sleeps  # Retry-After rispettato
    n = len(fake.calls)
    with pytest.raises(DomainRateLimited):
        s.get("https://e.it/altra")  # dominio sospeso: nessuna nuova richiesta
    assert len(fake.calls) == n


def test_429_then_success():
    robots = FakeResp(200, b"User-agent: *\nDisallow:")
    seq = [FakeResp(429, b"", {"Retry-After": "5"}), FakeResp(200, b"<html>ok</html>")]
    s, _, clock = make_session({"https://e.it/robots.txt": robots, "https://e.it/p": seq})
    assert s.get("https://e.it/p").status == 200
    assert 5 in clock.sleeps


def test_challenge_blocks_domain_without_bypass():
    robots = FakeResp(403, b"<html><title>Access Denied</title> errors.edgesuite.net</html>")
    s, fake, _ = make_session({"https://blocked.it/robots.txt": robots})
    with pytest.raises(DomainBlocked):
        s.get("https://blocked.it/p")
    with pytest.raises(DomainBlocked):
        s.get("https://blocked.it/q")
    assert fake.calls == ["https://blocked.it/robots.txt"]


def test_js_challenge_with_status_200_is_blocked():
    page = FakeResp(200, b"<html>Please Enable JavaScript. www.x.it is using a security service for protection against online attacks</html>")
    s, _, _ = make_session({"https://x.it/robots.txt": FakeResp(404, b"nf"), "https://x.it/p": page})
    with pytest.raises(DomainBlocked):
        s.get("https://x.it/p")


def test_5xx_retry_with_backoff_then_error():
    robots = FakeResp(200, b"User-agent: *\nDisallow:")
    s, fake, clock = make_session({"https://e.it/robots.txt": robots, "https://e.it/p": lambda: FakeResp(502, b"bad gateway")})
    with pytest.raises(FetchError):
        s.get("https://e.it/p")
    assert fake.calls.count("https://e.it/p") == 3
    assert 1 in clock.sleeps and 2 in clock.sleeps  # backoff esponenziale 1, 2


def test_empty_body_is_error():
    robots = FakeResp(200, b"User-agent: *\nDisallow:")
    s, _, _ = make_session({"https://e.it/robots.txt": robots, "https://e.it/p": FakeResp(200, b"")})
    with pytest.raises(FetchError):
        s.get("https://e.it/p")


# ============================================================== flusso scraper offline
class FakeScraperSession:
    """Sostituisce PoliteSession nel flusso BaseScraper."""

    def __init__(self, pages=None, exc=None):
        self.pages, self.exc, self.calls = pages or {}, exc, []

    def set_domain_delay(self, *a):
        pass

    def is_allowed(self, url):
        return True

    def get(self, url, accept=None, **kw):
        self.calls.append(url)
        if self.exc:
            raise self.exc
        from scrapers.http import FetchResult
        if url not in self.pages:
            return FetchResult(url, url, 404, b"nf", {}, 0.0)
        return FetchResult(url, url, 200, self.pages[url].encode("utf-8"), {"content-type": "text/html; charset=utf-8"}, 0.0)


class _TestScraper(BaseScraper):
    pharmacy_id = "test"
    name = "Test"
    base_url = "https://www.farmacia-test.it"
    price_css = [".current-price"]
    availability_css = ["#product-availability", ".product-add-to-cart"]


def _scraper(config, session, cands):
    cache = UrlCache()
    for pid, urls in cands.items():
        cache.set_candidates("test", pid, urls, "2099-01-01T00:00:00Z", "test")
    return _TestScraper(session, config, cache, now_iso="2026-11-18T07:00:00Z"), cache


def test_scrape_product_ok_with_404_fallback(config, products, fx):
    good = "https://www.farmacia-test.it/creme/26683-ceramol-crema-mani-100ml.html"
    sess = FakeScraperSession({good: fx("prestashop_product.html")})
    sc, cache = _scraper(config, sess, {"P004": ["https://www.farmacia-test.it/sparito", good]})
    offer = sc.scrape_product(products["P004"])
    assert offer.status == STATUS_OK and offer.price == 13.59 and offer.verified and offer.usable
    assert offer.match_level == MATCH_ID
    assert cache.entry("test", "P004")["matched_url"] == good


def test_scrape_product_not_found_and_unverified(config, products, fx):
    sc, _ = _scraper(config, FakeScraperSession(), {})
    assert sc.scrape_product(products["P001"]).status == STATUS_NOT_FOUND
    url = "https://www.farmacia-test.it/lipikar"
    sc2, _ = _scraper(config, FakeScraperSession({url: fx("unavailable_no_price.html").replace("Riferimento", "Minsan")}),
                      {"P002": [url]})
    off = sc2.scrape_product(products["P002"])
    assert off.status == STATUS_PRICE_UNVERIFIED and off.price is None and not off.usable


def test_scrape_product_rejects_wrong_product(config, products, fx):
    url = "https://www.farmacia-test.it/creme/26683-ceramol-crema-mani-100ml.html"
    sc, _ = _scraper(config, FakeScraperSession({url: fx("prestashop_product.html")}), {"P003": [url]})
    off = sc.scrape_product(products["P003"])
    assert off.status == STATUS_MATCH_UNRELIABLE and off.price is None


def test_blocked_and_rate_limited_stop_pharmacy(config, products):
    a, b = "https://www.farmacia-test.it/a", "https://www.farmacia-test.it/b"
    sc, _ = _scraper(config, FakeScraperSession(exc=DomainBlocked("challenge")), {"P001": [a], "P002": [b]})
    offers = sc.run([products["P001"], products["P002"]])
    assert [o.status for o in offers] == [STATUS_BLOCKED, STATUS_BLOCKED]
    sess = FakeScraperSession(exc=DomainRateLimited("HTTP 429 persistente"))
    sc, _ = _scraper(config, sess, {"P001": [a], "P002": [b]})
    offers = sc.run([products["P001"], products["P002"]])
    assert [o.status for o in offers] == [STATUS_RATE_LIMITED, STATUS_RATE_LIMITED]
    assert len(sess.calls) == 1  # dopo il 429 persistente nessun'altra richiesta


def test_failing_pharmacy_does_not_stop_others(config, products, fx, monkeypatch):
    import scraper as orchestrator

    good = "https://www.farmacia-test.it/creme/26683-ceramol-crema-mani-100ml.html"

    class Boom(BaseScraper):
        pharmacy_id, base_url = "boom", "https://boom.it"

        def run(self, *a, **k):
            raise RuntimeError("crash inatteso")

    monkeypatch.setitem(orchestrator.REGISTRY, "boom", Boom)
    monkeypatch.setitem(orchestrator.REGISTRY, "test", _TestScraper)
    cache = UrlCache()
    cache.set_candidates("test", "P004", [good], "2099-01-01T00:00:00Z", "t")
    sess = FakeScraperSession({good: fx("prestashop_product.html")})
    sess.domain_status = lambda u: {"requests": 1}
    pid, offers, _ = orchestrator.run_pharmacy({"id": "boom"}, sess, config, cache, "2026-11-18T07:00:00Z", [products["P004"]], False)
    assert offers[0].status == "ERRORE" and "crash" in offers[0].notes[0]
    pid, offers, _ = orchestrator.run_pharmacy({"id": "test"}, sess, config, cache, "2026-11-18T07:00:00Z", [products["P004"]], False)
    assert offers[0].status == STATUS_OK
