"""Controlli della dashboard in un browser reale, sui casi di anteprima in tests/fixtures/ui/ (DATI SIMULATI).

Saltati se Playwright/Chromium non sono installati (es. su GitHub Actions). Sempre offline: ogni
richiesta che non va al server locale (CDN di Tailwind, Chart.js, font) viene bloccata, quindi si
verifica anche che lo storico resti consultabile senza Chart.js.
"""
import socket
import subprocess
import sys
import time

import pytest

from conftest import ROOT

CASES = sorted(p.name for p in (ROOT / "tests" / "fixtures" / "ui").iterdir() if p.is_dir())
WIDTHS = (375, 768, 1280)
OVERFLOW = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


@pytest.fixture(scope="module")
def browser_page():
    sync_api = pytest.importorskip("playwright.sync_api")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"], cwd=ROOT,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    try:
        with sync_api.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Chromium non disponibile: {exc}")
            base = f"http://127.0.0.1:{port}"

            def open_page(case: str, width: int, *, corrupt: bool = False):
                page = browser.new_page(viewport={"width": width, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base)
                           else route.abort())
                if corrupt:
                    page.route(f"**/tests/fixtures/ui/{case}/data.json",
                               lambda route: route.fulfill(status=200, body="{rotto", content_type="application/json"))
                page.goto(f"{base}/index.html?data=tests/fixtures/ui/{case}/", wait_until="networkidle")
                page.wait_for_selector("#hero[aria-busy='false']")
                return page, errors

            yield open_page
            browser.close()
    finally:
        srv.terminate()


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("width", WIDTHS)
def test_fixture_renders_without_errors_or_overflow(browser_page, case, width):
    page, errors = browser_page(case, width)
    try:
        assert not errors, errors
        assert page.locator("#fixture-banner").is_visible()  # «DATI DI TEST, NON REALI»
        assert page.evaluate(OVERFLOW) <= 1, f"overflow orizzontale a {width}px"
        assert "migliore del mercato" not in page.content().lower()
    finally:
        page.close()


def test_corrupt_data_shows_section_errors(browser_page):
    page, errors = browser_page("completo", 375, corrupt=True)
    try:
        assert not errors
        assert "Impossibile leggere data.json" in page.locator("#hero").inner_text()
        assert page.evaluate(OVERFLOW) <= 1
    finally:
        page.close()


def test_keyboard_starts_from_skip_link(browser_page):
    page, _ = browser_page("completo", 1280)
    try:
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement.className") == "skip"
    finally:
        page.close()


def test_history_readable_without_chartjs(browser_page):
    page, errors = browser_page("storico-due", 768)
    try:
        assert not errors
        assert page.locator("#hist-table tbody tr").count() > 0
    finally:
        page.close()


def test_uncertain_partial_and_shipping_states_are_labelled(browser_page):
    page, errors = browser_page("incerte", 375)
    try:
        assert not errors
        assert "Scansione parziale" in page.locator("#alerts").inner_text()
        hero = page.locator("#hero").text_content()
        assert "Alternative potenziali" in hero and "Disponibilità da verificare" in hero
        assert "Disponibilità da verificare" in page.locator("#products").text_content()
        assert "Tariffa cambiata" in page.locator("#market").text_content()
        assert "escluse dal carrello verificato" in page.locator("#system").text_content()
        assert page.evaluate(OVERFLOW) <= 1
    finally:
        page.close()
