"""Operazioni di manutenzione dei dati.

  python maintenance.py format-change P003     # dopo aver cambiato il formato in config.json
  python maintenance.py migrate-history        # aggiunge `format` alle voci di storico che non lo hanno

`format-change` presuppone che config.json contenga già il NUOVO formato e, in `format_history`,
il formato precedente con la data `until`. Esegue, in modo idempotente e con scritture atomiche:
  1. migrazione dello storico: le voci senza formato ricevono quello valido nella loro data
     (nessuna voce viene cancellata o modificata nel prezzo);
  2. data.json: scarta le offerte del prodotto che non sono del formato corrente, azzera il suo
     miglior prezzo e ricalcola i carrelli (le offerte nuove arriveranno al prossimo run);
  3. product_urls.json: azzera candidati e URL confermati del prodotto, così il prossimo run
     li ricerca da capo per il nuovo formato.
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
    """Scarta da data.json le offerte del prodotto non del formato corrente e ricalcola i carrelli."""
    from optimizer import offer_matches_current_format, optimize

    pid = product["id"]
    products = config["products"]
    offers = data.get("offers") or []
    kept, removed = [], []
    for o in offers:
        (removed if o.get("product_id") == pid and not offer_matches_current_format(o, products) else kept).append(o)
    if not removed and data.get("format_changes"):
        return {"removed_offers": 0}
    data["offers"] = kept
    data["products"] = [{k: p.get(k) for k in ("id", "brand", "name", "format", "ean", "minsan", "format_history")}
                        for p in products]
    if (data.get("best_prices") or {}).get(pid) is not None and removed:
        data["best_prices"][pid] = None
    data["errors"] = [e for e in data.get("errors") or [] if e.get("product_id") != pid]
    old = (product.get("format_history") or [{}])[-1]
    data.setdefault("format_changes", []).append({
        "product_id": pid, "from": old.get("label"), "to": product["format"]["label"], "at": now_utc_iso(),
        "removed_offers": len(removed),
        "note": f"Offerte {pid} del formato {old.get('label')} scartate: le offerte {product['format']['label']} "
                "verranno generate dal prossimo run.",
    })
    stats = data.setdefault("stats", {})
    stats["offers_total"] = len(kept)
    stats["offers_valid"] = sum(1 for o in kept if o.get("usable"))
    stats["ok"] = sum(1 for o in kept if o.get("status") == "OK")
    if data.get("offers"):
        from physical_pharmacies import load_physical
        names = {k: v.get("name", k) for k, v in (data.get("pharmacies") or {}).items()}
        shipping_cfg = read_json(PATHS["shipping"], default={})
        physical = load_physical(config)
        data["carts"] = optimize(products, kept, shipping_cfg, physical, names)
        data["physical"] = physical
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
