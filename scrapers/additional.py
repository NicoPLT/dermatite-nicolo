"""Scraper delle altre farmacie. Una classe indipendente per farmacia.

Per aggiungere una farmacia: crea una classe che eredita dalla piattaforma giusta,
registrala in scrapers/__init__.py e aggiungila a pharmacies.json (vedi CLAUDE.md).
"""
import re

from .platforms import OpenCartScraper, PrestaShopScraper, ShopifyScraper, ShopwareScraper


# ----------------------------------------------------------------------------- Shopware 6

class FarmasaveScraper(ShopwareScraper):
    """farmasave.it - stesso catalogo/piattaforma di verafarma.it. JSON-LD con Minsan come sku."""

    pharmacy_id = "farmasave"
    name = "Farmasave"
    base_url = "https://www.farmasave.it"
    sitemap_urls = ["https://www.farmasave.it/sitemap.xml"]


class VerafarmaScraper(ShopwareScraper):
    """verafarma.it - JSON-LD con prezzo inaffidabile (vedi docs/analisi_siti.md)."""

    pharmacy_id = "verafarma"
    name = "Verafarma"
    base_url = "https://verafarma.it"
    sitemap_urls = ["https://verafarma.it/sitemap.xml"]


# ----------------------------------------------------------------------------- OpenCart

class FarmastrikeScraper(OpenCartScraper):
    """farmastrike.it - JSON-LD con Minsan come sku; slug identici a meafarma.it."""

    pharmacy_id = "farmastrike"
    name = "FarmaStrike"
    base_url = "https://farmastrike.it"
    sitemap_urls = ["https://farmastrike.it/index.php?route=extension/feed/google_sitemap"]


class MeafarmaScraper(OpenCartScraper):
    """meafarma.it - la pagina spedizioni è esclusa da robots.txt (costi da config manuale)."""

    pharmacy_id = "meafarma"
    name = "Meafarma"
    base_url = "https://meafarma.it"
    sitemap_urls = ["https://meafarma.it/sitemap.xml"]


class FarmavillageScraper(OpenCartScraper):
    """farmavillage.it - nessun dato strutturato, nessuna sitemap: ricerca interna + HTML.

    Il certificato TLS è servito senza intermedio: la sessione completa la catena via AIA.
    I prezzi in elenco mostrano anche l'imponibile ("IVA esclusa"): si usa solo il prezzo IVA inclusa.
    """

    pharmacy_id = "farmavillage"
    name = "Farmavillage"
    base_url = "https://farmavillage.it"
    sitemap_urls: list[str] = []
    # Scontato: <span class="new-price">€ 17,99</span><span class="old-price">€ 26,50</span>
    # Non scontato: "€ 29,50 (prezzo di listino)" nella prima voce di ul.price-product.
    price_css = ["ul.price-product .new-price", "ul.price-product li"]
    # Disponibilità: solo la presenza del pulsante carrello (segnale debole, nessun dato strutturato).
    availability_css = ["#product .stock", "#button-cart"]
    # "Codice articolo: 982981666" = Minsan (verificato su P001 e P005 il 18/11/2026).
    reference_is_minsan = True


# ----------------------------------------------------------------------------- PrestaShop

class FarmaciaIrisDianaScraper(PrestaShopScraper):
    """farmaciairisdiana.it - nessun JSON-LD Product: meta product:price:amount + HTML."""

    pharmacy_id = "farmaciairisdiana"
    name = "Farmacia Iris Diana"
    base_url = "https://farmaciairisdiana.it"
    sitemap_urls = ["https://farmaciairisdiana.it/1_index_sitemap.xml"]
    price_css = [".product-prices .current-price", ".current-price"]


class ClubfarmaScraper(PrestaShopScraper):
    """clubfarma.it - JSON-LD completo (gtin13 + Minsan)."""

    pharmacy_id = "clubfarma"
    name = "Clubfarma"
    base_url = "https://www.clubfarma.it"
    sitemap_urls = ["https://www.clubfarma.it/1_index_sitemap.xml"]
    price_css = [".current-price"]


# ----------------------------------------------------------------------------- Shopify

class FarmafidesScraper(ShopifyScraper):
    """farmafides.it - Shopify; ricerca consentita da robots.txt."""

    pharmacy_id = "farmafides"
    name = "Farmafides"
    base_url = "https://farmafides.it"
    sitemap_urls = ["https://farmafides.it/sitemap.xml"]
    search_url_template = "/search?q={q}&type=product"


class FarmacieVigoritoScraper(ShopifyScraper):
    """farmacievigorito.it - Shopify; /search vietato da robots.txt: solo sitemap (mensile)."""

    pharmacy_id = "farmacievigorito"
    name = "Farmacie Vigorito"
    base_url = "https://farmacievigorito.it"
    sitemap_urls = ["https://farmacievigorito.it/sitemap.xml"]
    sitemap_skip_child = re.compile(r"blog|pages|collections|agentic", re.I)
