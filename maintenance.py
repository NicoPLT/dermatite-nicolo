"""Operazioni di manutenzione dei dati.

  python maintenance.py format-change P003     # dopo aver cambiato il formato in config.json
  python maintenance.py migrate-history        # aggiunge `format` alle voci di storico che non lo hanno

`format-change` presuppone che config.json contenga già il NUOVO formato e, in `format_history`,
il formato precedente con la data `until`. Esegue, in modo idempotente e con scritture atomiche:
  1. migrazione dello storico: le voci senza formato ricevono quello valido nella loro data
     (nessuna voce viene cancellata o modificata nel prezzo);
  2. data.json: scarta le offerte del prodotto che non sono del formato corrente, azzera il suo
     miglior prezzo e ricalcola SEMPRE carrelli e statistiche, anche se non resta nessuna offerta
     (le offerte nuove arriveranno al prossimo run); registra formato precedente, nuovo e data;
  3. product_urls.json: azzera candidati e URL confermati SOLO di quel prodotto, così il prossimo
     run li ricerca da capo per il nuovo formato.
"""
from __future__ import annotations

import argparse
import sys

from storage import ROOT, StorageError, migrate_history, now_utc_iso, read_json, write_json_atomic

PATHS = {
    "config": ROOT / "config.json",
    "data": ROOT / "data.json",
    "history": ROOT / "price_history.json",
    "urls": ROOT / "product_urls.json",
    "shipping": ROOT / "shipping_config.json",
    "pharmacies": ROOT / "pharmacies.json",
}


def do_migrate_history(products: list[dict]) -> dict:
    hist = read_json(PATHS["history"], default=None)
    if not hist:
        return {"history_entries": 0, "migrated": 0}
    before = len(hist.get("entries") or [])
    migrated_hist, n = migrate_history(hist, products)
    after = len(migrated_hist["entries"])
    if after != before:
        raise StorageError(f"migrazione anomala: {before} -> {after} voci")
    if n:
        write_json_atomic(PATHS["history"], migrated_hist)
    return {"history_entries": after, "migrated": n}


def purge_offers(data: dict, product: dict, config: dict) -> dict:
    """Scarta da data.json le offerte del prodotto non del formato corrente e ricalcola tutto.

    Idempotente: se il cambio formato è già registrato e non c'è nulla da rimuovere, non modifica nulla.
    """
    from optimizer import offer_matches_current_format
    from physical_pharmacies import load_physical
    from snapshot import recompute

    pid = product["id"]
    products = config["products"]
    removed = [o for o in data.get("offers") or []
               if o.get("product_id") == pid and not offer_matches_current_format(o, products)]
    new_label = product["format"]["label"]
    already = any(fc.get("product_id") == pid and fc.get("to") == new_label for fc in data.get("format_changes") or [])
    if not removed and already:
        return {"removed_offers": 0}
    data["errors"] = [e for e in data.get("errors") or [] if e.get("product_id") != pid]
    old = (product.get("format_history") or [{}])[-1]
    if not already:
        data.setdefault("format_changes", []).append({
            "product_id": pid, "from": old.get("label"), "to": new_label, "at": now_utc_iso(),
            "removed_offers": len(removed),
            "note": f"Offerte {pid} del formato {old.get('label')} scartate: le offerte {new_label} "
                    "verranno generate dal prossimo run.",
        })
    recompute(data, config=config, registry=read_json(PATHS["pharmacies"], default=None),
              shipping_cfg=read_json(PATHS["shipping"], default={}), physical=load_physical(config),
              now=now_utc_iso(), reason=f"cambio formato {pid}: {old.get('label')} -> {new_label}")
    return {"removed_offers": len(removed)}


def reset_url_cache(pid: str) -> int:
    cache = read_json(PATHS["urls"], default=None)
    if not cache:
        return 0
    n = 0
    for ph in (cache.get("pharmacies") or {}).values():
        if (ph.get("products") or {}).pop(pid, None) is not None:
            n += 1
    write_json_atomic(PATHS["urls"], cache)
    return n


def format_change(pid: str) -> dict:
    config = read_json(PATHS["config"], required=True)
    product = next((p for p in config["products"] if p["id"] == pid), None)
    if product is None:
        raise SystemExit(f"Prodotto {pid} non trovato in config.json")
    if not product.get("format_history"):
        raise SystemExit(f"{pid}: manca format_history in config.json (formato precedente e data 'until')")
    report = {"product_id": pid, "new_format": product["format"]["label"]}
    report.update(do_migrate_history(config["products"]))
    data = read_json(PATHS["data"], default=None)
    if data and data.get("offers") is not None:
        report.update(purge_offers(data, product, config))
        write_json_atomic(PATHS["data"], data)
    report["url_cache_reset_pharmacies"] = reset_url_cache(pid)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Manutenzione dei dati")
    sub = ap.add_subparsers(dest="cmd", required=True)
    fc = sub.add_parser("format-change", help="applica un cambio di formato già scritto in config.json")
    fc.add_argument("product_id")
    sub.add_parser("migrate-history", help="aggiunge `format` alle voci di storico che non lo hanno")
    args = ap.parse_args(argv)
    if args.cmd == "format-change":
        print(format_change(args.product_id))
    else:
        print(do_migrate_history(read_json(PATHS["config"], required=True)["products"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
