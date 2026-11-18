"""Costi di spedizione per farmacia (shipping_config.json).

Regole:
- costo standard sconosciuto => costo None (MAI gratis): il carrello è "incompleto";
- la soglia di spedizione gratuita si applica solo se `free_threshold_verified` è vero;
- soglia "a partire da" = inclusiva (>=); "superiore a" = esclusiva (>), interpretazione prudente;
- `python shipping.py --check` rilegge le pagine ufficiali (se robots.txt lo consente) e
  segnala le differenze; con `--apply` aggiorna i valori trovati in modo univoco.
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from storage import ROOT, now_utc_iso, read_json, today_rome, write_json_atomic

log = logging.getLogger("tracker.shipping")
SHIPPING_PATH = ROOT / "shipping_config.json"


@dataclass
class ShippingQuote:
    pharmacy_id: str
    subtotal: float
    cost: float | None
    complete: bool
    free_shipping_applied: bool = False
    reliability: str = "sconosciuta"
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path = SHIPPING_PATH) -> dict:
    return read_json(path, default={"schema_version": 1, "pharmacies": {}})


def quote(cfg: dict, pharmacy_id: str, subtotal: float, *, cod: bool = False) -> ShippingQuote:
    """Costo di spedizione a domicilio per un ordine con il subtotale indicato."""
    ph = (cfg.get("pharmacies") or {}).get(pharmacy_id)
    subtotal = round(float(subtotal), 2)
    if not ph:
        return ShippingQuote(pharmacy_id, subtotal, None, False, notes=["farmacia assente da shipping_config.json"])
    q = ShippingQuote(pharmacy_id, subtotal, None, False, reliability=ph.get("reliability") or "sconosciuta")
    thr = ph.get("free_threshold_eur")
    if thr is not None and ph.get("free_threshold_verified"):
        inclusive = ph.get("free_threshold_inclusive")
        reached = subtotal >= thr if inclusive else subtotal > thr
        if reached:
            q.cost, q.complete, q.free_shipping_applied = 0.0, True, True
            q.notes.append(f"spedizione gratuita (soglia {'≥' if inclusive else '>'} {thr:.2f} € verificata)")
    elif thr is not None:
        q.notes.append("soglia di spedizione gratuita NON verificata: ignorata")
    if not q.free_shipping_applied:
        std = ph.get("standard_cost_eur")
        if std is None:
            q.notes.append("costo di spedizione sconosciuto (non è gratis)")
            return q
        q.cost, q.complete = round(float(std), 2), True
    for extra in ph.get("extra_costs") or []:
        amount = extra.get("amount_eur")
        if amount is None:
            q.complete = False
            q.notes.append(f"costo extra '{extra.get('label')}' sconosciuto")
            continue
        q.cost = round((q.cost or 0.0) + float(amount), 2)
    if cod:
        if ph.get("cod_cost_eur") is None:
            q.complete = False
            q.notes.append("supplemento contrassegno sconosciuto")
        else:
            q.cost = round((q.cost or 0.0) + float(ph["cod_cost_eur"]), 2)
    return q


def pickup_info(cfg: dict, pharmacy_id: str) -> dict | None:
    ph = (cfg.get("pharmacies") or {}).get(pharmacy_id) or {}
    if not ph.get("pickup_available"):
        return None
    return {"cost": ph.get("pickup_cost_eur"), "locations": ph.get("pickup_locations") or []}


def summary(cfg: dict) -> dict:
    """Vista compatta per data.json/dashboard."""
    out = {}
    for pid, ph in (cfg.get("pharmacies") or {}).items():
        out[pid] = {k: ph.get(k) for k in (
            "standard_cost_eur", "free_threshold_eur", "free_threshold_inclusive", "free_threshold_verified",
            "cod_cost_eur", "pickup_available", "pickup_cost_eur", "source_url", "verified_at", "reliability", "notes")}
    return out


# --------------------------------------------------------------------------- verifica automatica

def _num(text: str) -> float | None:
    try:
        return round(float(text.replace(".", "").replace(",", ".")) if "," in text else float(text), 2)
    except ValueError:
        return None


def extract_from_text(text: str, patterns: dict[str, str]) -> dict[str, float | None]:
    found: dict[str, float | None] = {}
    flat = re.sub(r"\s+", " ", text)
    for key, rx in (patterns or {}).items():
        values = {_num(m.group(1)) for m in re.finditer(rx, flat, re.I)}
        values.discard(None)
        found[key] = values.pop() if len(values) == 1 else None  # più valori diversi = ambiguo
    return found


def check(cfg: dict, session, *, apply: bool = False) -> list[dict]:
    from scrapers.extract import soup_of
    from scrapers.http import FetchError

    report = []
    for pid, ph in (cfg.get("pharmacies") or {}).items():
        ac = ph.get("auto_check") or {}
        url, patterns = ac.get("url"), ac.get("patterns") or {}
        if not url or not patterns:
            continue
        entry = {"pharmacy_id": pid, "url": url, "at": now_utc_iso(), "found": {}, "differences": [], "error": None}
        try:
            if not session.is_allowed(url):
                raise FetchError("pagina vietata da robots.txt")
            res = session.get(url)
            if not res.ok:
                raise FetchError(f"HTTP {res.status}")
            soup = soup_of(res.text)
            for tag in soup.select("script, style, noscript"):
                tag.decompose()
            entry["found"] = extract_from_text(soup.get_text(" ", strip=True), patterns)
        except FetchError as exc:
            entry["error"] = str(exc)
        for key, value in entry["found"].items():
            if value is None:
                entry["differences"].append(f"{key}: non trovato o ambiguo sulla pagina")
            elif ph.get(key) != value:
                entry["differences"].append(f"{key}: configurato {ph.get(key)} / pagina {value}")
                if apply:
                    ph[key] = value
                    ph["verified_at"] = today_rome()
                    ph["reliability"] = "alta"
        if not entry["error"] and not entry["differences"]:
            ph["verified_at"] = today_rome()
        ac["last_check"] = {k: entry[k] for k in ("at", "found", "differences", "error")}
        report.append(entry)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verifica/consulta i costi di spedizione")
    ap.add_argument("--check", action="store_true", help="rilegge le pagine ufficiali e segnala differenze")
    ap.add_argument("--apply", action="store_true", help="con --check: aggiorna i valori trovati in modo univoco")
    ap.add_argument("--quote", nargs=2, metavar=("FARMACIA", "SUBTOTALE"), help="calcola la spedizione per un ordine")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    if args.quote:
        print(quote(cfg, args.quote[0], float(args.quote[1].replace(",", "."))).to_dict())
        return 0
    if args.check:
        from scrapers.http import PoliteSession
        session = PoliteSession(read_json(ROOT / "config.json", required=True)["http"])
        report = check(cfg, session, apply=args.apply)
        for r in report:
            status = "ERRORE " + r["error"] if r["error"] else ("OK" if not r["differences"] else "DIFFERENZE")
            print(f"{r['pharmacy_id']:20} {status} {r['found']} {r['differences']}")
        cfg["updated_at"] = now_utc_iso()
        write_json_atomic(SHIPPING_PATH, cfg)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
