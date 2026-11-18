"""Validazione centralizzata di prezzi, valute, date e dei JSON pubblicati.

Usata da scraper.py prima di pubblicare (nessun file viene sostituito se i controlli falliscono)
e dal workflow GitHub Actions:

  python validation.py                 # valida i JSON del repository
  python validation.py --against-git   # in più: lo storico non deve essere più corto di quello in HEAD
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parent
SUPPORTED_CURRENCY = "EUR"
AVAILABLE = "DISPONIBILE"
PUBLIC_JSON = ("data.json", "price_history.json", "pharmacies.json", "shipping_config.json", "config.json",
               "product_urls.json", "store_prices.json", "physical_pharmacies.json")


class ValidationError(RuntimeError):
    """Uno snapshot non rispetta il contratto dati: non va pubblicato."""


# --------------------------------------------------------------------------- valori singoli

def valid_price(value: Any) -> bool:
    """Prezzo utilizzabile: numero finito maggiore di zero (mai bool, mai stringa)."""
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def is_eur(currency: Any) -> bool:
    return isinstance(currency, str) and currency.strip().upper() == SUPPORTED_CURRENCY


def parse_day(value: Any) -> date | None:
    """Data ISO (AAAA-MM-GG o data/ora ISO 8601). None se assente o non valida."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def normalize_format(label: Any) -> str:
    """'100 ml', '100ml', '100 ML' -> '100ml' (confronto tra formati)."""
    return "".join(str(label or "").lower().split()).replace(",", ".")


# --------------------------------------------------------------------------- snapshot

def iter_carts(carts: dict | None) -> Iterator[tuple[str, dict]]:
    """Tutti i carrelli VERIFICATI pubblicati in data.json -> carts (esclusi incompleti e incerti)."""
    carts = carts or {}
    for key in ("best_overall", "best_single_pharmacy", "best_max_two_pharmacies", "best_any_number"):
        if isinstance(carts.get(key), dict):
            yield key, carts[key]
    for mode, cart in (carts.get("by_mode") or {}).items():
        if isinstance(cart, dict):
            yield f"by_mode.{mode}", cart
    for name, ranking in (carts.get("rankings") or {}).items():
        for i, cart in enumerate(ranking or []):
            if isinstance(cart, dict):
                yield f"rankings.{name}[{i}]", cart


def validate_data(data: dict, *, active_ids: set[str] | None = None) -> list[str]:
    """Problemi bloccanti di data.json (lista vuota = pubblicabile)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["data.json non è un oggetto JSON"]
    offers = data.get("offers")
    if not isinstance(offers, list):
        return ["data.json: 'offers' non è una lista"]
    usable: set[tuple[str, str]] = set()
    for o in offers:
        tag = f"offerta {o.get('product_id')}@{o.get('pharmacy_id')}"
        if o.get("price") is not None and not valid_price(o["price"]):
            problems.append(f"{tag}: prezzo non valido ({o['price']!r})")
        if not o.get("usable"):
            continue
        if not valid_price(o.get("price")):
            problems.append(f"{tag}: utilizzabile senza un prezzo valido")
        if not is_eur(o.get("currency", SUPPORTED_CURRENCY)):
            problems.append(f"{tag}: valuta {o.get('currency')!r} diversa da EUR")
        if o.get("availability") != AVAILABLE:
            problems.append(f"{tag}: utilizzabile con disponibilità {o.get('availability')!r}")
        if active_ids is not None and o.get("pharmacy_id") not in active_ids:
            problems.append(f"{tag}: farmacia non ACTIVE tra le offerte utilizzabili")
        usable.add((o.get("product_id"), o.get("pharmacy_id")))
    for c in data.get("candidate_offers") or []:
        if c.get("usable"):
            problems.append(f"offerta candidata {c.get('product_id')}@{c.get('pharmacy_id')} marcata utilizzabile")
    for name, cart in iter_carts(data.get("carts")):
        for it in cart.get("items") or []:
            if not valid_price(it.get("price")):
                problems.append(f"carrello {name}: prezzo non valido per {it.get('product_id')}")
            if it.get("channel") in ("domicilio", "ritiro") and (it.get("product_id"), it.get("seller")) not in usable:
                problems.append(f"carrello {name}: usa un'offerta esclusa ({it.get('product_id')}@{it.get('seller')})")
            if it.get("channel") in ("domicilio", "ritiro") and it.get("availability") != AVAILABLE:
                problems.append(f"carrello {name}: disponibilità non confermata per {it.get('product_id')}")
    return problems


def history_problems(old: dict | None, new: dict | None) -> list[str]:
    """Lo storico non deve mai accorciarsi né contenere prezzi non validi o voci senza formato."""
    problems: list[str] = []
    old_entries = (old or {}).get("entries") or []
    new_entries = (new or {}).get("entries")
    if not isinstance(new_entries, list):
        return ["price_history.json: 'entries' non è una lista"]
    if len(new_entries) < len(old_entries):
        problems.append(f"storico accorciato: {len(old_entries)} -> {len(new_entries)} voci")
    # senza il formato: la migrazione aggiunge `format` alle voci storiche, non le sostituisce
    keys = lambda e: (e.get("date"), e.get("product_id"), e.get("pharmacy_id"))  # noqa: E731
    missing = {keys(e) for e in old_entries} - {keys(e) for e in new_entries}
    if missing:
        problems.append(f"storico: {len(missing)} voci precedenti scomparse")
    for e in new_entries:
        if not valid_price(e.get("price")):
            problems.append(f"storico {e.get('date')} {e.get('product_id')}@{e.get('pharmacy_id')}: prezzo non valido")
        if not e.get("format"):
            problems.append(f"storico {e.get('date')} {e.get('product_id')}@{e.get('pharmacy_id')}: formato mancante")
    return problems


def raise_if(problems: list[str], what: str) -> None:
    if problems:
        raise ValidationError(f"{what}: " + "; ".join(problems[:10]) + (" ..." if len(problems) > 10 else ""))


# --------------------------------------------------------------------------- CLI

def _git_history() -> dict | None:
    try:
        out = subprocess.run(["git", "show", "HEAD:price_history.json"], cwd=ROOT, capture_output=True,
                             check=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return json.loads(out.stdout.decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Valida i JSON pubblicati dal tracker")
    ap.add_argument("--against-git", action="store_true", help="confronta lo storico con la versione in HEAD")
    args = ap.parse_args(argv)
    problems: list[str] = []
    loaded: dict[str, Any] = {}
    for name in PUBLIC_JSON:
        path = ROOT / name
        if not path.exists():
            continue
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"{name}: JSON non valido ({exc})")
    registry = loaded.get("pharmacies.json") or {}
    active = {p["id"] for p in registry.get("pharmacies", []) if p.get("status") == "ACTIVE"} if registry else None
    if isinstance(loaded.get("data.json"), dict) and loaded["data.json"].get("offers") is not None:
        problems += validate_data(loaded["data.json"], active_ids=active)
    if "price_history.json" in loaded:
        old = _git_history() if args.against_git else None
        problems += history_problems(old, loaded["price_history.json"])
    for p in problems:
        print("ERRORE:", p)
    if problems:
        return 1
    data = loaded.get("data.json") or {}
    scope = (data.get("scope") or {}).get("type")
    print(f"JSON validi ({len(loaded)} file). Stato: {data.get('status')}, scansione: {scope or 'n.d.'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
