"""Scraper GENERICO basato solo su dati strutturati (JSON-LD -> microdata -> meta tag).

Serve a valutare e, dopo promozione MANUALE, a monitorare farmacie per cui non esiste uno
scraper dedicato. Non legge mai il testo visibile: se una pagina non ha dati strutturati
l'esito è "NESSUN DATO STRUTTURATO".

Individuazione delle pagine prodotto (per ogni prodotto, ci si ferma appena funziona):
  1. sitemap (da robots.txt o /sitemap.xml), con filtro per parole chiave nell'URL;
  2. ricerca interna: pattern salvato in pharmacies.json, poi OpenSearch (<link rel="search">),
     poi pattern comuni. Il pattern che funziona viene salvato (search_url_template);
  3. dai risultati si tengono al massimo N link (default 5) che contengono marca/nome nell'URL o nel titolo.

Regole: robots.txt verificato prima di ogni richiesta; ritmo e retry della PoliteSession;
tetto di richieste per dominio per esecuzione (config.json -> generic_jsonld.max_requests_per_domain);
403/429/challenge => BLOCKED; match dubbio => offerta scartata con il motivo.

Le offerte dei siti non ACTIVE non entrano MAI nel carrello né nello storico: finiscono in
data.json -> candidate_offers con reliability "NON_VERIFICATO".
"""
from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlsplit

from .base import (STATUS_BLOCKED, STATUS_MATCH_UNRELIABLE, STATUS_NO_STRUCTURED, STATUS_OK, STATUS_RATE_LIMITED,
                   BaseScraper, Offer, UrlCache, clean_result_url)
from .extract import (AVAILABLE, UNAVAILABLE, LdVariant, PageData, extract_page, ids_from_url, iter_jsonld,
                      normalize_minsan, soup_of)
from .http import DomainBlocked, DomainRateLimited, FetchError, RobotsDisallowed, domain_key
from .matcher import ACCEPTED_LEVELS, MATCH_ID, match_product, normalize, volumes_ml
from .sitemap import PRODUCT_CHILD, fetch_sitemap_urls, keyword_prefilter, rank_candidates

log = logging.getLogger("tracker.generic")

DEFAULTS = {
    "max_requests_per_domain": 25,
    "max_candidates_per_product": 5,
    "max_sitemap_children": 4,
    "probe_max_age_days": 7,
    "probe_per_discovery_run": 5,
    "search_patterns": [
        "/search?q={q}", "/?s={q}", "/catalogsearch/result/?q={q}", "/ricerca?q={q}",
        "/search?search={q}", "/index.php?route=product/search&search={q}", "/ricerca?controller=search&s={q}",
    ],
}
COMPAT_FULL, COMPAT_PARTIAL, COMPAT_NONE, COMPAT_BLOCKED = "FULL", "PARTIAL", "NONE", "BLOCKED"
CANDIDATE_RELIABILITY = "NON_VERIFICATO"


class RequestBudgetExceeded(FetchError):
    """Tetto di richieste per dominio raggiunto: nessuna ulteriore richiesta in questa esecuzione."""


def settings(config: dict) -> dict:
    return {**DEFAULTS, **(config.get("generic_jsonld") or {})}


def opensearch_to_template(tmpl: str) -> str:
    """'https://x.it/s?p={startPage?}&q={searchTerms}&l={language}' -> 'https://x.it/s?q={q}'.

    Tiene i parametri fissi e quello con {searchTerms}; toglie gli altri segnaposto OpenSearch.
    """
    parts = urlsplit(tmpl)
    kept = []
    for pair in parts.query.split("&") if parts.query else []:
        if "{searchTerms}" in pair:
            kept.append(pair.replace("{searchTerms}", "{q}"))
        elif "{" not in pair and pair:
            kept.append(pair)
    path = parts.path.replace("{searchTerms}", "{q}")
    query = "&".join(kept)
    return f"{parts.scheme}://{parts.netloc}{path}" + (f"?{query}" if query else "")


def _relevant_text(product: dict, text: str) -> bool:
    """Il link/titolo contiene tutti i termini obbligatori del prodotto (marca/linea/nome)?"""
    toks = set(normalize(text).split())
    required = [normalize(t) for t in product.get("match", {}).get("required_all", [])]
    return bool(required) and all(t in toks for t in required)


def _link_score(product: dict, url: str, text: str) -> int | None:
    eans, _ = ids_from_url(url)
    if product.get("ean") and product["ean"] in eans:
        return 1000
    if eans and product.get("ean") and product["ean"] not in eans:
        return None  # EAN di un altro prodotto/formato nell'URL
    if not _relevant_text(product, f"{url.replace('-', ' ').replace('_', ' ')} {text}"):
        return None
    slug = urlsplit(url).path.lower()
    if any(tok in slug for tok in product.get("url_filter", {}).get("exclude", [])):
        return None
    vols = volumes_ml(f"{text} {slug.replace('-', ' ')}")
    fmt = int(product["format"]["quantity"])
    if vols and fmt not in vols:
        return None  # formato diverso dichiarato nel link
    return 100 + (50 if fmt in vols else 0) - min(len(slug) // 20, 20)


class GenericJsonLdScraper(BaseScraper):
    platform = "generico (JSON-LD)"
    sku_is_minsan = False   # lo SKU di un sito sconosciuto non è garantito essere un Minsan
    price_css: list[str] = []
    availability_css: list[str] = []
    sitemap_refresh_days = 7

    def __init__(self, entry: dict, session, config: dict, cache: UrlCache, *, now_iso: str,
                 logger: logging.Logger | None = None):
        self.entry = entry
        self.pharmacy_id = entry["id"]
        self.name = entry.get("name") or entry["domain"]
        self.base_url = (entry.get("base_url") or f"https://{entry['domain']}").rstrip("/")
        self.settings = settings(config)
        self.search_url_template = entry.get("search_url_template")
        self.sitemap_urls = list(entry.get("sitemaps") or [])
        super().__init__(session, config, cache, now_iso=now_iso, logger=logger or log)
        self.max_candidates = int(self.settings["max_candidates_per_product"])
        self.budget = int(self.settings["max_requests_per_domain"])
        self._req_start = session.domain_status(self.base_url)["requests"]
        self.budget_exhausted = False
        self.fetched: list[str] = []
        self.search_pattern_used: str | None = None
        self.sitemaps_used: list[str] = []
        self.discovery_notes: list[str] = []

    # ------------------------------------------------------------------ richieste (tetto + robots)
    def requests_used(self) -> int:
        return self.session.domain_status(self.base_url)["requests"] - self._req_start

    def _check_budget(self) -> None:
        if self.requests_used() >= self.budget:
            self.budget_exhausted = True
            raise RequestBudgetExceeded(f"tetto di {self.budget} richieste per {domain_key(self.base_url)} raggiunto")

    def fetch(self, url: str, accept: str | None = None):
        self._check_budget()
        if not self.session.is_allowed(url):  # robots.txt prima di ogni path (può costare 1 richiesta)
            raise RobotsDisallowed(f"Vietato da robots.txt: {url}")
        self._check_budget()
        res = self.session.get(url, accept=accept)
        self.fetched.append(url)
        return res

    # ------------------------------------------------------------------ 1) sitemap
    def _sitemap_roots(self) -> list[str]:
        if self.sitemap_urls:
            return self.sitemap_urls[:2]
        pol = self.session.robots_policy(self.base_url + "/")
        return (pol.sitemaps or [self.base_url + "/sitemap.xml"])[:2]

    def candidates_from_sitemap(self, products: list[dict]) -> dict[str, list[str]]:
        roots = self._sitemap_roots()
        self.sitemaps_used = roots
        urls, errors = fetch_sitemap_urls(self.fetch_bytes, roots, prefilter=keyword_prefilter(products),
                                          max_children=int(self.settings["max_sitemap_children"]), prioritize=PRODUCT_CHILD)
        for e in errors[:3]:
            self.discovery_notes.append(f"sitemap: {e}")
        return {p["id"]: rank_candidates(urls, p, limit=self.max_candidates) for p in products}

    # ------------------------------------------------------------------ 2) ricerca interna
    def _fill(self, template: str, query: str) -> str:
        url = template.replace("{q}", quote_plus(query))
        return url if url.startswith("http") else urljoin(self.base_url + "/", url.lstrip("/"))

    def _opensearch_template(self) -> str | None:
        home = self.fetch(self.base_url + "/")
        if not home.ok:
            return None
        link = soup_of(home.text).find("link", attrs={"rel": lambda v: v and "search" in v,
                                                      "type": "application/opensearchdescription+xml"})
        if link is None or not link.get("href"):
            return None
        desc = self.fetch(urljoin(home.final_url, link["href"]))
        if not desc.ok:
            return None
        try:
            from bs4 import BeautifulSoup
            osd = BeautifulSoup(desc.text, "xml")  # il descrittore OpenSearch è XML
        except Exception:  # noqa: BLE001 - senza lxml si ripiega sul parser HTML
            osd = soup_of(desc.text)
        for url_el in osd.find_all(lambda t: t.name and t.name.lower() == "url"):
            tmpl, typ = url_el.get("template"), (url_el.get("type") or "text/html")
            if tmpl and "html" in typ and "{searchTerms}" in tmpl:
                return opensearch_to_template(tmpl)
        return None

    def result_links(self, html: str, base: str, product: dict) -> list[str]:
        soup = soup_of(html)
        host = domain_key(self.base_url)
        scored: dict[str, int] = {}
        pairs: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            pairs.append((a["href"], " ".join(filter(None, [a.get_text(" ", strip=True), a.get("title")]))))
        for item in iter_jsonld(soup):  # risultati descritti in un ItemList
            if item.get("url") and item.get("name"):
                pairs.append((str(item["url"]), str(item["name"])))
        for href, text in pairs:
            url = clean_result_url(urljoin(base, href.split("#")[0]))
            if domain_key(url) != host or url.rstrip("/") == base.rstrip("/"):
                continue
            s = _link_score(product, url, text)
            if s is not None and s > scored.get(url, -1):
                scored[url] = s
        ranked = sorted(scored, key=lambda u: (-scored[u], len(u)))
        return ranked[: self.max_candidates]

    def detect_search_template(self, product: dict) -> str | None:
        query = product.get("search_queries", [product.get("name", "")])[0]
        templates: list[str] = []
        try:
            osd = self._opensearch_template()
            if osd:
                templates.append(osd)
        except (DomainBlocked, DomainRateLimited, RequestBudgetExceeded):
            raise
        except FetchError as exc:
            self.discovery_notes.append(f"OpenSearch non disponibile: {exc}")
        templates += [t for t in self.settings["search_patterns"] if t not in templates]
        for tmpl in templates:
            url = self._fill(tmpl, query)
            if not self.session.is_allowed(url):
                self.discovery_notes.append(f"pattern di ricerca vietato da robots.txt: {tmpl}")
                continue
            try:
                res = self.fetch(url)
            except RobotsDisallowed:
                continue
            except (DomainBlocked, DomainRateLimited, RequestBudgetExceeded):
                raise
            except FetchError as exc:
                self.discovery_notes.append(f"ricerca {tmpl}: {exc}")
                continue
            if res.ok and self.result_links(res.text, res.final_url, product):
                self.search_url_template = self.search_pattern_used = tmpl
                return tmpl
        return None

    def candidates_from_search(self, product: dict) -> list[str]:
        if not self.search_url_template:
            return []
        for q in product.get("search_queries", [])[:2]:
            url = self._fill(self.search_url_template, q)
            if not self.session.is_allowed(url):
                return []
            res = self.fetch(url)
            if res.ok:
                links = self.result_links(res.text, res.final_url, product)
                if links:
                    return links
        return []

    # ------------------------------------------------------------------ orchestrazione candidati
    def refresh_candidates(self, products: list[dict]) -> None:
        found: dict[str, list[str]] = {}
        method: dict[str, str] = {}
        try:
            for pid, c in self.candidates_from_sitemap(products).items():
                if c:
                    found[pid], method[pid] = c, "sitemap"
        except (DomainBlocked, DomainRateLimited, RequestBudgetExceeded):
            raise
        except FetchError as exc:
            self.discovery_notes.append(f"sitemap non leggibile: {exc}")
        missing = [p for p in products if not found.get(p["id"])]
        if missing:
            try:
                if not self.search_url_template:
                    self.detect_search_template(missing[0])
                elif not self.search_pattern_used:
                    self.search_pattern_used = self.search_url_template
                for p in missing:
                    c = self.candidates_from_search(p)
                    if c:
                        found[p["id"]], method[p["id"]] = c, "ricerca interna"
            except (DomainBlocked, DomainRateLimited):
                raise
            except FetchError as exc:  # incluso il tetto di richieste
                self.discovery_notes.append(str(exc))
        for p in products:
            self.cache.set_candidates(self.pharmacy_id, p["id"], found.get(p["id"], []), self.now_iso,
                                      method.get(p["id"], "nessuno"))

    # ------------------------------------------------------------------ pagina prodotto
    def parse(self, html: str, url: str) -> PageData:
        return extract_page(html, url, price_css=[], availability_css=[], title_css=["h1"], sku_is_minsan=False,
                            ids_from_url_enabled=True, allow_text_fallback=False)

    @staticmethod
    def page_for_variant(page: PageData, v: LdVariant) -> PageData:
        """Vista della pagina ristretta a UNA variante: solo i dati di quella variante.

        Titolo h1/og e prezzi microdata/meta della pagina si riferiscono spesso alla variante
        preselezionata: non sono attribuibili e vengono esclusi.
        """
        base_name = page.titles.get("json_ld") or ""
        name = v.name or base_name
        if base_name and normalize(base_name) not in normalize(name):
            name = f"{base_name} {name}" if not volumes_ml(base_name) else name
        return replace(page, titles={"json_ld": name}, eans=set(v.eans), minsans=set(),
                       price_sources={"json_ld": v.price} if v.price is not None else {},
                       availability_sources={"json_ld": v.availability} if v.availability else {},
                       currency=v.currency, variants=[v], skus=set(v.skus), notes=list(page.notes))

    def choose_variant(self, product: dict, page: PageData) -> tuple[LdVariant | None, str]:
        if product.get("ean"):
            by_ean = [v for v in page.variants if product["ean"] in v.eans]
            if len(by_ean) == 1:
                return by_ean[0], f"variante per EAN {product['ean']}"
        accepted = []
        for v in page.variants:
            m = match_product(product, self._with_minsan(product, self.page_for_variant(page, v)), self.bundle_terms)
            if m.accepted:
                accepted.append((v, m))
        if len(accepted) == 1:
            return accepted[0][0], f"unica variante coerente ({accepted[0][1].level})"
        if not accepted:
            return None, f"nessuna delle {len(page.variants)} varianti corrisponde a {product['format']['label']}"
        by_id = [v for v, m in accepted if m.level == MATCH_ID]
        if len(by_id) == 1:
            return by_id[0], "unica variante con identificativo coincidente"
        return None, f"{len(accepted)} varianti compatibili: variante non determinabile"

    @staticmethod
    def _with_minsan(product: dict, page: PageData) -> PageData:
        """Lo SKU vale come Minsan SOLO se coincide con quello del catalogo (mai come conflitto)."""
        cfg = product.get("minsan")
        if cfg and any(normalize_minsan(s) == cfg for s in page.skus):
            page.minsans.add(cfg)
        return page

    def evaluate(self, product: dict, page: PageData, final_url: str) -> Offer:
        if not page.structured:
            return Offer(product["id"], self.pharmacy_id, STATUS_NO_STRUCTURED, url=final_url, title=page.title or None,
                         match_level="NESSUN_DATO", match_reliability="esclusa", scraped_at=self.now_iso,
                         notes=["nessun dato strutturato (JSON-LD, microdata, meta prezzo): pagina non valutata"])
        chosen: LdVariant | None = None
        if len(page.variants) > 1:
            chosen, why = self.choose_variant(product, page)
            if chosen is None:
                return Offer(product["id"], self.pharmacy_id, STATUS_MATCH_UNRELIABLE, url=final_url,
                             title=page.title or None, match_level="VARIANTE_INCERTA", match_reliability="esclusa",
                             scraped_at=self.now_iso, notes=[why])
            page = self.page_for_variant(page, chosen)
            page.notes.append(why)
        elif page.variants and page.variants[0].price_ambiguous:
            # AggregateOffer/offerte con prezzi diversi senza variante attribuibile: niente ripiego su meta/microdata
            page.price_sources = {}
            page.notes.append("JSON-LD con più prezzi non attribuibili a un formato: prezzo non verificabile")
        page = self._with_minsan(product, page)
        offer = super().evaluate(product, page, final_url)
        v = chosen or (page.variants[0] if page.variants else None)
        if v is not None:
            offer.variant = v.name if chosen else None
            offer.shipping_jsonld = v.shipping or None
        return offer

    def run(self, products: list[dict], *, refresh: bool = False, refresh_days: float = 7) -> list[Offer]:
        try:
            self.session.robots_policy(self.base_url + "/")  # robots.txt subito: conta nel tetto
        except (DomainBlocked, DomainRateLimited) as exc:
            self.fatal = str(exc)
            return [self._fatal_offer(p) for p in products]
        offers = super().run(products, refresh=refresh, refresh_days=refresh_days)
        if self.budget_exhausted:
            for o in offers:
                if o.status not in (STATUS_OK,):
                    o.notes.append(f"tetto di {self.budget} richieste raggiunto: ricerca incompleta")
        return offers


# --------------------------------------------------------------------------- probe

def compatibility(offers: list[Offer], fatal: str | None) -> tuple[str, str]:
    ok = [o for o in offers if o.status == STATUS_OK and o.verified and o.price is not None]
    full = [o for o in ok if o.match_level == MATCH_ID and o.availability in (AVAILABLE, UNAVAILABLE)]
    if fatal:  # 403/429/challenge: il sito non è monitorabile in modo affidabile, anche se qualcosa è passato
        return COMPAT_BLOCKED, fatal
    if full:
        return COMPAT_FULL, f"{len(full)} offerte autentiche (prezzo + disponibilità + EAN/Minsan coincidente)"
    if ok:
        return COMPAT_PARTIAL, "prezzo strutturato trovato ma match solo per nome+formato o disponibilità ignota"
    matched = [o for o in offers if o.match_level in ACCEPTED_LEVELS]
    if matched:
        return COMPAT_PARTIAL, "prodotto riconosciuto ma prezzo strutturato assente, ambiguo o in valuta diversa"
    if any(o.status == STATUS_NO_STRUCTURED for o in offers):
        return COMPAT_NONE, "pagine prodotto trovate ma senza dati strutturati"
    return COMPAT_NONE, "nessuno dei 5 prodotti trovato con dati strutturati affidabili"


def offer_brief(o: Offer, products: dict[str, dict]) -> dict:
    return {
        "product_id": o.product_id, "status": o.status, "url": o.url, "title": o.title, "price": o.price,
        "availability": o.availability, "match_level": o.match_level, "match_reasons": o.match_reasons[:2],
        "variant": o.variant, "shipping_jsonld": o.shipping_jsonld, "price_method": o.price_method,
        "format": (products.get(o.product_id) or {}).get("format", {}).get("label"), "scraped_at": o.scraped_at,
        "notes": [n for n in o.notes if n][:3],
        "rejected": [{"url": r.get("url"), "level": r.get("match_level"), "reasons": r.get("reasons", [])[:1]}
                     for r in o.rejected_candidates[:3]],
    }


def probe(entry: dict, session, config: dict, products: list[dict], now_iso: str) -> tuple[dict, list[Offer]]:
    """Valuta un dominio sui prodotti del catalogo. Non modifica file: restituisce (report, offerte)."""
    sc = GenericJsonLdScraper(entry, session, config, UrlCache(), now_iso=now_iso)
    offers = sc.run(products, refresh=True)
    compat, reason = compatibility(offers, sc.fatal)
    by_id = {p["id"]: p for p in products}
    found = [o for o in offers if o.status == STATUS_OK]
    report = {
        "domain": entry["domain"], "pharmacy_id": entry["id"], "at": now_iso,
        "compatibility": compat, "reason": reason,
        "requests": sc.requests_used(), "budget": sc.budget, "budget_exhausted": sc.budget_exhausted,
        "search_url_template": sc.search_pattern_used, "sitemaps_used": sc.sitemaps_used,
        "paths_used": sorted({urlsplit(u).path or "/" for u in sc.fetched}),
        "urls_fetched": sc.fetched,
        "products_found": sorted({o.product_id for o in found}),
        "products_not_found": sorted({o.product_id for o in offers} - {o.product_id for o in found}),
        "examples": [{"product_id": o.product_id, "url": o.url, "price": o.price, "availability": o.availability,
                      "match_level": o.match_level, "variant": o.variant} for o in found],
        "offers": [offer_brief(o, by_id) for o in offers],
        "errors": sorted({n for o in offers for n in o.notes if n and o.status != STATUS_OK})[:10] + sc.discovery_notes[:5],
        "fatal": sc.fatal,
    }
    return report, offers


def apply_probe(entry: dict, report: dict) -> None:
    """Aggiorna la voce del registro con l'esito del probe. Non promuove MAI ad ACTIVE."""
    compat = report["compatibility"]
    entry["jsonld_compatibility"] = compat
    entry["probe"] = {k: report[k] for k in ("at", "compatibility", "reason", "requests", "budget", "budget_exhausted",
                                             "search_url_template", "sitemaps_used", "paths_used", "products_found",
                                             "products_not_found", "examples", "errors", "offers")}
    entry["probe"]["urls_fetched"] = list(dict.fromkeys(report.get("urls_fetched") or []))[:40]
    if report.get("search_url_template"):
        entry["search_url_template"] = report["search_url_template"]
    entry["last_verified"] = report["at"]
    entry["products_found"] = report["products_found"]
    entry["errors"] = report["errors"][:10]
    if entry.get("status") == "ACTIVE":
        entry["scraper_status"] = "BLOCKED" if compat == COMPAT_BLOCKED else entry.get("scraper_status")
    else:
        entry["scraper_status"] = f"PROBE_{compat}"
    new_status = entry.get("status")
    if compat == COMPAT_BLOCKED:
        new_status = "BLOCKED"
    elif entry.get("status") == "DISCOVERED":
        new_status = "UNDER_REVIEW"
    if new_status != entry.get("status"):
        entry.setdefault("status_history", []).append(
            {"at": report["at"], "status": new_status, "reason": f"probe JSON-LD: {compat} ({report['reason']})"})
        entry["status"] = new_status


def candidate_offers_from_registry(registry: dict) -> list[dict]:
    """Offerte trovate dai probe sui siti NON attivi: mai nel carrello, mai nello storico."""
    out = []
    for p in registry.get("pharmacies", []):
        if p.get("status") == "ACTIVE":
            continue
        for o in (p.get("probe") or {}).get("offers") or []:
            if o.get("status") == STATUS_OK and o.get("price") is not None:
                out.append({**o, "pharmacy_id": p["id"], "pharmacy_name": p.get("name"), "domain": p.get("domain"),
                            "pharmacy_status": p.get("status"), "jsonld_compatibility": p.get("jsonld_compatibility"),
                            "reliability": CANDIDATE_RELIABILITY, "source": "probe", "usable": False})
    return out


def probe_markdown(report: dict, products: list[dict]) -> str:
    by_id = {p["id"]: p for p in products}
    lines = [
        f"# Probe JSON-LD: {report['domain']}",
        "",
        f"- Data: {report['at']}",
        f"- **Compatibilità: {report['compatibility']}**: {report['reason']}",
        f"- Richieste effettuate: {report['requests']} (tetto {report['budget']}"
        + (", RAGGIUNTO" if report.get("budget_exhausted") else "") + ")",
        f"- Sitemap usate: {', '.join(report['sitemaps_used']) or 'nessuna'}",
        f"- Pattern di ricerca: `{report['search_url_template']}`" if report.get("search_url_template") else "- Pattern di ricerca: nessuno",
        f"- Prodotti trovati: {', '.join(report['products_found']) or 'nessuno'}",
        f"- Prodotti non trovati: {', '.join(report['products_not_found']) or 'nessuno'}",
        "",
        "Le offerte qui sotto NON sono verificate e NON entrano nel carrello finché il dominio non viene promosso a mano",
        "(`python pharmacy_discovery.py promote <dominio>`). Controlla gli esempi aprendo gli URL.",
        "",
        "## Esito per prodotto",
        "",
        "| Prodotto | Esito | Prezzo | Disponibilità | Match | URL |",
        "|---|---|---|---|---|---|",
    ]
    for o in report["offers"]:
        p = by_id.get(o["product_id"], {})
        price = f"{o['price']:.2f} €".replace(".", ",") if o.get("price") is not None else "—"
        lines.append(f"| {o['product_id']} {p.get('name', '')} {p.get('format', {}).get('label', '')} | {o['status']} | {price} | "
                     f"{o.get('availability') or '—'} | {o.get('match_level') or '—'} | {o.get('url') or '—'} |")
    lines += ["", "## Dettagli", ""]
    for o in report["offers"]:
        lines.append(f"### {o['product_id']}")
        for r in o.get("match_reasons") or []:
            lines.append(f"- motivo match: {r}")
        if o.get("variant"):
            lines.append(f"- variante scelta: {o['variant']}")
        if o.get("price_method"):
            lines.append(f"- fonte prezzo: {o['price_method']}")
        if o.get("shipping_jsonld"):
            lines.append(f"- spedizione dichiarata nel JSON-LD: {o['shipping_jsonld']}")
        for n in o.get("notes") or []:
            lines.append(f"- nota: {n}")
        for r in o.get("rejected") or []:
            lines.append(f"- scartato: {r['url']} ({r['level']}: {'; '.join(r['reasons'])})")
        lines.append("")
    if report["errors"]:
        lines += ["## Errori e avvisi", ""] + [f"- {e}" for e in report["errors"]] + [""]
    lines += ["## Percorsi richiesti (tutti verificati su robots.txt)", ""] + [f"- `{p}`" for p in report["paths_used"]]
    return "\n".join(lines) + "\n"


def write_probe_markdown(report: dict, products: list[dict], docs_dir: Path) -> Path:
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / f"{report['domain']}.md"
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(probe_markdown(report, products), encoding="utf-8")
    tmp.replace(path)
    return path
