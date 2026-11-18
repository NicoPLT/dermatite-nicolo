"""Estrazione di prezzo, disponibilità, titolo e identificativi da una pagina prodotto.

Priorità delle fonti: JSON-LD → microdata → meta tag → HTML strutturato → selettori CSS → testo.
Il prezzo NON viene preso dalla prima fonte disponibile: si raccolgono tutte le fonti e si
confrontano (vedi `resolve_price`). Su verafarma.it, ad esempio, il JSON-LD riporta un prezzo
diverso da quello mostrato (vedi docs/analisi_siti.md).
"""
from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

from bs4 import BeautifulSoup

try:  # lxml è più veloce e tollerante; html.parser come ripiego
    import lxml  # noqa: F401
    _PARSER = "lxml"
except ImportError:  # pragma: no cover
    _PARSER = "html.parser"

SOURCE_ORDER = ["json_ld", "microdata", "meta", "html", "css", "platform_json", "text"]
STRUCTURED_SOURCES = {"json_ld", "microdata", "meta", "html", "css", "platform_json"}

AVAILABLE = "DISPONIBILE"
UNAVAILABLE = "NON DISPONIBILE"
UNKNOWN = "SCONOSCIUTA"

_PRICE_RX = re.compile(r"(?<![\d.,])(\d{1,3}(?:[.\s]\d{3})*(?:,\d{1,2})|\d+(?:[.,]\d{1,2})?)(?![\d])")
_EURO_PRICE_RX = re.compile(
    r"(?:€|eur)\s*(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{2})?)|"
    r"(\d{1,3}(?:[.\s]\d{3})*(?:,\d{2})|\d+(?:[.,]\d{2})?)\s*(?:€|eur\b)", re.I)


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, _PARSER)


# --------------------------------------------------------------------------- prezzi

def parse_price(value: Any, *, allow_zero: bool = False) -> float | None:
    """Converte '12,09 €', '€ 1.234,50', '12.09', 12.09 in float. Mai 0: restituisce None.

    `allow_zero=True` solo per importi che possono valere davvero 0 (es. spedizione gratuita
    dichiarata nei dati strutturati): mai per i prezzi dei prodotti.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        num = float(value)
    else:
        text = str(value).replace("\xa0", " ").strip()
        if not text:
            return None
        m = _EURO_PRICE_RX.search(text)
        token = (m.group(1) or m.group(2)) if m else None
        if token is None:
            m2 = _PRICE_RX.search(text)
            if not m2:
                return None
            token = m2.group(1)
        token = token.replace(" ", "")
        if "," in token and "." in token:
            token = token.replace(".", "").replace(",", ".") if token.rfind(",") > token.rfind(".") else token.replace(",", "")
        elif "," in token:
            token = token.replace(",", ".")
        elif token.count(".") > 1:
            token = token.replace(".", "")
        try:
            num = float(token)
        except ValueError:
            return None
    if num != num or num < 0 or (num == 0 and not allow_zero):  # 0, negativi e NaN non sono prezzi
        return None
    return round(num, 2)


# --------------------------------------------------------------------------- disponibilità

_AVAIL_SCHEMA = {
    "instock": AVAILABLE, "limitedavailability": AVAILABLE, "onlineonly": AVAILABLE, "instoreonly": AVAILABLE,
    "preorder": UNAVAILABLE, "presale": UNAVAILABLE, "backorder": UNAVAILABLE, "madetoorder": UNAVAILABLE,
    "outofstock": UNAVAILABLE, "soldout": UNAVAILABLE, "discontinued": UNAVAILABLE,
    "in stock": AVAILABLE, "out of stock": UNAVAILABLE, "instock_": AVAILABLE,
    "true": AVAILABLE, "false": UNAVAILABLE,
}
_UNAVAIL_TEXT = re.compile(r"non\s+(?:più\s+|piu\s+)?disponibil|esaurit|sold\s*out|out\s+of\s+stock|non\s+acquistabile|"
                           r"terminat|attualmente\s+non|avvisami|fuori\s+produzione|momentaneamente\s+non", re.I)
_AVAIL_TEXT = re.compile(r"disponibil|in\s+stock|pronta\s+consegna|spedizione\s+(?:immediata|in\s+24)|"
                         r"aggiungi\s+al\s+carrello|ultim[ie]\s+\d+\s+pezz|\d+\s+articol", re.I)


def normalize_availability(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return AVAILABLE if value else UNAVAILABLE
    text = str(value).strip()
    if not text:
        return None
    key = text.rsplit("/", 1)[-1].replace(" ", "").lower() if "schema.org" in text.lower() else text.lower()
    if key in _AVAIL_SCHEMA:
        return _AVAIL_SCHEMA[key]
    if _UNAVAIL_TEXT.search(text):
        return UNAVAILABLE
    if _AVAIL_TEXT.search(text):
        return AVAILABLE
    return None


# --------------------------------------------------------------------------- identificativi

def gtin_valid(code: str) -> bool:
    if not code.isdigit() or len(code) not in (8, 12, 13, 14):
        return False
    digits = [int(c) for c in code]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def normalize_gtin(code: Any) -> str | None:
    if code is None:
        return None
    digits = re.sub(r"\D", "", str(code))
    if len(digits) == 14 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 12:
        digits = "0" + digits
    return digits if gtin_valid(digits) else None


def normalize_minsan(code: Any, *, labeled: bool = False) -> str | None:
    """Minsan = 9 cifre (a volte con prefisso 'IT' o 'A').

    Le 8 cifre (zero iniziale perso) si accettano solo se il codice è etichettato
    esplicitamente come Minsan/AIC: altrimenti uno SKU interno di 8 cifre
    diventerebbe un falso Minsan e genererebbe falsi conflitti.
    """
    if code is None:
        return None
    text = str(code).strip().upper()
    text = re.sub(r"^(IT|A)(?=\d)", "", text)
    if re.fullmatch(r"\d{9}", text):
        return text
    if labeled and re.fullmatch(r"\d{8}", text):
        return text.zfill(9)
    return None


_LABELED_ID_RX = re.compile(
    r"(minsan|cod(?:ice)?\.?\s*minsan|aic|ean(?:13)?|gtin|codice\s+a\s+barre|barcode|cod(?:ice)?\.?\s*prodotto|"
    r"cod(?:ice)?\.?\s*articolo|referenza|riferimento)\s*[:#]?\s*([A-Z]{0,2}\d{8,14})", re.I)


def ids_from_url(url: str) -> tuple[set[str], set[str]]:
    path = unquote(urlsplit(url).path)
    eans = {c for c in re.findall(r"(?<!\d)(\d{13})(?!\d)", path) if gtin_valid(c)}
    minsans = set()
    for m in re.finditer(r"(?:^|[/\-_])(?:IT)?(\d{9})(?=$|[/\-_.])", path):
        minsans.add(m.group(1))
    return eans, minsans


# --------------------------------------------------------------------------- JSON-LD

def _strip_json_noise(s: str) -> str:
    """Rimuove commenti // e /* */ e virgole finali FUORI dalle stringhe JSON.

    Scansione carattere per carattere: un "//" dentro una stringa (es. un URL) non viene toccato.
    """
    out, i, n, in_str, esc = [], 0, len(s), False, False
    while i < n:
        c = s[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = n if j < 0 else j
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j < 0 else j + 2
        elif c == ",":
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j < n and s[j] in "}]":
                i += 1  # virgola finale: scartata
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def loads_jsonld(raw: str) -> Any | None:
    """JSON-LD tollerante: BOM, commenti HTML/JS, virgole finali, entità HTML, caratteri di controllo.

    Restituisce None (mai un'eccezione) se il blocco resta illeggibile.
    """
    if not raw:
        return None
    text = raw.strip().lstrip("﻿").strip()
    text = text.removeprefix("<!--").removesuffix("-->").strip()
    text = re.sub(r"^\s*<!\[CDATA\[|\]\]>\s*$", "", text).strip()
    attempts = [text, _strip_json_noise(text)]
    if "&quot;" in text or "&#" in text or "&amp;" in text:
        unescaped = html_lib.unescape(text)
        attempts += [unescaped, _strip_json_noise(unescaped)]
    for candidate in attempts:
        try:
            return json.loads(candidate, strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
    return None


def iter_jsonld(soup: BeautifulSoup) -> Iterable[dict]:
    for tag in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        raw = tag.string if tag.string is not None else tag.get_text()
        if not raw or not raw.strip():
            continue
        data = loads_jsonld(raw)
        if data is not None:
            yield from _flatten_ld(data)


def _flatten_ld(data: Any, depth: int = 0) -> Iterable[dict]:
    """Appiattisce @graph, liste, ItemList (itemListElement/ListItem.item) e mainEntity."""
    if depth > 6:
        return
    if isinstance(data, list):
        for item in data:
            yield from _flatten_ld(item, depth + 1)
    elif isinstance(data, dict):
        if "@graph" in data:
            yield from _flatten_ld(data["@graph"], depth + 1)
            return
        yield data
        types = _types(data)
        if "ItemList" in types:
            for el in data.get("itemListElement") or []:
                if isinstance(el, dict):
                    child = el.get("item") if isinstance(el.get("item"), dict) else el
                    child["__from_item_list__"] = True  # prodotti "correlati" o risultati: mai il prodotto principale per default
                    yield from _flatten_ld(child, depth + 1)
        main = data.get("mainEntity")
        if isinstance(main, (dict, list)):
            yield from _flatten_ld(main, depth + 1)


def _types(item: dict) -> set[str]:
    t = item.get("@type")
    if isinstance(t, list):
        return {str(x) for x in t}
    return {str(t)} if t else set()


def _norm_url(u: str | None) -> str:
    if not u:
        return ""
    p = urlsplit(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def main_product_ld(items: list[dict], page_url: str) -> dict | None:
    products = [i for i in items if _types(i) & {"Product", "ProductGroup", "IndividualProduct"}]
    if not products:
        return None
    target = _norm_url(page_url)
    for p in products:
        offers = p.get("offers")
        urls = [p.get("url"), p.get("@id")]
        if isinstance(offers, dict):
            urls.append(offers.get("url"))
        if any(_norm_url(u) == target for u in urls if isinstance(u, str)):
            return p
    # un ProductGroup "contiene" le varianti: va preferito a una sua variante elencata a parte
    top = [p for p in products if not p.get("__from_item_list__")] or products
    groups = [p for p in top if "ProductGroup" in _types(p) or p.get("hasVariant")]
    return groups[0] if groups else top[0]


def _ld_offers(product: dict) -> list[dict]:
    offers = product.get("offers")
    if isinstance(offers, dict):
        if "AggregateOffer" in _types(offers) and isinstance(offers.get("offers"), list):
            return [o for o in offers["offers"] if isinstance(o, dict)] or [offers]
        return [offers]
    if isinstance(offers, list):
        return [o for o in offers if isinstance(o, dict)]
    return []


_NOT_SELLING_PRICE = re.compile(r"ListPrice|StrikethroughPrice|MSRP|SRP|InvoicePrice|MinimumAdvertisedPrice", re.I)


def _public_spec_price(spec: Any) -> float | None:
    """Prezzo da priceSpecification (oggetto o lista), solo se c'è UN prezzo pubblico univoco.

    Esclusi: prezzi riservati agli iscritti (validForMemberTier), prezzi di listino/barrati
    (priceType ListPrice/StrikethroughPrice...). Es. 1000farmacie.it: 15,89 € pubblico + 14,78 € "care-plan".
    """
    specs = spec if isinstance(spec, list) else ([spec] if isinstance(spec, dict) else [])
    public = set()
    for s in specs:
        if not isinstance(s, dict) or s.get("validForMemberTier") or s.get("eligibleCustomerType"):
            continue
        if _NOT_SELLING_PRICE.search(str(s.get("priceType") or "")):
            continue
        if "UnitPriceSpecification" in _types(s) and s.get("priceType"):
            continue
        p = parse_price(s.get("price"))
        if p is not None:
            public.add(p)
    return next(iter(public)) if len(public) == 1 else None


def ld_price_and_availability(product: dict) -> tuple[float | None, str | None, str | None]:
    """Prezzo, disponibilità e valuta dal JSON-LD. Se ci sono più offerte con prezzi
    diversi il prezzo è ambiguo e si restituisce None (mai indovinare)."""
    offers = _ld_offers(product)
    prices, avails, currency = set(), set(), None
    for o in offers:
        if "AggregateOffer" in _types(o):
            lo, hi = parse_price(o.get("lowPrice")), parse_price(o.get("highPrice"))
            if lo is not None and (hi is None or hi == lo):
                prices.add(lo)
            elif lo is not None:
                prices.update({lo, hi})
        else:
            p = parse_price(o.get("price"))
            if p is None:
                p = _public_spec_price(o.get("priceSpecification"))
            if p is not None:
                prices.add(p)
        a = normalize_availability(o.get("availability"))
        if a:
            avails.add(a)
        currency = currency or o.get("priceCurrency")
    price = next(iter(prices)) if len(prices) == 1 else None
    avail = next(iter(avails)) if len(avails) == 1 else (None if not avails else UNKNOWN)
    return price, avail, currency


def ld_ids(product: dict, *, sku_is_minsan: bool) -> tuple[set[str], set[str]]:
    eans, minsans = set(), set()
    blocks = [product] + _ld_offers(product)
    for b in blocks:
        for key in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8", "ean"):
            g = normalize_gtin(b.get(key))
            if g:
                eans.add(g)
        if sku_is_minsan:
            for key in ("sku", "mpn", "productID"):
                v = b.get(key)
                if isinstance(v, (str, int)):
                    g = normalize_gtin(v) if len(re.sub(r"\D", "", str(v))) >= 12 else None
                    if g:
                        eans.add(g)
                    else:
                        m = normalize_minsan(v)
                        if m:
                            minsans.add(m)
    return eans, minsans


def ld_brand(product: dict) -> str | None:
    b = product.get("brand")
    if isinstance(b, dict):
        b = b.get("name")
    if isinstance(b, list) and b:
        b = b[0].get("name") if isinstance(b[0], dict) else b[0]
    return str(b).strip() if b else None


def ld_shipping(offer: dict) -> list[dict]:
    """OfferShippingDetails -> [{"cost": float|None, "currency": str|None, "country": str|None}]."""
    sd = offer.get("shippingDetails")
    items = sd if isinstance(sd, list) else ([sd] if isinstance(sd, dict) else [])
    out = []
    for d in items:
        if not isinstance(d, dict):
            continue
        rate = d.get("shippingRate") if isinstance(d.get("shippingRate"), dict) else {}
        cost = parse_price(rate.get("value", rate.get("price")), allow_zero=True)
        currency = rate.get("currency") or rate.get("priceCurrency")
        dest = d.get("shippingDestination")
        dests = dest if isinstance(dest, list) else ([dest] if isinstance(dest, dict) else [])
        countries = []
        for x in dests:
            c = x.get("addressCountry") if isinstance(x, dict) else None
            if isinstance(c, dict):
                c = c.get("name") or c.get("identifier")
            if c:
                countries.append(str(c).upper())
        if currency and str(currency).upper() != "EUR":
            cost = None  # mai convertire valute: costo ignoto
        for c in countries or [None]:
            out.append({"cost": cost, "currency": currency, "country": c})
    return out


@dataclass
class LdVariant:
    """Un'unità acquistabile descritta dal JSON-LD (variante di ProductGroup o offerta distinta)."""

    name: str | None
    eans: set[str]
    skus: set[str]
    price: float | None
    price_ambiguous: bool
    availability: str | None
    currency: str | None
    url: str | None
    shipping: list[dict]
    source: str


def _gtins(block: dict) -> set[str]:
    out = set()
    for key in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8", "ean"):
        g = normalize_gtin(block.get(key))
        if g:
            out.add(g)
    return out


def _skus(block: dict) -> set[str]:
    return {str(block[k]).strip() for k in ("sku", "mpn", "productID") if isinstance(block.get(k), (str, int)) and str(block[k]).strip()}


def _variant_from(product: dict, source: str, name: str | None = None) -> LdVariant:
    price, avail, cur = ld_price_and_availability(product)
    offers = _ld_offers(product)
    ambiguous = price is None and any(parse_price(o.get("price") or o.get("lowPrice")) is not None for o in offers)
    eans, skus = _gtins(product), _skus(product)
    url = product.get("url")
    ship: list[dict] = []
    for o in offers:
        eans |= _gtins(o)
        skus |= _skus(o)
        url = url or o.get("url")
        ship += ld_shipping(o)
    return LdVariant(name or product.get("name"), eans, skus, price, ambiguous, avail, cur, url, ship, source)


def ld_variants(product: dict) -> list[LdVariant]:
    """Varianti acquistabili del prodotto principale.

    - ProductGroup/hasVariant: una variante per ogni Product figlio;
    - Product con più Offer che si distinguono per gtin/sku/nome: una variante per offerta
      (es. stesso prodotto in 50 ml e 100 ml nella stessa pagina);
    - altrimenti: una sola variante (il prodotto stesso).
    """
    variants = [v for v in (product.get("hasVariant") or []) if isinstance(v, dict)]
    if variants:
        return [_variant_from(v, "hasVariant") for v in variants]
    offers = _ld_offers(product)
    identities = {(frozenset(_gtins(o)), frozenset(_skus(o)), str(o.get("name") or "")) for o in offers}
    if len(offers) > 1 and len(identities) > 1:
        out = []
        for o in offers:
            pseudo = {"name": o.get("name") or product.get("name"), "offers": o}
            v = _variant_from(pseudo, "offer")
            v.eans |= _gtins(o) or (set() if len(offers) > 1 else _gtins(product))
            out.append(v)
        return out
    return [_variant_from(product, "product")]


# --------------------------------------------------------------------------- PageData

@dataclass
class PageData:
    url: str
    titles: dict[str, str] = field(default_factory=dict)  # fonte -> titolo
    brand: str | None = None
    eans: set[str] = field(default_factory=set)
    minsans: set[str] = field(default_factory=set)
    price_sources: dict[str, float] = field(default_factory=dict)
    availability_sources: dict[str, str] = field(default_factory=dict)
    currency: str | None = None
    notes: list[str] = field(default_factory=list)
    variants: list[LdVariant] = field(default_factory=list)   # dal JSON-LD del prodotto principale
    structured: bool = False   # trovato almeno un dato strutturato di prodotto (JSON-LD, microdata o meta)
    skus: set[str] = field(default_factory=set)   # sku/mpn grezzi (non interpretati come Minsan)

    @property
    def title(self) -> str:
        for k in ("json_ld", "h1", "microdata", "og", "html_title"):
            if self.titles.get(k):
                return self.titles[k]
        return ""


def _text(el) -> str:
    return el.get_text(" ", strip=True) if el is not None else ""


def extract_page(html: str, url: str, *, price_css: Iterable[str] = (), availability_css: Iterable[str] = (),
                 title_css: Iterable[str] = ("h1",), sku_is_minsan: bool = True, reference_is_minsan: bool = False,
                 ids_from_url_enabled: bool = True, extra_price_sources: dict[str, float] | None = None,
                 allow_text_fallback: bool = True) -> PageData:
    soup = soup_of(html)
    page = PageData(url=url)

    # 1) JSON-LD
    items = list(iter_jsonld(soup))
    prod = main_product_ld(items, url)
    if prod:
        page.structured = True
        page.variants = ld_variants(prod)
        page.skus = _skus(prod) | {s for o in _ld_offers(prod) for s in _skus(o)}
        if prod.get("name"):
            page.titles["json_ld"] = str(prod["name"]).strip()
        page.brand = ld_brand(prod)
        price, avail, cur = ld_price_and_availability(prod)
        if price is not None:
            page.price_sources["json_ld"] = price
        elif _ld_offers(prod):
            page.notes.append("JSON-LD: prezzo assente o ambiguo (più offerte con prezzi diversi)")
        if avail:
            page.availability_sources["json_ld"] = avail
        page.currency = cur
        e, m = ld_ids(prod, sku_is_minsan=sku_is_minsan)
        page.eans |= e
        page.minsans |= m

    # 2) microdata (limitata al blocco Product se presente)
    scope = soup.find(attrs={"itemtype": re.compile(r"schema\.org/Product", re.I)}) or soup
    el = scope.find(attrs={"itemprop": "price"})
    if el is not None:
        p = parse_price(el.get("content") or _text(el))
        if p is not None:
            page.price_sources["microdata"] = p
    el = scope.find(attrs={"itemprop": "availability"})
    if el is not None:
        a = normalize_availability(el.get("href") or el.get("content") or _text(el))
        if a:
            page.availability_sources["microdata"] = a
    el = scope.find(attrs={"itemprop": "name"})
    if el is not None and el.name not in ("meta", "link"):
        page.titles["microdata"] = _text(el)
    for key in ("gtin13", "gtin", "gtin12", "gtin14"):
        el = scope.find(attrs={"itemprop": key})
        g = normalize_gtin(el.get("content") or _text(el)) if el is not None else None
        if g:
            page.eans.add(g)
    if sku_is_minsan:
        el = scope.find(attrs={"itemprop": "sku"})
        m = normalize_minsan(el.get("content") or _text(el)) if el is not None else None
        if m:
            page.minsans.add(m)

    # 3) meta tag
    metas = {}
    for m in soup.find_all("meta"):
        key = (m.get("property") or m.get("name") or "").strip().lower()
        if key and m.get("content") is not None:
            metas.setdefault(key, m.get("content"))
    for key in ("product:price:amount", "og:price:amount", "product:sale_price:amount"):
        p = parse_price(metas.get(key))
        if p is not None:
            page.price_sources["meta"] = p
            break
    for key in ("product:availability", "og:availability"):
        a = normalize_availability(metas.get(key))
        if a:
            page.availability_sources["meta"] = a
            break
    if metas.get("og:title"):
        page.titles["og"] = metas["og:title"].strip()
    if not page.brand and metas.get("product:brand"):
        page.brand = metas["product:brand"].strip()
    if metas.get("product:retailer_item_id") and sku_is_minsan:
        m = normalize_minsan(metas["product:retailer_item_id"])
        if m:
            page.minsans.add(m)

    # 4) HTML strutturato: titolo e codici etichettati nella zona prodotto
    for sel in title_css:
        el = soup.select_one(sel)
        if el is not None and _text(el):
            page.titles["h1"] = _text(el)
            break
    if soup.title and soup.title.string:
        page.titles["html_title"] = soup.title.string.strip()
    main = soup.select_one("main, #main, #content, .product-detail, .product-info, .product") or soup
    for m in _LABELED_ID_RX.finditer(_text(main)[:20000]):
        label, code = m.group(1).lower(), m.group(2)
        if "minsan" in label or label == "aic":
            v = normalize_minsan(code, labeled=True)
            if v:
                page.minsans.add(v)
        elif any(k in label for k in ("ean", "gtin", "barre", "barcode")):
            v = normalize_gtin(code)
            if v:
                page.eans.add(v)
        elif reference_is_minsan and any(k in label for k in ("articolo", "prodotto", "riferimento", "referenza")):
            v = normalize_minsan(code)  # solo 9 cifre esatte
            if v:
                page.minsans.add(v)

    # 5) selettori CSS specifici della farmacia (prezzo visibile)
    for sel in price_css:
        for el in soup.select(sel):
            p = parse_price(el.get("content") or el.get("data-price-amount") or _text(el))
            if p is not None:
                page.price_sources["css"] = p
                break
        if "css" in page.price_sources:
            break
    for sel in availability_css:
        el = soup.select_one(sel)
        if el is not None:
            a = normalize_availability(el.get("content") or el.get("href") or _text(el))
            if a:
                page.availability_sources["css"] = a
                break

    if "microdata" in page.price_sources or "meta" in page.price_sources:
        page.structured = True

    # 6) ripiego testuale: solo se nessun'altra fonte ha trovato un prezzo (mai nello scraper generico)
    if not page.price_sources and allow_text_fallback:
        h1 = soup.select_one("h1")
        if h1 is not None:
            following = []
            for node in h1.find_all_next(string=True, limit=80):
                following.append(str(node))
            m = _EURO_PRICE_RX.search(" ".join(following))
            if m:
                p = parse_price(m.group(0))
                if p is not None:
                    page.price_sources["text"] = p

    if extra_price_sources:
        page.price_sources.update({k: v for k, v in extra_price_sources.items() if v is not None})

    if ids_from_url_enabled:
        e, m = ids_from_url(url)
        page.eans |= e
        if sku_is_minsan:
            page.minsans |= m
    return page


# --------------------------------------------------------------------------- risoluzione conflitti

@dataclass
class Resolution:
    value: Any
    verified: bool
    method: str
    conflict: bool
    discarded: dict[str, Any] = field(default_factory=dict)


def resolve_price(sources: dict[str, float], tolerance: float = 0.01,
                  priority: list[str] | None = None) -> Resolution:
    """Confronta le fonti di prezzo.

    - nessuna fonte -> None;
    - tutte concordi -> prezzo verificato (se almeno una fonte è strutturata);
    - disaccordo -> vince il gruppo con più fonti; a parità -> NON verificato;
    - solo testo libero -> mai verificato.
    """
    priority = priority or SOURCE_ORDER
    vals = {k: v for k, v in sources.items() if v is not None}
    if not vals:
        return Resolution(None, False, "nessuna fonte", False)
    groups: list[list[str]] = []
    for k in sorted(vals, key=lambda s: priority.index(s) if s in priority else 99):
        for g in groups:
            if abs(vals[g[0]] - vals[k]) <= tolerance + 1e-9:
                g.append(k)
                break
        else:
            groups.append([k])
    groups.sort(key=lambda g: -len(g))
    if len(groups) > 1 and len(groups[0]) == len(groups[1]):
        return Resolution(None, False, "fonti in disaccordo senza maggioranza: " +
                          ", ".join(f"{k}={vals[k]:.2f}" for k in vals), True, dict(vals))
    best = groups[0]
    value = vals[best[0]]
    discarded = {k: vals[k] for g in groups[1:] for k in g}
    structured = any(k in STRUCTURED_SOURCES for k in best)
    method = "+".join(best)
    if discarded:
        method = f"maggioranza {method} (scartato: " + ", ".join(f"{k}={v:.2f}" for k, v in discarded.items()) + ")"
    return Resolution(value, structured, method, bool(discarded), discarded)


def resolve_availability(sources: dict[str, str], priority: list[str] | None = None) -> Resolution:
    priority = priority or SOURCE_ORDER
    vals = {k: v for k, v in sources.items() if v and v != UNKNOWN}
    if not vals:
        return Resolution(UNKNOWN, False, "nessuna fonte", False)
    counts: dict[str, list[str]] = {}
    for k, v in vals.items():
        counts.setdefault(v, []).append(k)
    ranked = sorted(counts.items(), key=lambda kv: -len(kv[1]))
    if len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1]):
        return Resolution(UNKNOWN, False, "fonti in disaccordo: " + ", ".join(f"{k}={v}" for k, v in vals.items()), True)
    value, keys = ranked[0]
    return Resolution(value, True, "+".join(sorted(keys, key=lambda s: priority.index(s) if s in priority else 99)),
                      len(ranked) > 1)
