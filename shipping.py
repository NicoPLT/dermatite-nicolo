"""Costi di spedizione per farmacia (shipping_config.json).

Regole:
- costo standard sconosciuto => costo None (MAI gratis): il carrello è "incompleto";
- la soglia di spedizione gratuita si applica solo se `free_threshold_verified` è vero;
- soglia "a partire da" = inclusiva (>=); "superiore a" = esclusiva (>), interpretazione prudente;
- ogni tariffa ha uno STATO (vedi shipping_status): solo "verificata" entra nel carrello
  verificato; "scaduta", "discordante" e "sconosciuta" rendono il carrello incompleto
  (il costo noto resta visibile tra le alternative, mai come vincitore certo);
- `python shipping.py --check` rilegge le pagine ufficiali (se robots.txt lo consente) e
  segnala le differenze; con `--apply` aggiorna i valori trovati in modo univoco.
  Un valore ambiguo o non più trovato sulla pagina rende la tariffa "discordante".
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from storage import ROOT, days_since, now_utc_iso, read_json, today_rome, write_json_atomic
from validation import parse_day

log = logging.getLogger("tracker.shipping")
SHIPPING_PATH = ROOT / "shipping_config.json"

STATUS_VERIFIED = "verificata"
STATUS_EXPIRED = "scaduta"
STATUS_DISCORDANT = "discordante"
STATUS_UNKNOWN = "sconosciuta"
DEFAULT_MAX_AGE_DAYS = 60


@dataclass
class ShippingQuote:
    pharmacy_id: str
    subtotal: float
    cost: float | None
    complete: bool
    free_shipping_applied: bool = False
    reliability: str = "sconosciuta"
    notes: list[str] = field(default_factory=list)
    status: str = STATUS_UNKNOWN

    def to_dict(self) -> dict:
        return asdict(self)


def load_config(path: str | Path = SHIPPING_PATH) -> dict:
    return read_json(path, default={"schema_version": 1, "pharmacies": {}})


def max_age_days(cfg: dict) -> float:
    return float(cfg.get("max_age_days") or DEFAULT_MAX_AGE_DAYS)


def shipping_status(ph: dict | None, *, max_age: float = DEFAULT_MAX_AGE_DAYS) -> tuple[str, str]:
    """(stato, motivazione) della tariffa di una farmacia.

    - sconosciuta: tariffa standard assente (e nessuna soglia verificata) o data di verifica mancante;
    - discordante: l'ultimo controllo automatico ha trovato valori diversi, ambigui o assenti
      sulla pagina ufficiale (e non c'è stata una verifica manuale successiva);
    - scaduta: verifica più vecchia di `max_age` giorni;
    - verificata: altrimenti.
    """
    if not ph:
        return STATUS_UNKNOWN, "farmacia assente da shipping_config.json"
    has_threshold = ph.get("free_threshold_verified") and ph.get("free_threshold_eur") is not None
    if ph.get("standard_cost_eur") is None and not has_threshold:
        return STATUS_UNKNOWN, "costo di spedizione standard sconosciuto"
    verified = parse_day(ph.get("verified_at"))
    if verified is None:
        return STATUS_UNKNOWN, "data di verifica mancante: tariffa non attendibile"
    last = (ph.get("auto_check") or {}).get("last_check") or {}
    checked = parse_day(last.get("at"))
    if last.get("differences") and (checked is None or verified <= checked):
        return STATUS_DISCORDANT, (f"controllo del {checked or 'n.d.'} sulla pagina ufficiale: "
                                   + "; ".join(last["differences"][:3]))
    age = days_since(ph.get("verified_at"))
    if age is not None and age > max_age:
        return STATUS_EXPIRED, f"ultima verifica il {verified.isoformat()}, oltre {max_age:.0f} giorni fa"
    return STATUS_VERIFIED, f"verificata il {verified.isoformat()}"


def quote(cfg: dict, pharmacy_id: str, subtotal: float, *, cod: bool = False) -> ShippingQuote:
    """Costo di spedizione a domicilio per un ordine con il subtotale indicato.

    Il costo noto viene sempre calcolato; `complete` è vero solo se la tariffa è "verificata"
    e non mancano componenti (costi extra, contrassegno).
    """
    ph = (cfg.get("pharmacies") or {}).get(pharmacy_id)
    subtotal = round(float(subtotal), 2)
    if not ph:
        return ShippingQuote(pharmacy_id, subtotal, None, False, notes=["farmacia assente da shipping_config.json"])
    status, reason = shipping_status(ph, max_age=max_age_days(cfg))
    q = ShippingQuote(pharmacy_id, subtotal, None, False, reliability=ph.get("reliability") or "sconosciuta",
                      status=status)
    q = _price(ph, q, cod)
    if status != STATUS_VERIFIED and q.cost is not None:
        q.complete = False
        q.notes.append(f"tariffa {status}: {reason}")
    return q


def _price(ph: dict, q: ShippingQuote, cod: bool) -> ShippingQuote:
    subtotal = q.subtotal
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
    """Vista compatta per data.json/dashboard, con stato e motivazione della tariffa."""
    out = {}
    for pid, ph in (cfg.get("pharmacies") or {}).items():
        out[pid] = {k: ph.get(k) for k in (
            "standard_cost_eur", "free_threshold_eur", "free_threshold_inclusive", "free_threshold_verified",
            "cod_cost_eur", "pickup_available", "pickup_cost_eur", "source_url", "verified_at", "reliability", "notes")}
        out[pid]["status"], out[pid]["status_reason"] = shipping_status(ph, max_age=max_age_days(cfg))
    return out


def unverified(cfg: dict, pharmacy_ids) -> list[str]:
    """Farmacie (tra quelle indicate) la cui tariffa non è 'verificata'."""
    phs = cfg.get("pharmacies") or {}
    return sorted(pid for pid in pharmacy_ids
                  if shipping_status(phs.get(pid), max_age=max_age_days(cfg))[0] != STATUS_VERIFIED)


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
        entry = {"pharmacy_id": pid, "url": url, "at": now_utc_iso(), "found": {}, "differences": [], "applied": [],
                 "error": None}
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
                # valore sparito o ambiguo: la tariffa precedente non è più confermata (mai applicato)
                entry["differences"].append(f"{key}: non trovato o ambiguo sulla pagina")
            elif ph.get(key) != value:
                msg = f"{key}: configurato {ph.get(key)} / pagina {value}"
                if apply:
                    ph[key] = value
                    entry["applied"].append(msg)
                else:
                    entry["differences"].append(msg)
        if not entry["error"] and not entry["differences"]:
            ph["verified_at"] = today_rome()
            if entry["applied"]:
                ph["reliability"] = "alta"
        # Le differenze non risolte restano nel controllo: shipping_status() le legge e la tariffa
        # diventa "discordante" finché un controllo o una verifica manuale successiva non la conferma.
        ac["last_check"] = {k: entry[k] for k in ("at", "found", "differences", "applied", "error")}
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
