"""Configurazione comune dei test. Tutti i test sono OFFLINE: nessuna richiesta di rete.

I dati in tests/fixtures/ sono SIMULATI (HTML sintetico modellato sulla struttura reale
delle piattaforme): non sono prezzi reali e non vengono mai usati fuori dai test.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def config():
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def products(config):
    return {p["id"]: p for p in config["products"]}


@pytest.fixture(scope="session")
def bundle_terms(config):
    return config["global_exclusions"]["bundle_terms"]


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fx():
    return fixture_text
