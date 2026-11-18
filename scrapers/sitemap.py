"""Lettura delle sitemap XML e selezione degli URL candidati per ciascun prodotto.

Le sitemap sono il metodo di scoperta preferito: sono pubblicate apposta per i crawler e,
su molti siti, la ricerca interna è vietata da robots.txt. Sono però grandi: vanno lette
al massimo una volta ogni `sitemap_refresh_days` giorni, e gli URL utili vanno in cache.

Sicurezza: l'XML è letto con un'espressione regolare sui soli tag <loc> (nessun parser XML,
quindi nessuna espansione di entità o DTD esterni); il gzip è decompresso con un tetto;
indici annidati sono seguiti fino a `max_depth` livelli e `max_children` sitemap figlie,
senza mai rileggere la stessa sitemap (difesa dai cicli).
"""
from __future__ import annotations

import html
import logging
import re
from typing import Callable, Iterable
from urllib.parse import unquote, urlsplit

from .extract import ids_from_url
from .http import safe_gunzip

log = logging.getLogger("tracker.sitemap")

_LOC_RX = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S | re.I)
DEFAULT_SKIP_CHILD = re.compile(r"blog|brand|categor|pagine|pages|advisor|collections|agentic|cms|manufacturer|supplier|news|article", re.I)


def parse_sitemap(body: bytes | str) -> tuple[bool, list[str]]:
    """Restituisce (è_indice, lista_loc)."""
    if isinstance(body, bytes):
        if body[:2] == b"\x1f\x8b":
            body = safe_gunzip(body)
        body = body.decode("utf-8", errors="replace")
    is_index = bool(re.search(r"<sitemapindex[\s>]", body, re.I))
    locs = [html.unescape(m.strip()) for m in _LOC_RX.findall(body)]
    return is_index, [l for l in locs if l.startswith("http")]


PRODUCT_CHILD = re.compile(r"product|prodott|articol|item|catalog", re.I)


def fetch_sitemap_urls(get: Callable[[str], bytes], roots: Iterable[str], *, skip_child: re.Pattern | None = DEFAULT_SKIP_CHILD,
                       max_children: int = 250, prefilter: Callable[[str], bool] | None = None,
                       prioritize: re.Pattern | None = None, max_depth: int = 3) -> tuple[list[str], list[str]]:
    """Scarica le sitemap partendo dalle radici. `get(url) -> bytes` deve essere educato.

    Restituisce (url_prodotto_filtrati, errori). Con `prefilter` si tengono in memoria solo
    gli URL potenzialmente utili (le sitemap possono superare 200.000 voci).
    """
    queue: list[tuple[str, int]] = [(r, 0) for r in roots]
    seen: set[str] = set()
    urls: list[str] = []
    errors: list[str] = []
    children = 0
    while queue:
        sm, depth = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        try:
            is_index, locs = parse_sitemap(get(sm))
        except Exception as exc:  # noqa: BLE001 - una sitemap rotta non ferma le altre
            errors.append(f"{sm}: {exc}")
            log.warning("Sitemap non leggibile %s: %s", sm, exc)
            continue
        if is_index:
            if depth >= max_depth:
                errors.append(f"{sm}: indice annidato oltre {max_depth} livelli, ignorato")
                continue
            if prioritize is not None:  # con un tetto di richieste, prima le sitemap "prodotti"
                locs = sorted(locs, key=lambda u: 0 if prioritize.search(urlsplit(u).path) else 1)
            for child in locs:
                if child in seen or (skip_child is not None and skip_child.search(urlsplit(child).path)):
                    continue
                if children >= max_children:
                    errors.append(f"limite di {max_children} sitemap figlie raggiunto")
                    break
                children += 1
                queue.append((child, depth + 1))
        else:
            urls.extend(u for u in locs if prefilter is None or prefilter(u))
    return list(dict.fromkeys(urls)), errors


def _slug(url: str) -> str:
    p = urlsplit(url)
    return unquote(p.path + ("?" + p.query if p.query else "")).lower()


def candidate_score(url: str, product: dict) -> int | None:
    """Punteggio del candidato (None = scartato). L'identificativo nell'URL vince su tutto."""
    eans, minsans = ids_from_url(url)
    if (product.get("ean") and product["ean"] in eans) or (product.get("minsan") and product["minsan"] in minsans):
        return 1000
    if eans or minsans:
        # URL con un identificativo diverso: quasi certamente un altro prodotto/formato.
        other = (eans - {product.get("ean")}) or (minsans - {product.get("minsan")})
        if other:
            return None
    f = product.get("url_filter", {})
    slug = _slug(url)
    if any(tok not in slug for tok in f.get("all", [])):
        return None
    if f.get("any") and not any(tok in slug for tok in f["any"]):
        return None
    if any(tok in slug for tok in f.get("exclude", [])):
        return None
    score = 100
    if any(h in slug for h in f.get("format_hints", [])):
        score += 50
    score += 20 * sum(1 for tok in f.get("prefer", []) if tok in slug)
    score -= min(len(slug) // 20, 20)  # a parità, preferisci slug più corti (meno "rumore")
    return score


def rank_candidates(urls: Iterable[str], product: dict, limit: int | None = None) -> list[str]:
    scored = []
    for u in dict.fromkeys(urls):
        s = candidate_score(u, product)
        if s is not None:
            scored.append((s, u))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    out = [u for _, u in scored]
    return out[:limit] if limit else out


def keyword_prefilter(products: list[dict]) -> Callable[[str], bool]:
    """Filtro rapido per non tenere in memoria centinaia di migliaia di URL."""
    keys = set()
    for p in products:
        f = p.get("url_filter", {})
        keys.update(f.get("all", [])[:1] or f.get("any", []))
        if p.get("ean"):
            keys.add(p["ean"])
        if p.get("minsan"):
            keys.add(p["minsan"])
    keys = {k.lower() for k in keys if k}

    def _f(url: str) -> bool:
        s = url.lower()
        return any(k in s for k in keys)
    return _f
