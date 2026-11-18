"""Comportamenti comuni per piattaforma e-commerce.

Ogni farmacia eredita da una di queste classi e dichiara solo ciò che la distingue.
Selettori verificati sulle pagine reali il 18/11/2026 (vedi docs/analisi_siti.md).
"""
from __future__ import annotations

import re

from .base import BaseScraper


class PrestaShopScraper(BaseScraper):
    """PrestaShop 1.7/8. La ricerca (/ricerca, ?search_query=) è in genere vietata da robots.txt."""

    platform = "PrestaShop"
    price_css = [".current-price [itemprop=price]", ".current-price-value", ".product-prices .current-price",
                 ".current-price"]
    # #product-availability è spesso vuoto; il blocco del carrello dice "Aggiungi al carrello" oppure
    # "Prodotto non disponibile" (su alpifarma il meta og:availability resta "instock" anche se esaurito).
    availability_css = ["#product-availability", ".product-add-to-cart", ".product-actions"]
    sitemap_skip_child = re.compile(r"(?!)")  # le sitemap PrestaShop non distinguono: leggile tutte


class ShopwareScraper(BaseScraper):
    """Shopware 6. Ricerca consentita (/search?search=...).

    ATTENZIONE: su verafarma/farmasave il JSON-LD può riportare un prezzo diverso da
    quello mostrato; il prezzo visibile (.product-detail-price) e il microdata fanno maggioranza.
    """

    platform = "Shopware 6"
    search_url_template = "/search?search={q}"
    search_result_selectors = ["a.product-name", ".product-box a.product-image-link", ".product-info a[href]"]
    price_css = [".product-detail-price"]
    availability_css = [".product-detail-buy .delivery-information", ".delivery-information"]
    sitemap_skip_child = re.compile(r"(?!)")


class OpenCartScraper(BaseScraper):
    """OpenCart 2/3. Ricerca consentita (index.php?route=product/search)."""

    platform = "OpenCart"
    search_url_template = "/index.php?route=product/search&search={q}"
    search_result_selectors = [".product-thumb .caption a[href]", ".product-thumb h4 a[href]", ".product-layout a[href]"]
    price_css = [".product-info .price-new", "#product .price-new", ".price-new", "ul.list-unstyled h2",
                 ".product-price", "#product .price"]
    availability_css = ["#product .stock", ".product-stock", ".stock-status"]
    sitemap_skip_child = re.compile(r"(?!)")


class MagentoScraper(BaseScraper):
    """Magento 2 (tema Hyvä su efarma). Ricerca vietata da robots.txt (/catalogsearch/)."""

    platform = "Magento 2"
    price_css = ["[data-price-type=finalPrice] [data-price-amount]", "[data-price-type=finalPrice] .price"]
    availability_css = [".stock span", ".stock"]


class ShopifyScraper(BaseScraper):
    """Shopify. Rate limit severo (429 'local_rate_limited'): ritmo più lento e sitemap rara.

    Lo SKU su Shopify è libero (non necessariamente un Minsan): si usano solo gtin/barcode.
    """

    platform = "Shopify"
    search_result_selectors = ["a[href*='/products/']"]
    price_css = [".price__sale .price-item--sale", ".price-item--regular", "[data-product-price]", ".product__price"]
    availability_css = ["[data-add-to-cart-text]", "button[name=add]"]
    sku_is_minsan = False
    ids_from_url = False
    # Il 18/11/2026 farmafides/vigorito hanno risposto 429 con Retry-After: 60 anche a ritmi di 6 s:
    # si adotta il ritmo indicato dal server (1 richiesta ogni ~65 s). ~6 richieste/giorno per dominio.
    min_delay_seconds = 65.0
    sitemap_refresh_days = 30
    sitemap_skip_child = re.compile(r"blog|pages|collections|agentic", re.I)
