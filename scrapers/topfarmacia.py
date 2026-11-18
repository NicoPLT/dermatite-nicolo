"""Top Farmacia (topfarmacia.it) - BLOCCATO.

Il 18/11/2026 il sito ha risposto "403 Access Denied" (edge Akamai) già su /robots.txt e
sulla homepage, con User-Agent identificabile. Per regola del progetto non si tenta alcun
aggiramento (niente UA da browser, niente browser stealth).

Lo scraper esiste per documentare il caso e per un'eventuale riverifica manuale:
`python scraper.py --pharmacy topfarmacia --probe` effettua UNA richiesta a robots.txt
per vedere se il blocco è stato rimosso.
"""
from .base import BlockedScraper


class TopfarmaciaScraper(BlockedScraper):
    pharmacy_id = "topfarmacia"
    name = "Top Farmacia"
    base_url = "https://www.topfarmacia.it"
    platform = "sconosciuta (Akamai)"
    reason = ("HTTP 403 Access Denied (Akamai) su robots.txt e homepage il 18/11/2026: "
              "sito non monitorabile senza aggirare protezioni anti-bot.")
