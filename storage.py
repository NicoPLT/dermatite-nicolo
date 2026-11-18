"""I/O JSON atomico, storico prezzi append-only e utilità di data/ora.

Regole:
- ogni scrittura passa da un file temporaneo nella stessa cartella + os.replace (atomico);
- lo storico non perde mai voci: le giornate passate non vengono mai modificate;
- se il file esistente è illeggibile/corrotto NON lo si sovrascrive (si solleva un errore).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ROME = ZoneInfo("Europe/Rome")


class StorageError(RuntimeError):
    """Errore di lettura/scrittura che deve fermare l'aggiornamento di un file."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_utc_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def today_rome() -> str:
    return datetime.now(ROME).date().isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: str | None) -> float | None:
    dt = parse_iso(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now_utc() - dt).total_seconds() / 86400.0


def read_json(path: str | Path, default: Any = None, *, required: bool = False) -> Any:
    """Legge un JSON. Se manca restituisce `default` (o errore se required).

    Se il file esiste ma è corrotto solleva sempre StorageError: meglio fermarsi
    che sovrascrivere dati validi con un default vuoto.
    """
    p = Path(path)
    if not p.exists():
        if required:
            raise StorageError(f"File obbligatorio mancante: {p}")
        return default
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise StorageError(f"Impossibile leggere {p}: {exc}") from exc


def _stage(path: Path, obj: Any) -> str:
    """Serializza e valida `obj`, lo scrive in un file temporaneo accanto a `path` e ne restituisce il nome."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)  # NaN/oggetti -> ValueError/TypeError
    json.loads(payload)  # validazione: non scriviamo mai JSON non valido
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(payload)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except BaseException:
        _unlink(tmp)
        raise
    return tmp


def _unlink(tmp: str) -> None:
    try:
        os.unlink(tmp)
    except OSError:
        pass


def write_json_atomic(path: str | Path, obj: Any) -> None:
    """Scrive `obj` in `path` in modo atomico (temp file + fsync + os.replace)."""
    p = Path(path)
    tmp = _stage(p, obj)
    try:
        os.replace(tmp, p)
    except BaseException:
        _unlink(tmp)
        raise


def write_many_atomic(items: dict[Path, Any]) -> list[Path]:
    """Pubblica più file JSON come un'unica operazione, per quanto il file system lo consente.

    1. staging: TUTTI i file vengono serializzati, validati e scritti in temporanei; se uno
       fallisce, i temporanei sono rimossi e nessun file pubblicato viene toccato;
    2. pubblicazione: os.replace di ciascun temporaneo, nell'ordine dato (di solito storico,
       poi data.json, poi metadati). Un errore qui (raro: disco/permessi) solleva StorageError
       che elenca esattamente quali file sono stati aggiornati e quali no.
    Restituisce i percorsi pubblicati.
    """
    staged: list[tuple[str, Path]] = []
    try:
        for path, obj in items.items():
            staged.append((_stage(Path(path), obj), Path(path)))
    except BaseException:
        for tmp, _ in staged:
            _unlink(tmp)
        raise
    done: list[Path] = []
    for i, (tmp, path) in enumerate(staged):
        try:
            os.replace(tmp, path)
        except OSError as exc:
            for t, _ in staged[i:]:
                _unlink(t)
            raise StorageError(f"Pubblicazione interrotta su {path.name}: aggiornati {[p.name for p in done] or 'nessuno'}, "
                               f"NON aggiornati {[p.name for _, p in staged[i:]]} ({exc})") from exc
        done.append(path)
    return done


# --------------------------------------------------------------------------- storico

# Il formato fa parte della chiave: dopo un cambio formato (es. P003 50 ml -> 100 ml) una voce
# del nuovo formato non sostituisce mai quella del vecchio nello stesso giorno.
HISTORY_KEYS = ("date", "product_id", "pharmacy_id", "format")
HISTORY_SCHEMA = 2


def history_entry(date: str, product_id: str, pharmacy_id: str, price: float | None,
                  shipping: float | None, total: float | None, availability: str | None,
                  match_level: str | None = None, format_label: str | None = None) -> dict:
    return {
        "date": date,
        "product_id": product_id,
        "pharmacy_id": pharmacy_id,
        "format": format_label,
        "price": price,
        "shipping": shipping,
        "total": total,
        "availability": availability,
        "match": match_level,
    }


def format_for_date(product: dict, date: str) -> str | None:
    """Formato monitorato per un prodotto in una certa data (usa format_history)."""
    for old in sorted(product.get("format_history") or [], key=lambda h: h.get("until") or ""):
        if old.get("until") and date <= old["until"]:
            return old.get("label")
    return (product.get("format") or {}).get("label")


def migrate_history(hist: dict | None, products: list[dict]) -> tuple[dict, int]:
    """Aggiunge `format` alle voci che ne sono prive, SENZA rimuovere o alterare altro.

    Le voci senza formato sono state scritte prima dell'introduzione del campo: ricevono il
    formato valido in quella data secondo `format_history` (es. P003 fino al 18/11/2026 = '50 ml').
    Idempotente. Restituisce (storico, numero di voci migrate).
    """
    hist = hist if isinstance(hist, dict) else {"schema_version": HISTORY_SCHEMA, "entries": []}
    by_id = {p["id"]: p for p in products}
    migrated = 0
    entries = []
    for e in hist.get("entries") or []:
        if not e.get("format") and e.get("product_id") in by_id:
            e = {**e, "format": format_for_date(by_id[e["product_id"]], e.get("date") or "")}
            migrated += 1
        entries.append(e)
    out = dict(hist)
    out["entries"] = entries
    out["schema_version"] = HISTORY_SCHEMA
    if len(out["entries"]) != len(hist.get("entries") or []):  # difesa in profondità
        raise StorageError("La migrazione dello storico non deve cambiare il numero di voci")
    return out, migrated


def merge_history(existing: dict | None, new_entries: Iterable[dict], *, today: str) -> dict:
    """Unisce nuove voci allo storico.

    - una voce per (data, prodotto, farmacia): una seconda esecuzione nello stesso
      giorno sostituisce solo la voce di OGGI (nessun duplicato inutile);
    - le voci dei giorni passati non vengono mai toccate né rimosse;
    - voci senza prezzo verificato vengono ignorate.
    """
    hist = existing if isinstance(existing, dict) else {"schema_version": 1, "entries": []}
    entries = list(hist.get("entries") or [])
    index = {tuple(e.get(k) for k in HISTORY_KEYS): i for i, e in enumerate(entries)}
    added = 0
    for e in new_entries:
        if e.get("price") is None:
            continue
        if e.get("date") != today:
            raise StorageError("Le nuove voci di storico devono avere la data di oggi")
        key = tuple(e.get(k) for k in HISTORY_KEYS)
        if key in index:
            entries[index[key]] = e
        else:
            index[key] = len(entries)
            entries.append(e)
            added += 1
    entries.sort(key=lambda e: (e.get("date") or "", e.get("product_id") or "", e.get("pharmacy_id") or ""))
    out = dict(hist)
    out["schema_version"] = max(int(hist.get("schema_version") or 1), 1)
    out["entries"] = entries
    out["updated_at"] = now_utc_iso() if added or new_entries else hist.get("updated_at")
    return out


def prepare_history(path: str | Path, new_entries: list[dict], *, today: str | None = None,
                    products: list[dict] | None = None) -> tuple[dict, dict | None]:
    """Calcola in memoria lo storico aggiornato SENZA scriverlo.

    Restituisce (storico_attuale, storico_nuovo); storico_nuovo è None se non ci sono voci
    nuove con prezzo. Solleva StorageError se il file attuale è illeggibile o se il risultato
    sarebbe più corto dell'attuale. Con `products` migra prima le voci senza formato.
    """
    today = today or today_rome()
    valid = [e for e in new_entries if e.get("price") is not None]
    existing = read_json(path, default={"schema_version": HISTORY_SCHEMA, "entries": []})
    before = len(existing.get("entries") or [])
    if not valid:
        return existing, None
    current = existing
    if products:
        current, _ = migrate_history(existing, products)
    merged = merge_history(current, valid, today=today)
    if len(merged["entries"]) < before:  # difesa in profondità
        raise StorageError("Lo storico risultante sarebbe più corto di quello esistente: scrittura annullata")
    return existing, merged


def update_history_file(path: str | Path, new_entries: list[dict], *, today: str | None = None,
                        products: list[dict] | None = None) -> int:
    """Aggiorna price_history.json. Restituisce il numero di voci dopo l'aggiornamento.

    Non scrive nulla se non ci sono voci nuove con prezzo; non scrive mai uno
    storico più corto di quello esistente. Con `products` migra prima le voci senza formato.
    """
    existing, merged = prepare_history(path, new_entries, today=today, products=products)
    if merged is None:
        return len(existing.get("entries") or [])
    write_json_atomic(path, merged)
    return len(merged["entries"])
