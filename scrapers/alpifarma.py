"""Alpifarma (alpifarma.it) - PrestaShop.

robots.txt vieta la ricerca interna (/ricerca, ?search_query=): si usa solo la sitemap.
JSON-LD Product completo: gtin13 + sku (Minsan) + prezzo + disponibilità.
"""
from .platforms import PrestaShopScraper


class AlpifarmaScraper(PrestaShopScraper):
    pharmacy_id = "alpifarma"
    name = "Alpifarma"
    base_url = "https://www.alpifarma.it"
    sitemap_urls = ["https://www.alpifarma.it/1_index_sitemap.xml"]
    # .current-price contiene sia il prezzo sia la percentuale di sconto: si prende il primo prezzo.
    price_css = [".current-price"]
