"""Redcare (redcare.it) - piattaforma proprietaria.

robots.txt vieta /search.htm: si usa la sitemap (78 file prodotto, ~230.000 URL),
letta al massimo una volta a settimana. Gli URL contengono il Minsan (es. IT977658020).

La pagina mostra più varianti (es. 100 ml e "set da 2") con lo stesso markup di prezzo:
il selettore CSS sarebbe ambiguo, quindi si usa solo il JSON-LD, che descrive la variante
della pagina (sku = Minsan).
"""
import re

from .base import BaseScraper


class RedcareScraper(BaseScraper):
    pharmacy_id = "redcare"
    name = "Redcare"
    base_url = "https://www.redcare.it"
    platform = "Redcare (proprietaria)"
    sitemap_urls = ["https://www.redcare.it/sitemap/sitemap.xml"]
    sitemap_skip_child = re.compile(r"advisor|brands|categor", re.I)
    price_css: list[str] = []
    availability_css: list[str] = []
