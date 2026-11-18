"""Test di spedizioni e soglie. Configurazioni SIMULATE (non i valori reali delle farmacie)."""
import json

import pytest

from shipping import extract_from_text, pickup_info, quote

CFG = {"pharmacies": {
    "incl": {"standard_cost_eur": 3.99, "free_threshold_eur": 18.99, "free_threshold_inclusive": True,
             "free_threshold_verified": True, "cod_cost_eur": 3.95, "reliability": "alta"},
    "excl": {"standard_cost_eur": 5.0, "free_threshold_eur": 69.0, "free_threshold_inclusive": False,
             "free_threshold_verified": True, "reliability": "alta"},
    "unverified_thr": {"standard_cost_eur": 5.9, "free_threshold_eur": 29.9, "free_threshold_verified": False},
    "unknown": {"standard_cost_eur": None, "free_threshold_eur": None, "reliability": "sconosciuta"},
    "unknown_thr_ok": {"standard_cost_eur": None, "free_threshold_eur": 50.0, "free_threshold_inclusive": True,
                       "free_threshold_verified": True},
    "extras": {"standard_cost_eur": 4.0, "extra_costs": [{"label": "imballo", "amount_eur": 1.5}],
               "free_threshold_eur": None},
    "extras_unknown": {"standard_cost_eur": 4.0, "extra_costs": [{"label": "supplemento", "amount_eur": None}]},
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
                                "free_threshold_verified": True}}}
    assert quote(cfg, "v", subtotal).cost == expected
