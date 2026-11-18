"""Test di spedizioni e soglie. Configurazioni SIMULATE (non i valori reali delle farmacie)."""
import json
from datetime import date, timedelta

import pytest

from shipping import (STATUS_DISCORDANT, STATUS_EXPIRED, STATUS_UNKNOWN, STATUS_VERIFIED, check,
                      extract_from_text, pickup_info, quote, shipping_status)

TODAY = date.today().isoformat()
CFG = {"pharmacies": {
    "incl": {"standard_cost_eur": 3.99, "free_threshold_eur": 18.99, "free_threshold_inclusive": True,
             "free_threshold_verified": True, "cod_cost_eur": 3.95, "reliability": "alta", "verified_at": TODAY},
    "excl": {"standard_cost_eur": 5.0, "free_threshold_eur": 69.0, "free_threshold_inclusive": False,
             "free_threshold_verified": True, "reliability": "alta", "verified_at": TODAY},
    "unverified_thr": {"standard_cost_eur": 5.9, "free_threshold_eur": 29.9, "free_threshold_verified": False},
    "unknown": {"standard_cost_eur": None, "free_threshold_eur": None, "reliability": "sconosciuta"},
    "unknown_thr_ok": {"standard_cost_eur": None, "free_threshold_eur": 50.0, "free_threshold_inclusive": True,
                       "free_threshold_verified": True, "verified_at": TODAY},
    "extras": {"standard_cost_eur": 4.0, "extra_costs": [{"label": "imballo", "amount_eur": 1.5}],
               "free_threshold_eur": None, "verified_at": TODAY},
    "extras_unknown": {"standard_cost_eur": 4.0, "extra_costs": [{"label": "supplemento", "amount_eur": None}],
                       "verified_at": TODAY},
    "pickup": {"standard_cost_eur": 4.99, "pickup_available": True, "pickup_cost_eur": 0.0,
               "pickup_locations": [{"city": "Alagna Valsesia"}]},
}}


def test_below_threshold_pays_standard():
    q = quote(CFG, "incl", 18.98)
    assert q.cost == 3.99 and q.complete and not q.free_shipping_applied


def test_inclusive_threshold_at_exact_value():
    q = quote(CFG, "incl", 18.99)
    assert q.cost == 0.0 and q.free_shipping_applied


def test_exclusive_threshold_at_exact_value_still_pays():
    assert quote(CFG, "excl", 69.0).cost == 5.0
    assert quote(CFG, "excl", 69.01).cost == 0.0


def test_unverified_threshold_is_ignored():
    q = quote(CFG, "unverified_thr", 100)
    assert q.cost == 5.9 and not q.free_shipping_applied
    assert any("NON verificata" in n for n in q.notes)


def test_unknown_cost_is_never_free():
    q = quote(CFG, "unknown", 10)
    assert q.cost is None and not q.complete
    assert quote(CFG, "missing_pharmacy", 10).cost is None


def test_verified_threshold_applies_even_if_standard_unknown():
    assert quote(CFG, "unknown_thr_ok", 60).cost == 0.0
    assert quote(CFG, "unknown_thr_ok", 40).cost is None


def test_extra_costs_and_cod():
    assert quote(CFG, "extras", 10).cost == 5.5
    q = quote(CFG, "extras_unknown", 10)
    assert not q.complete
    assert quote(CFG, "incl", 10, cod=True).cost == round(3.99 + 3.95, 2)
    q = quote(CFG, "excl", 10, cod=True)  # contrassegno sconosciuto
    assert not q.complete


def test_pickup_info():
    assert pickup_info(CFG, "pickup")["cost"] == 0.0
    assert pickup_info(CFG, "incl") is None


def test_extract_from_text_patterns():
    text = "Le spese di spedizione sono gratuite per ordini di importo uguale o superiore a € 18,99. Altrimenti le spese di spedizioni ammontano a € 3,99."
    found = extract_from_text(text, {"free_threshold_eur": r"uguale o superiore a\s*€\s*(\d+[.,]\d{2})",
                                     "standard_cost_eur": r"spedizioni ammontano a\s*€\s*(\d+[.,]\d{2})"})
    assert found == {"free_threshold_eur": 18.99, "standard_cost_eur": 3.99}


def test_extract_ambiguous_returns_none():
    text = "spedizione a 5,99€ ... spedizione a 7,99€"
    assert extract_from_text(text, {"standard_cost_eur": r"spedizione a\s*(\d+,\d{2})"}) == {"standard_cost_eur": None}


def test_real_shipping_config_is_consistent():
    """Il file reale non deve mai dichiarare gratis una spedizione sconosciuta."""
    from storage import ROOT
    cfg = json.loads((ROOT / "shipping_config.json").read_text(encoding="utf-8"))
    for pid, ph in cfg["pharmacies"].items():
        if ph.get("standard_cost_eur") is None:
            # "NON_VERIFICATA" = creata alla promozione di un sito generico, ancora da compilare
            assert ph.get("reliability") in ("sconosciuta", "NON_VERIFICATA"), pid
        if ph.get("free_threshold_verified"):
            assert ph.get("free_threshold_eur") is not None and ph.get("free_threshold_inclusive") is not None, pid
            assert ph.get("source_url") and ph.get("verified_at"), pid
        for key in ("standard_cost_eur", "free_threshold_eur", "cod_cost_eur"):
            v = ph.get(key)
            assert v is None or v > 0, f"{pid}.{key} non può essere 0: usa null"


@pytest.mark.parametrize("subtotal,expected", [(29.89, 5.0), (29.9, 5.0), (29.91, 0.0)])
def test_threshold_table(subtotal, expected):
    cfg = {"pharmacies": {"v": {"standard_cost_eur": 5.0, "free_threshold_eur": 29.9, "free_threshold_inclusive": False,
                                "free_threshold_verified": True, "verified_at": TODAY}}}
    q = quote(cfg, "v", subtotal)
    assert q.cost == expected and q.complete and q.status == STATUS_VERIFIED


# --------------------------------------------------------------------------- stato della tariffa
def _ph(**kw):
    base = {"standard_cost_eur": 5.0, "free_threshold_eur": 50.0, "free_threshold_inclusive": True,
            "free_threshold_verified": True, "reliability": "alta", "verified_at": TODAY}
    return {**base, **kw}


def test_missing_verification_date_is_unknown_and_not_complete():
    ph = _ph(verified_at=None)
    assert shipping_status(ph)[0] == STATUS_UNKNOWN
    q = quote({"pharmacies": {"x": ph}}, "x", 10)
    assert q.cost == 5.0 and not q.complete  # costo noto ma non attendibile: mai vincitore certo


def test_expired_verification():
    old = (date.today() - timedelta(days=61)).isoformat()
    status, reason = shipping_status(_ph(verified_at=old), max_age=60)
    assert status == STATUS_EXPIRED and old in reason
    q = quote({"max_age_days": 60, "pharmacies": {"x": _ph(verified_at=old)}}, "x", 10)
    assert not q.complete and any("scaduta" in n for n in q.notes)
    assert quote({"max_age_days": 90, "pharmacies": {"x": _ph(verified_at=old)}}, "x", 10).complete


def test_changed_rate_on_official_page_invalidates_previous_value():
    last = {"at": TODAY + "T06:00:00Z", "differences": ["standard_cost_eur: configurato 5.0 / pagina 6.9"]}
    ph = _ph(auto_check={"last_check": last})
    status, reason = shipping_status(ph)
    assert status == STATUS_DISCORDANT and "6.9" in reason
    q = quote({"pharmacies": {"x": ph}}, "x", 10)
    assert q.cost == 5.0 and not q.complete  # la tariffa precedente non resta implicitamente affidabile
    # una verifica manuale successiva al controllo la riconferma
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert shipping_status(_ph(verified_at=tomorrow, auto_check={"last_check": last}))[0] == STATUS_VERIFIED


def test_unknown_extra_cost_keeps_cart_incomplete_even_if_verified():
    q = quote({"pharmacies": {"x": _ph(extra_costs=[{"label": "imballo", "amount_eur": None}])}}, "x", 10)
    assert q.status == STATUS_VERIFIED and not q.complete


class _Page:
    def __init__(self, html):
        self.ok, self.text, self.status = True, html, 200


class _Session:
    def __init__(self, html):
        self.html = html

    def is_allowed(self, url):
        return True

    def get(self, url):
        return _Page(self.html)


def _auto(ph_values):
    patterns = {"standard_cost_eur": r"spedizione\s*€\s*(\d+,\d{2})"}
    return {"pharmacies": {"x": _ph(**ph_values, auto_check={"url": "https://x.it/spedizioni", "patterns": patterns})}}


def test_check_detects_changed_rate_and_marks_discordant():
    cfg = _auto({"verified_at": "2026-01-01"})
    report = check(cfg, _Session("<p>spedizione € 6,90</p>"))
    assert report[0]["differences"] and cfg["pharmacies"]["x"]["standard_cost_eur"] == 5.0
    assert shipping_status(cfg["pharmacies"]["x"])[0] == STATUS_DISCORDANT


def test_check_ambiguous_value_invalidates():
    cfg = _auto({})
    check(cfg, _Session("<p>spedizione € 5,00</p><p>spedizione € 7,90</p>"))
    status, reason = shipping_status(cfg["pharmacies"]["x"])
    assert status == STATUS_DISCORDANT and "ambiguo" in reason


def test_check_apply_updates_value_and_confirms():
    cfg = _auto({})
    report = check(cfg, _Session("<p>spedizione € 6,90</p>"), apply=True)
    ph = cfg["pharmacies"]["x"]
    assert ph["standard_cost_eur"] == 6.9 and report[0]["applied"] and not report[0]["differences"]
    assert shipping_status(ph)[0] == STATUS_VERIFIED


def test_check_matching_page_confirms_rate():
    cfg = _auto({"verified_at": "2026-01-01"})
    check(cfg, _Session("<p>spedizione € 5,00</p>"))
    assert cfg["pharmacies"]["x"]["verified_at"] != "2026-01-01"
    assert shipping_status(cfg["pharmacies"]["x"])[0] == STATUS_VERIFIED
