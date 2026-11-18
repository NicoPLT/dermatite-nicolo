"""Classe base degli scraper e modello dati dell'offerta.

Ogni farmacia ha una sottoclasse (di solito tramite una piattaforma in platforms.py)
che dichiara: URL base, sitemap, selettori CSS del prezzo/disponibilità e, se la
ricerca interna è consentita da robots.txt, come costruire l'URL di ricerca.

Flusso per prodotto:
  candidati (URL già confermato in cache -> sitemap/ricerca) -> pagina -> estrazione
  -> match rigoroso -> scelta dell'offerta migliore -> Offer
"""
from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from .extract import (AVAILABLE, UNAVAILABLE, UNKNOWN, PageData, extract_page, resolve_availability,
                      resolve_price, soup_of)
from .http import DomainBlocked, DomainRateLimited, FetchError, PoliteSession, RobotsDisallowed
from .matcher import ACCEPTED_LEVELS, MATCH_ID, MatchResult, match_product
from .sitemap import fetch_sitemap_urls, keyword_prefilter, rank_candidates

STATUS_OK = "OK"
STATUS_NOT_FOUND = "NON TROVATO"
STATUS_PRICE_UNVERIFIED = "PREZZO NON VERIFICATO"
STATUS_MATCH_UNRELIABLE = "MATCH NON AFFIDABILE"
STATUS_ERROR = "ERRORE"
STATUS_BLOCKED = "BLOCCATO"
STATUS_RATE_LIMITED = "LIMITATO (429)"
STATUS_ROBOTS = "VIETATO DA ROBOTS"
STATUS_NO_STRUCTURED = "NESSUN DATO STRUTTURATO"  # scraper generico: pagina senza JSON-LD/microdata/meta prezzo


@dataclass
class Offer:
    product_id: str
    pharmacy_id: str
    status: str
    price: float | None = None
    currency: str = "EUR"
    availability: str = UNKNOWN
    url: str | None = None
    title: str | None = None
    match_level: str | None = None
    match_reliability: str | None = None
    match_reasons: list[str] = field(default_factory=list)
    ids_found: dict[str, list[str]] = field(default_factory=dict)
    price_sources: dict[str, float] = field(default_factory=dict)
    price_method: str | None = None
    price_conflict: bool = False
    availability_sources: dict[str, str] = field(default_factory=dict)
    availability_method: str | None = None
    scraped_at: str | None = None
    verified: bool = False
    notes: list[str] = field(default_factory=list)
    rejected_candidates: list[dict] = field(default_factory=list)
    shipping_jsonld: list[dict] | None = None   # costo/paese da OfferShippingDetails, se dichiarati
    variant: str | None = None                   # variante scelta (ProductGroup/offerte multiple)

    @property
    def usable(self) -> bool:
        """Utilizzabile dall'ottimizzatore: verificato e non esaurito."""
        return self.status == STATUS_OK and self.verified and self.price is not None and self.availability != UNAVAILABLE

    def to_dict(self) -> dict:
        d = asdict(self)
        d["usable"] = self.usable
        return d


class UrlCache:
    """Cache thread-safe degli URL candidati/confermati (product_urls.json)."""

    def __init__(self, data: dict | None = None):
        self.data = data if isinstance(data, dict) else {}
        self.data.setdefault("schema_version", 1)
        self.data.setdefault("pharmacies", {})
        self._lock = threading.Lock()

    def ph(self, pharmacy_id: str) -> dict:
        with self._lock:
            return self.data["pharmacies"].setdefault(pharmacy_id, {"products": {}})

    def entry(self, pharmacy_id: str, product_id: str) -> dict:
        ph = self.ph(pharmacy_id)
        with self._lock:
            return ph["products"].setdefault(product_id, {})

    def set_candidates(self, pharmacy_id: str, product_id: str, urls: list[str], when: str, method: str) -> None:
        e = self.entry(pharmacy_id, product_id)
        with self._lock:
            e["candidates"] = urls
            e["candidates_updated_at"] = when
            e["candidates_method"] = method

    def set_matched(self, pharmacy_id: str, product_id: str, url: str | None, level: str | None, when: str) -> None:
        e = self.entry(pharmacy_id, product_id)
        with self._lock:
            if url:
                e["matched_url"] = url
                e["match_level"] = level
                e["last_matched_at"] = when
            else:
                e.pop("matched_url", None)
                e.pop("match_level", None)


class BaseScraper:
    pharmacy_id: str = "base"
    name: str = "Base"
    base_url: str = ""
    platform: str = "generic"
    requires_js: bool = False
    sitemap_urls: list[str] = []
    sitemap_skip_child = None  # regex; None => default di sitemap.py
    search_url_template: str | None = None  # es. "/search?search={q}"
    search_result_selectors: list[str] = ["a[href]"]
    price_css: list[str] = []
    availability_css: list[str] = []
    title_css: list[str] = ["h1"]
    sku_is_minsan: bool = True
    reference_is_minsan: bool = False  # "Codice articolo"/"Riferimento" in pagina = Minsan (9 cifre)
    ids_from_url: bool = True
    min_delay_seconds: float | None = None
    sitemap_refresh_days: int | None = None

    def __init__(self, session: PoliteSession, config: dict, cache: UrlCache, *, now_iso: str,
                 logger: logging.Logger | None = None):
        self.session = session
        self.config = config
        self.cache = cache
        self.now_iso = now_iso
        self.log = logger or logging.getLogger(f"tracker.{self.pharmacy_id}")
        sc = config.get("scraping", {})
        self.max_candidates = int(sc.get("max_candidates_per_product", 3))
        self.tolerance = float(sc.get("price_tolerance_eur", 0.01))
        self.min_price = float(sc.get("min_plausible_price_eur", 0.5))
        self.max_price = float(sc.get("max_plausible_price_eur", 300))
        self.bundle_terms = config.get("global_exclusions", {}).get("bundle_terms", [])
        if self.min_delay_seconds:
            session.set_domain_delay(self.base_url, self.min_delay_seconds)
        self.fatal: str | None = None  # BLOCCATO/LIMITATO: interrompe la farmacia

    # ------------------------------------------------------------------ fetch
    def fetch(self, url: str, accept: str | None = None):
        return self.session.get(url, accept=accept)

    def fetch_bytes(self, url: str) -> bytes:
        res = self.fetch(url, accept="application/xml,text/xml,*/*;q=0.5")
        if not res.ok:
            raise FetchError(f"HTTP {res.status} su {url}")
        return res.content

    # ------------------------------------------------------------------ candidati
    def discover_from_sitemaps(self, products: list[dict]) -> dict[str, list[str]]:
        if not self.sitemap_urls:
            return {}
        kwargs = {"prefilter": keyword_prefilter(products)}
        if self.sitemap_skip_child is not None:
            kwargs["skip_child"] = self.sitemap_skip_child
        urls, errors = fetch_sitemap_urls(self.fetch_bytes, self.sitemap_urls, **kwargs)
        for e in errors:
            self.log.warning("[%s] sitemap: %s", self.pharmacy_id, e)
        return {p["id"]: rank_candidates(urls, p, limit=15) for p in products}

    def search_url(self, query: str) -> str | None:
        if not self.search_url_template:
            return None
        return urljoin(self.base_url + "/", self.search_url_template.format(q=quote_plus(query)).lstrip("/"))

    def discover_from_search(self, product: dict) -> list[str]:
        found: list[str] = []
        for q in product.get("search_queries", [])[:2]:
            url = self.search_url(q)
            if not url:
                break
            if not self.session.is_allowed(url):
                self.log.info("[%s] ricerca vietata da robots.txt: %s", self.pharmacy_id, url)
                break
            res = self.fetch(url)
            if not res.ok:
                continue
            soup = soup_of(res.text)
            for sel in self.search_result_selectors:
                for a in soup.select(sel):
                    href = a.get("href")
                    if href:
                        found.append(clean_result_url(urljoin(res.final_url, href.split("#")[0])))
            ranked = rank_candidates(found, product, limit=15)
            if ranked:
                return ranked
        return rank_candidates(found, product, limit=15)

    def refresh_candidates(self, products: list[dict]) -> None:
        by_sitemap = {}
        if self.sitemap_urls:
            by_sitemap = self.discover_from_sitemaps(products)
            self.cache.ph(self.pharmacy_id)["sitemap_scanned_at"] = self.now_iso
        for p in products:
            cands = by_sitemap.get(p["id"], [])
            method = "sitemap"
            if not cands and self.search_url_template:
                cands = self.discover_from_search(p)
                method = "ricerca interna"
            self.cache.set_candidates(self.pharmacy_id, p["id"], cands, self.now_iso, method)

    def candidates_for(self, product: dict) -> list[str]:
        e = self.cache.entry(self.pharmacy_id, product["id"])
        ordered = []
        if e.get("matched_url"):
            ordered.append(e["matched_url"])
        ordered += [u for u in e.get("candidates", []) if u not in ordered]
        return ordered

    # ------------------------------------------------------------------ pagina
    def extra_price_sources(self, url: str, html: str) -> dict[str, float]:
        """Hook per fonti aggiuntive (es. JSON di piattaforma). Default: nessuna."""
        return {}

    def parse(self, html: str, url: str) -> PageData:
        return extract_page(html, url, price_css=self.price_css, availability_css=self.availability_css,
                            title_css=self.title_css, sku_is_minsan=self.sku_is_minsan,
                            reference_is_minsan=self.reference_is_minsan,
                            ids_from_url_enabled=self.ids_from_url,
                            extra_price_sources=self.extra_price_sources(url, html))

    def evaluate(self, product: dict, page: PageData, final_url: str) -> Offer:
        m: MatchResult = match_product(product, page, self.bundle_terms)
        offer = Offer(product_id=product["id"], pharmacy_id=self.pharmacy_id, status=STATUS_MATCH_UNRELIABLE,
                      url=final_url, title=page.title or None, match_level=m.level, match_reliability=m.reliability,
                      match_reasons=m.reasons, ids_found={"ean": sorted(page.eans), "minsan": sorted(page.minsans)},
                      price_sources=dict(page.price_sources), availability_sources=dict(page.availability_sources),
                      scraped_at=self.now_iso, notes=list(page.notes))
        if page.currency and page.currency.upper() != "EUR":
            offer.notes.append(f"valuta {page.currency} non supportata")
            offer.status = STATUS_PRICE_UNVERIFIED
            return offer
        av = resolve_availability(page.availability_sources)
        offer.availability, offer.availability_method = av.value, av.method
        if av.conflict:
            offer.notes.append("disponibilità: fonti in disaccordo (" + av.method + ")")
        if not m.accepted:
            return offer
        pr = resolve_price(page.price_sources, self.tolerance)
        offer.price_method, offer.price_conflict = pr.method, pr.conflict
        if pr.value is not None and not (self.min_price <= pr.value <= self.max_price):
            offer.notes.append(f"prezzo {pr.value} fuori dall'intervallo plausibile: scartato")
            pr.value, pr.verified = None, False
        if pr.conflict:
            offer.notes.append("prezzo: fonti in disaccordo -> " + pr.method)
        if not page.price_sources:
            offer.notes.append("nessun prezzo esposto in pagina" +
                               (" (prodotto non disponibile)" if offer.availability == UNAVAILABLE else ""))
        if pr.value is None or not pr.verified:
            offer.status = STATUS_PRICE_UNVERIFIED
            offer.price = None  # un prezzo non verificato non viene mai esposto come prezzo
            offer.verified = False
            if pr.value is not None:
                offer.notes.append(f"prezzo trovato solo come testo libero ({pr.value:.2f}): non verificato")
            return offer
        offer.price, offer.verified, offer.status = pr.value, True, STATUS_OK
        return offer

    # ------------------------------------------------------------------ prodotto
    def scrape_product(self, product: dict) -> Offer:
        if self.fatal:
            return self._fatal_offer(product)
        candidates = self.candidates_for(product)[: self.max_candidates + 1]
        if not candidates:
            return Offer(product["id"], self.pharmacy_id, STATUS_NOT_FOUND, scraped_at=self.now_iso,
                         notes=["nessun URL candidato da sitemap/ricerca"])
        accepted: list[Offer] = []
        rejected: list[Offer] = []
        errors: list[str] = []
        for url in candidates:
            try:
                res = self.fetch(url)
            except RobotsDisallowed as exc:
                errors.append(str(exc))
                continue
            except (DomainBlocked, DomainRateLimited) as exc:
                self.fatal = str(exc)
                return self._fatal_offer(product)
            except FetchError as exc:
                errors.append(str(exc))
                continue
            if res.status == 404 or res.status == 410:
                errors.append(f"HTTP {res.status}: {url}")
                continue
            if not res.ok:
                errors.append(f"HTTP {res.status}: {url}")
                continue
            page = self.parse(res.text, res.final_url)
            offer = self.evaluate(product, page, res.final_url)
            if offer.match_level in ACCEPTED_LEVELS:
                accepted.append(offer)
                if offer.match_level == MATCH_ID and offer.status == STATUS_OK and offer.availability != UNAVAILABLE:
                    break  # identificativo confermato e acquistabile: inutile guardare altro
            else:
                rejected.append(offer)
        best = self._choose(accepted)
        if best is not None:
            best.rejected_candidates = [self._brief(o) for o in rejected]
            if errors:
                best.notes += errors
            self.cache.set_matched(self.pharmacy_id, product["id"], best.url, best.match_level, self.now_iso)
            return best
        self.cache.set_matched(self.pharmacy_id, product["id"], None, None, self.now_iso)
        if rejected:
            # una pagina con dati strutturati che non corrisponde è più informativa di una senza dati
            rejected.sort(key=lambda o: 1 if o.status == STATUS_NO_STRUCTURED else 0)
            top = rejected[0]
            if top.status != STATUS_NO_STRUCTURED:
                top.status = STATUS_MATCH_UNRELIABLE
            top.price = None
            top.rejected_candidates = [self._brief(o) for o in rejected[1:]]
            top.notes += errors
            return top
        status = STATUS_ROBOTS if errors and all("robots" in e for e in errors) else (STATUS_ERROR if errors else STATUS_NOT_FOUND)
        return Offer(product["id"], self.pharmacy_id, status, scraped_at=self.now_iso, notes=errors or ["nessuna pagina valida"])

    @staticmethod
    def _brief(o: Offer) -> dict:
        return {"url": o.url, "title": o.title, "match_level": o.match_level, "reasons": o.match_reasons[:3]}

    @staticmethod
    def _choose(offers: list[Offer]) -> Offer | None:
        if not offers:
            return None
        rank = {MATCH_ID: 0}

        def key(o: Offer):
            return (0 if o.status == STATUS_OK else 1,
                    0 if o.availability == AVAILABLE else (1 if o.availability == UNKNOWN else 2),
                    rank.get(o.match_level, 1),
                    o.price if o.price is not None else 1e9)
        return sorted(offers, key=key)[0]

    def _fatal_offer(self, product: dict) -> Offer:
        status = STATUS_RATE_LIMITED if "429" in (self.fatal or "") else STATUS_BLOCKED
        return Offer(product["id"], self.pharmacy_id, status, scraped_at=self.now_iso, notes=[self.fatal or ""])

    # ------------------------------------------------------------------ farmacia
    def run(self, products: list[dict], *, refresh: bool = False, refresh_days: float = 7) -> list[Offer]:
        ph = self.cache.ph(self.pharmacy_id)
        try:
            stale = refresh or any(
                not self.cache.entry(self.pharmacy_id, p["id"]).get("candidates_updated_at")
                or (days_since(self.cache.entry(self.pharmacy_id, p["id"]).get("candidates_updated_at")) or 0)
                > (self.sitemap_refresh_days or refresh_days)
                for p in products)
            if stale:
                self.log.info("[%s] aggiornamento URL candidati (sitemap/ricerca)", self.pharmacy_id)
                self.refresh_candidates(products)
        except (DomainBlocked, DomainRateLimited) as exc:
            self.fatal = str(exc)
        except FetchError as exc:
            self.log.warning("[%s] aggiornamento candidati fallito: %s", self.pharmacy_id, exc)
            ph.setdefault("errors", []).append(str(exc))
        offers = [self.scrape_product(p) for p in products]
        resolve_shared_urls(offers)
        # Se un URL confermato è sparito, riprova una volta con candidati freschi.
        retry = [p for p, o in zip(products, offers)
                 if o.status in (STATUS_NOT_FOUND, STATUS_ERROR) and not refresh and not self.fatal and not stale]
        if retry:
            try:
                self.refresh_candidates(retry)
                for p in retry:
                    idx = products.index(p)
                    offers[idx] = self.scrape_product(p)
            except (DomainBlocked, DomainRateLimited) as exc:
                self.fatal = str(exc)
            except FetchError as exc:
                self.log.warning("[%s] secondo tentativo fallito: %s", self.pharmacy_id, exc)
            resolve_shared_urls(offers)
        return offers


class BlockedScraper(BaseScraper):
    """Farmacia nota come bloccata: non invia richieste e documenta il motivo."""

    reason = "Sito bloccato (anti-bot): nessun tentativo di aggiramento."

    def run(self, products: list[dict], *, refresh: bool = False, refresh_days: float = 7) -> list[Offer]:
        return [Offer(p["id"], self.pharmacy_id, STATUS_BLOCKED, scraped_at=self.now_iso, notes=[self.reason])
                for p in products]


def as_dicts(offers: list[Offer]) -> list[dict[str, Any]]:
    return [o.to_dict() for o in offers]


def _url_key(url: str | None) -> str:
    if not url:
        return ""
    p = urlsplit(url)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def resolve_shared_urls(offers: list[Offer]) -> list[str]:
    """Una pagina non può mai valere per due prodotti diversi (es. P003 Lipocrema 100 ml e
    P004 Crema Mani 100 ml: stessa linea e stesso formato).

    Se la stessa pagina della stessa farmacia è accettata per più prodotti:
    - la tiene SOLO il prodotto con match per identificativo, se è uno solo;
    - altrimenti viene esclusa per tutti (MATCH NON AFFIDABILE).
    Modifica le offerte sul posto e restituisce le note delle esclusioni.
    """
    groups: dict[tuple[str, str], list[Offer]] = {}
    for o in offers:
        if o.match_level in ACCEPTED_LEVELS and o.url:
            groups.setdefault((o.pharmacy_id, _url_key(o.url)), []).append(o)
    notes = []
    for (_ph, _u), group in groups.items():
        if len({o.product_id for o in group}) < 2:
            continue
        by_id = [o for o in group if o.match_level == MATCH_ID]
        keep = by_id[0] if len(by_id) == 1 else None
        for o in group:
            if o is keep:
                continue
            owner = f"già assegnata a {keep.product_id} (match per identificativo)" if keep else \
                "contesa tra più prodotti senza un identificativo che la attribuisca"
            o.notes.append(f"pagina {owner}: esclusa per {o.product_id}")
            o.status, o.price, o.verified = STATUS_MATCH_UNRELIABLE, None, False
            o.match_level = "PAGINA_CONDIVISA"
            o.match_reliability = "esclusa"
            notes.append(o.notes[-1])
    return notes


_SEARCH_PARAMS = {"search", "q", "s", "query", "tag", "description", "page", "limit", "sort", "order", "type",
                  "search_query", "options[prefix]"}


def clean_result_url(url: str) -> str:
    """Toglie dai link dei risultati di ricerca i parametri della ricerca stessa.

    OpenCart aggiunge '?search=<query>' ai link prodotto: lasciato lì, renderebbe ogni
    risultato "pertinente" al filtro sugli slug (bug osservato su farmavillage.it).
    I parametri necessari a identificare il prodotto (es. product_id) restano.
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in _SEARCH_PARAMS]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), ""))


def days_since(value: str | None) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0
