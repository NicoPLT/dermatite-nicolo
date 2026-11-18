"""eFarma (efarma.com) - Magento 2 dietro Cloudflare.

robots.txt vieta /catalogsearch/ e ?q=: si usa la sitemap.
robots.txt blocca i crawler di addestramento AI (GPTBot, ClaudeBot...): questo tracker usa
un User-Agent proprio e rispetta le regole del gruppo "User-agent: *".
Condizioni d'uso: uso consentito "meramente personale e non commerciale".
"""
import re

from .platforms import MagentoScraper


class EfarmaScraper(MagentoScraper):
    pharmacy_id = "efarma"
    name = "eFarma"
    base_url = "https://www.efarma.com"
    sitemap_urls = ["https://www.efarma.com/media/google_sitemap_index.xml"]
    sitemap_skip_child = re.compile(r"Blog|Brand|Pagine|Categorie", re.I)
