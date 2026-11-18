"""Test di scrittura JSON atomica, conservazione dello storico e coerenza dei file dati."""
import json
import math
import re

import pytest

import storage
from storage import StorageError, history_entry, merge_history, read_json, update_history_file, write_json_atomic

ROOT = storage.ROOT


# --------------------------------------------------------------------------- scrittura atomica
def test_write_json_atomic_roundtrip(tmp_path):
    p = tmp_path / "x.json"
    write_json_atomic(p, {"a": 1, "è": "ü"})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1, "è": "ü"}
    assert [f.name for f in tmp_path.iterdir()] == ["x.json"]  # nessun file temporaneo rimasto


def test_write_json_atomic_failure_keeps_original(tmp_path):
    p = tmp_path / "data.json"
    write_json_atomic(p, {"ok": True})
    with pytest.raises(ValueError):
        write_json_atomic(p, {"bad": math.nan})  # NaN non è JSON valido
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}
    assert [f.name for f in tmp_path.iterdir()] == ["data.json"]


def test_read_json_corrupt_raises(tmp_path):
    p = tmp_path / "c.json"
    p.write_text("{rotto", encoding="utf-8")
    with pytest.raises(StorageError):
        read_json(p, default={})
    assert read_json(tmp_path / "manca.json", default={"d": 1}) == {"d": 1}


# --------------------------------------------------------------------------- storico
def e(date, pid="P001", ph="A", price=10.0):
    return history_entry(date, pid, ph, price, 3.0, None if price is None else price + 3.0, "DISPONIBILE", "ID")


def test_merge_history_same_day_replaces_past_untouched():
    hist = {"schema_version": 1, "entries": [e("2026-11-16", price=11.0), e("2026-11-17", price=10.5)]}
    out = merge_history(hist, [e("2026-11-18", price=10.0), e("2026-11-18", price=9.9)], today="2026-11-18")
    days = [(x["date"], x["price"]) for x in out["entries"]]
    assert days == [("2026-11-16", 11.0), ("2026-11-17", 10.5), ("2026-11-18", 9.9)]


def test_merge_history_skips_missing_prices_and_rejects_past_dates():
    out = merge_history({"entries": []}, [e("2026-11-18", price=None)], today="2026-11-18")
    assert out["entries"] == []
    with pytest.raises(StorageError):
        merge_history({"entries": []}, [e("2026-11-01")], today="2026-11-18")


def test_update_history_file_never_overwrites_with_empty(tmp_path):
    p = tmp_path / "price_history.json"
    write_json_atomic(p, {"schema_version": 1, "entries": [e("2026-11-17")]})
    before = p.read_text(encoding="utf-8")
    assert update_history_file(p, [], today="2026-11-18") == 1
    assert update_history_file(p, [e("2026-11-18", price=None)], today="2026-11-18") == 1
    assert p.read_text(encoding="utf-8") == before
    assert update_history_file(p, [e("2026-11-18")], today="2026-11-18") == 2


def test_update_history_file_corrupt_is_not_overwritten(tmp_path):
    p = tmp_path / "price_history.json"
    p.write_text("{corrotto", encoding="utf-8")
    with pytest.raises(StorageError):
        update_history_file(p, [e("2026-11-18")], today="2026-11-18")
    assert p.read_text(encoding="utf-8") == "{corrotto"


# --------------------------------------------------------------------------- formato nello storico (P003 50 -> 100 ml)
PRODS = [
    {"id": "P001", "format": {"label": "1000 ml"}},
    {"id": "P003", "format": {"label": "100 ml"}, "format_history": [{"label": "50 ml", "until": "2026-11-18"}]},
]


def legacy(date, pid, ph="A", price=10.0):
    x = e(date, pid, ph, price)
    x.pop("format")
    return x


def test_migrate_history_adds_format_without_losing_data():
    from storage import migrate_history
    old = {"schema_version": 1, "entries": [legacy("2026-11-17", "P003"), legacy("2026-11-18", "P003", "B", 11.28),
                                             legacy("2026-11-18", "P001", "A", 16.38)]}
    out, n = migrate_history(json.loads(json.dumps(old)), PRODS)
    assert n == 3 and len(out["entries"]) == 3 and out["schema_version"] == 2
    assert [x["format"] for x in out["entries"]] == ["50 ml", "50 ml", "1000 ml"]
    for before, after in zip(old["entries"], out["entries"]):
        assert {k: v for k, v in after.items() if k != "format"} == before  # nient'altro cambia
    again, n2 = migrate_history(out, PRODS)
    assert n2 == 0 and again["entries"] == out["entries"]  # idempotente


def test_new_format_entry_does_not_overwrite_old_format_same_day():
    from storage import migrate_history
    hist, _ = migrate_history({"entries": [legacy("2026-11-18", "P003", "A", 11.28)]}, PRODS)
    new = history_entry("2026-11-18", "P003", "A", 18.73, None, None, "DISPONIBILE", "ID", "100 ml")
    out = merge_history(hist, [new], today="2026-11-18")
    assert sorted((x["format"], x["price"]) for x in out["entries"]) == [("100 ml", 18.73), ("50 ml", 11.28)]


def test_update_history_file_migrates_then_appends(tmp_path):
    p = tmp_path / "price_history.json"
    write_json_atomic(p, {"schema_version": 1, "entries": [legacy("2026-11-18", "P003")]})
    n = update_history_file(p, [history_entry("2026-11-19", "P003", "A", 18.0, None, None, "DISPONIBILE", "ID", "100 ml")],
                            today="2026-11-19", products=PRODS)
    entries = json.loads(p.read_text(encoding="utf-8"))["entries"]
    assert n == 2 and [x["format"] for x in entries] == ["50 ml", "100 ml"]


def test_maintenance_format_change(tmp_path, monkeypatch):
    import maintenance
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    paths = {k: tmp_path / v.name for k, v in maintenance.PATHS.items()}
    write_json_atomic(paths["config"], cfg)
    write_json_atomic(paths["shipping"], {"pharmacies": {"A": {"standard_cost_eur": 5.0, "reliability": "alta"}}})
    write_json_atomic(paths["history"], {"schema_version": 1, "entries": [legacy("2026-11-18", "P003"), legacy("2026-11-18", "P004")]})
    off = lambda pid, price: {"product_id": pid, "pharmacy_id": "A", "price": price, "usable": True, "status": "OK",  # noqa: E731
                              "verified": True, "currency": "EUR",
                              "match_level": "ID", "availability": "DISPONIBILE", "url": "https://a.it/" + pid}
    write_json_atomic(paths["data"], {"offers": [off("P003", 11.28), off("P004", 11.79)], "pharmacies": {},
                                      "best_prices": {"P003": {"price": 11.28}}, "errors": [{"product_id": "P003"}]})
    write_json_atomic(paths["urls"], {"pharmacies": {"A": {"products": {"P003": {"matched_url": "x"}, "P004": {}}}}})
    monkeypatch.setattr(maintenance, "PATHS", paths)
    monkeypatch.setattr("physical_pharmacies.load_physical", lambda cfg, cache=None: None)
    rep = maintenance.format_change("P003")
    assert rep["removed_offers"] == 1 and rep["migrated"] == 2 and rep["history_entries"] == 2
    data = json.loads(paths["data"].read_text(encoding="utf-8"))
    assert [o["product_id"] for o in data["offers"]] == ["P004"]
    assert data["best_prices"]["P003"] is None and data["errors"] == []
    assert data["carts"]["products_without_offers"] == ["P001", "P002", "P003", "P005"]
    assert data["format_changes"][0]["from"] == "50 ml" and data["format_changes"][0]["to"] == "100 ml"
    urls = json.loads(paths["urls"].read_text(encoding="utf-8"))
    assert "P003" not in urls["pharmacies"]["A"]["products"] and "P004" in urls["pharmacies"]["A"]["products"]
    hist = json.loads(paths["history"].read_text(encoding="utf-8"))["entries"]
    assert [(x["product_id"], x["format"]) for x in hist] == [("P003", "50 ml"), ("P004", "100 ml")]
    assert maintenance.format_change("P003")["removed_offers"] == 0  # idempotente


def test_real_history_keeps_old_p003_entries_labelled():
    hist = load("price_history.json")
    p3 = [x for x in hist.get("entries", []) if x["product_id"] == "P003"]
    assert all(x.get("format") in ("50 ml", "100 ml") for x in p3)
    assert all(x.get("format") for x in hist.get("entries", []))  # nessuna voce senza formato


def test_dashboard_history_ignores_previous_format(tmp_path):
    """Rendering reale nel browser (saltato se Playwright/Chromium non sono installati, es. su Actions)."""
    sync_api = pytest.importorskip("playwright.sync_api")
    import shutil
    import socket
    import subprocess
    import sys
    import time
    shutil.copy(ROOT / "index.html", tmp_path / "index.html")
    shutil.copytree(ROOT / "assets", tmp_path / "assets")
    p3 = {"id": "P003", "name": "Ceramol 311 Lipocrema", "format": {"label": "100 ml"}, "ean": None, "minsan": None,
          "format_history": [{"label": "50 ml", "until": "2026-11-18"}]}
    write_json_atomic(tmp_path / "data.json", {"generated_at": "2026-11-20T07:00:00Z", "status": "OK", "products": [p3],
                                                "offers": [], "pharmacies": {}})
    entries = [dict(e(d, "P003", "A", 11.0), format="50 ml") for d in ("2026-11-16", "2026-11-17", "2026-11-18")]
    entries += [dict(e(d, "P003", "A", 18.5 + i), format="100 ml") for i, d in enumerate(("2026-11-19", "2026-11-20"))]
    write_json_atomic(tmp_path / "price_history.json", {"schema_version": 2, "entries": entries})
    write_json_atomic(tmp_path / "pharmacies.json", {"pharmacies": []})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=tmp_path,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.0)
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Chromium non disponibile: {exc}")
            page = browser.new_page()
            errors = []
            page.on("pageerror", lambda err: errors.append(str(err)))
            page.goto(f"http://127.0.0.1:{port}/index.html", wait_until="networkidle")
            rows = page.locator("#hist-table tbody tr").all_inner_texts()
            stats = page.locator("#hist-stats").inner_text()
            browser.close()
    finally:
        srv.terminate()
    assert not errors
    assert len(rows) == 2 and all("11,00" not in r for r in rows)  # solo le 2 voci 100 ml
    assert "18,50" in stats and "11,00" not in stats                # minimo storico calcolato solo sul 100 ml
    assert "Formato cambiato da 50 ml a 100 ml" in stats and "3 rilevazioni del formato 50 ml" in stats


# --------------------------------------------------------------------------- orchestratore: fallimento totale
def test_total_failure_keeps_previous_data(tmp_path, monkeypatch):
    import scraper as orch
    from scrapers.base import Offer

    paths = {k: tmp_path / v.name for k, v in orch.PATHS.items()}
    for k in ("config", "pharmacies", "shipping"):
        paths[k].write_text(orch.PATHS[k].read_text(encoding="utf-8"), encoding="utf-8")
    registry = json.loads(paths["pharmacies"].read_text(encoding="utf-8"))
    registry["pharmacies"][0]["status"] = "ACTIVE"
    paths["pharmacies"].write_text(json.dumps(registry), encoding="utf-8")
    previous = {"schema_version": 1, "generated_at": "2026-11-17T07:00:00Z", "offers": [{"product_id": "P001", "price": 9.99}]}
    write_json_atomic(paths["data"], previous)
    write_json_atomic(paths["history"], {"schema_version": 1, "entries": [e("2026-11-17")]})
    hist_before = paths["history"].read_text(encoding="utf-8")
    monkeypatch.setattr(orch, "PATHS", paths)
    monkeypatch.setattr(orch, "load_physical", lambda cfg: None)

    def failing(entry, session, config, cache, now, products, refresh):
        return entry["id"], [Offer(p["id"], entry["id"], "ERRORE", scraped_at=now, notes=["rete giù"]) for p in products], {}
    monkeypatch.setattr(orch, "run_pharmacy", failing)
    code = orch.main([])
    assert code == 2
    data = json.loads(paths["data"].read_text(encoding="utf-8"))
    assert data["offers"] == previous["offers"]          # dati precedenti conservati
    assert data["last_attempt"]["status"] == "ERRORE"     # ma il fallimento è registrato
    assert paths["history"].read_text(encoding="utf-8") == hist_before


# --------------------------------------------------------------------------- coerenza dei file reali
def load(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def _price_keys(obj, path=""):
    """Chiavi che conterrebbero un prezzo (price, prezzo, costo...) a qualsiasi profondità."""
    found = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if re.search(r"price|prezz|cost|eur$", k, re.I):
                found.append(path + k)
            found += _price_keys(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found += _price_keys(v, f"{path}{i}.")
    return found


def test_config_products_catalog(config):
    from scrapers.extract import gtin_valid
    ids = [p["id"] for p in config["products"]]
    assert ids == ["P001", "P002", "P003", "P004", "P005"]
    for p in config["products"]:
        assert p["format"]["quantity"] > 0 and p["format"]["unit"] == "ml"
        if p.get("ean"):
            assert gtin_valid(p["ean"]), p["id"]
            assert p["identifiers_verification"]["verified"] and len(p["identifiers_verification"]["sources"]) >= 2
        assert not _price_keys(p), f"{p['id']}: il catalogo non deve contenere prezzi {_price_keys(p)}"
    assert config["app"]["disclaimer"] == "Miglior prezzo trovato tra le farmacie monitorate"


def test_pharmacies_registry_schema():
    reg = load("pharmacies.json")
    allowed = set(reg["statuses"])
    seen = set()
    for p in reg["pharmacies"]:
        assert p["status"] in allowed, p["id"]
        assert p["id"] not in seen
        seen.add(p["id"])
        for k in ("name", "domain", "base_url", "country", "status", "source", "scraper_status", "errors"):
            assert k in p, (p["id"], k)
        if p["source"] != "seed_list_utente":
            # le farmacie scoperte non possono essere attive senza attivazione manuale documentata
            assert p["status"] != "ACTIVE" or p.get("activation_evidence"), p["id"]
    assert "excluded_domains" in reg


def test_active_pharmacies_have_scraper_and_evidence():
    from scrapers import REGISTRY
    for p in load("pharmacies.json")["pharmacies"]:
        if p["status"] != "ACTIVE":
            continue
        if p["id"] in REGISTRY:
            assert p.get("activation_evidence", {}).get("price"), f"{p['id']} attiva senza offerta autentica"
        else:
            # sito generico: solo dopo promozione manuale documentata
            assert p.get("scraper") == "generic_jsonld" and p.get("promoted_at") and p.get("probe"), p["id"]


EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}")
# chiavi JSON che indicherebbero una credenziale (match esatto sul nome: "robots_token" è legittima)
SECRET = re.compile(r"\"(api[_-]?key|access[_-]?token|auth[_-]?token|token|password|passwd|secret|client[_-]?secret)\"\s*:", re.I)


@pytest.mark.parametrize("name", ["data.json", "price_history.json", "pharmacies.json", "shipping_config.json",
                                  "config.json", "product_urls.json", "store_prices.json", "physical_pharmacies.json"])
def test_public_json_has_no_personal_data_or_secrets(name):
    path = ROOT / name
    if not path.exists():
        pytest.skip(f"{name} non ancora generato")
    text = path.read_text(encoding="utf-8")
    json.loads(text)
    assert not EMAIL.search(text), f"indirizzo email in {name}"
    assert not SECRET.search(text), f"possibile credenziale in {name}"


def test_location_is_city_or_postcode_only(config):
    loc = config["location"]
    assert not re.search(r"\b(via|viale|piazza|corso)\b", json.dumps(loc), re.I)


def test_data_json_never_has_zero_prices():
    data = load("data.json")
    for o in data.get("offers", []):
        assert o.get("price") is None or o["price"] > 0
        if o.get("price") is not None:
            assert o["status"] == "OK" and o["verified"], "prezzo esposto senza verifica"
    hist = load("price_history.json")
    assert all(x["price"] is None or x["price"] > 0 for x in hist.get("entries", []))


def test_dashboard_contract():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    assert "Miglior prezzo trovato tra le farmacie monitorate" in html
    assert "migliore del mercato" not in html.lower()
    for f in ("data.json", "price_history.json", "pharmacies.json"):
        assert f in html
    for section in ("panoramica", "carrello", "online-o-farmacia", "confronto", "mercato", "storico", "sistema", "localita"):
        assert f'id="{section}"' in html
    assert any("localStorage" in module.read_text(encoding="utf-8") for module in (ROOT / "assets").glob("*.js"))
    # librerie esterne solo da CDN con versione fissata
    for src in re.findall(r'<script src="([^"]+)"', html):
        assert re.search(r"@\d+\.\d+\.\d+|/\d+\.\d+\.\d+", src), src


def test_fixtures_are_marked_as_simulated():
    for f in (ROOT / "tests" / "fixtures").glob("*.html"):
        assert "SIMULATI" in f.read_text(encoding="utf-8"), f.name
