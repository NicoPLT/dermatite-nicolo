"""Registro degli scraper: id farmacia (come in pharmacies.json) -> classe."""
from .additional import (ClubfarmaScraper, FarmaciaIrisDianaScraper, FarmacieVigoritoScraper, FarmafidesScraper,
                         FarmasaveScraper, FarmastrikeScraper, FarmavillageScraper, MeafarmaScraper, VerafarmaScraper)
from .alpifarma import AlpifarmaScraper
from .base import BaseScraper, Offer, UrlCache
from .efarma import EfarmaScraper
from .redcare import RedcareScraper
from .topfarmacia import TopfarmaciaScraper

REGISTRY: dict[str, type[BaseScraper]] = {
    cls.pharmacy_id: cls
    for cls in (
        TopfarmaciaScraper, AlpifarmaScraper, RedcareScraper, EfarmaScraper,
        FarmasaveScraper, VerafarmaScraper, FarmastrikeScraper, MeafarmaScraper, FarmavillageScraper,
        FarmaciaIrisDianaScraper, ClubfarmaScraper, FarmafidesScraper, FarmacieVigoritoScraper,
    )
}

__all__ = ["REGISTRY", "BaseScraper", "Offer", "UrlCache"]
