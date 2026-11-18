"""Controllo manuale ripetibile. Avviare prima python -m http.server 8126."""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
CASES = ["reale", *(p.name for p in sorted((ROOT / "tests/fixtures/ui").iterdir()) if p.is_dir())]
VIEWPORTS = [(375, 812), (768, 1024), (1280, 900)]

# Restituisce le coppie di colori dei testi effettivamente disposti dal browser,
# includendo i testi nei dettagli dopo l'apertura e gli sfondi semitrasparenti.
TEXT_COLORS = """() => {
  const sample = new Map();
  const css = element => getComputedStyle(element);
  function rgb(color) { return color.match(/[.\\d]+/g).map(Number); }
  function over(color, base) {
    const alpha = color[3] ?? 1;
    return color.slice(0, 3).map((v, i) => Math.round(v * alpha + base[i] * (1 - alpha)));
  }
  const tree = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (tree.nextNode()) {
    const node = tree.currentNode, el = node.parentElement, text = node.textContent.trim();
    if (!text || !el || el.closest('script,style,svg,.sr-only')) continue;
    const range = document.createRange();
    range.selectNodeContents(node);
    const rect = range.getBoundingClientRect();
    if (rect.width < 1 || rect.height < 1 || css(el).visibility !== 'visible') continue;
    const backgrounds = [];
    for (let p = el; p; p = p.parentElement) backgrounds.push(rgb(css(p).backgroundColor));
    const bg = backgrounds.reverse().reduce((base, c) => over(c, base), [255,255,255]);
    const fg = over(rgb(css(el).color), bg);
    const large = parseFloat(css(el).fontSize) >= (Number(css(el).fontWeight) >= 700 ? 18.667 : 24);
    const key = [fg.join(','), bg.join(','), large].join('|');
    if (!sample.has(key)) sample.set(key, [fg, bg, large, text.slice(0, 65)]);
  }
  return [...sample.values()];
}"""


def luminance(rgb):
    channels = [v / 255 for v in rgb]
    linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
    return sum(v * w for v, w in zip(linear, (0.2126, 0.7152, 0.0722)))


def collect_contrast(page, colors):
    for fg, bg, large, example in page.evaluate(TEXT_COLORS):
        low, high = sorted([luminance(fg), luminance(bg)])
        colors[tuple(fg), tuple(bg), large] = ((high + 0.05) / (low + 0.05), example)


def control_contrast(page, colors):
    # Alcuni stati non coesistono nella stessa vista, ad esempio un bottone
    # primario disabilitato e un collegamento con il puntatore sopra.
    samples = page.evaluate("""() => {
      const rgb = color => color.match(/[.\\d]+/g).map(Number).slice(0, 3);
      const probe = document.createElement('span');
      document.body.append(probe);
      const value = token => {
        probe.style.color = 'var(--' + token + ')';
        return rgb(getComputedStyle(probe).color);
      };
      const pairs = [
        ['blue-700','white'], ['blue-700','neutral-50'], ['blue-900','blue-50'],
        ['neutral-800','neutral-50'], ['white','blue-900'], ['neutral-500','neutral-100'],
        ['neutral-500','white'], ['neutral-600','blue-50'], ['blue-800','blue-50'],
        ['neutral-600','white'], ['success-text','success-bg'], ['warning-text','warning-bg'],
        ['error-text','error-bg'], ['info-text','info-bg'], ['hero-ink','blue-900']
      ].map(([fg,bg]) => [value(fg), value(bg), fg + ' / ' + bg]);
      probe.remove();
      return pairs;
    }""")
    for fg, bg, example in samples:
        low, high = sorted([luminance(fg), luminance(bg)])
        colors[tuple(fg), tuple(bg), False] = ((high + 0.05) / (low + 0.05), example)


def no_overflow(page):
    dimensions = page.evaluate("() => [document.documentElement.scrollWidth, innerWidth]")
    assert dimensions[0] <= dimensions[1], f"Overflow orizzontale: {dimensions}"


def ready(page):
    page.wait_for_function("""() => document.querySelector('#hero').innerText.trim()
      && !document.querySelector('main [aria-busy="true"], main .loading')""")
    page.evaluate("document.fonts.ready")


def region_shot(page, anchor, path):
    page.locator(anchor).evaluate("el => el.scrollIntoView({block:'start',behavior:'instant'})")
    page.screenshot(path=path, animations="disabled")


def check_loading(page, origin, out, width):
    pending = []
    page.add_init_script("""
      window.layoutSamples = [];
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) {
          if (!entry.hadRecentInput) window.layoutSamples.push([entry.startTime, entry.value]);
        }
      }).observe({type: 'layout-shift', buffered: true});
    """)
    page.route("**/tests/fixtures/ui/completo/*.json*", lambda route: pending.append(route))
    page.goto(origin + "?data=tests/fixtures/ui/completo/")
    expect(page.locator("#hero")).to_have_attribute("aria-busy", "true")
    assert page.locator("#hero").bounding_box()["height"] >= 100
    expect(page.locator("#fixture-banner")).to_be_visible()
    page.screenshot(path=out / f"caricamento-{width}.png", animations="disabled")
    start = page.evaluate("performance.now()")
    before = page.locator("#hero").bounding_box()["height"]
    page.wait_for_timeout(1000)
    for route in pending:
        route.continue_()
    page.unroute("**/tests/fixtures/ui/completo/*.json*")
    ready(page)
    page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
    shifts = page.evaluate("window.layoutSamples")
    after = page.locator("#hero").bounding_box()["height"]
    delta = sum(weight for time, weight in shifts if time >= start)
    print(f"JSON ritardati, {width} px: CLS durante la sostituzione {delta:.4f}; altezza carrello {before:.0f} → {after:.0f} px", flush=True)


def check_keyboard(page):
    page.keyboard.press("Tab")
    expect(page.locator("a.skip")).to_be_focused()
    assert page.locator("a.skip").evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
    page.keyboard.press("Enter")
    assert page.url.endswith("#carrello")
    summary = page.locator("details > summary:visible").first
    summary.focus()
    page.keyboard.press("Space")
    assert summary.evaluate("el => el.parentElement.open")
    page.keyboard.press("Enter")
    assert not summary.evaluate("el => el.parentElement.open")
    tabs = page.locator("#history [role=tab]")
    tabs.first.focus()
    page.keyboard.press("ArrowRight")
    expect(tabs.nth(1)).to_be_focused()
    expect(tabs.nth(1)).to_have_attribute("aria-selected", "true")
    page.keyboard.press("Home")
    expect(tabs.first).to_be_focused()
    expect(page.locator("#hist-chart")).to_be_visible()
    page.keyboard.press("Tab")
    page.keyboard.press("Tab")
    expect(page.locator("#hist-toggle")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#hist-table")).to_be_visible()
    assert page.locator("#hist-table tbody tr").count() >= 3


def check_preferences(page):
    form = page.locator("#loc-form")
    original_city = form.locator("[name=city]").input_value()
    form.locator("[name=city]").fill("Genova")
    form.locator("[name=postcode]").fill("16121")
    form.locator("[name=max_km]").fill("4")
    transport = form.locator('input[type="radio"][value="bici"]')
    transport.focus()
    page.keyboard.press("Space")
    form.locator("[type=submit]").focus()
    page.keyboard.press("Enter")
    expect(page.locator("#saved")).not_to_be_empty()
    page.reload()
    ready(page)
    expect(form.locator("[name=city]")).to_have_value("Genova")
    expect(form.locator("[name=postcode]")).to_have_value("16121")
    expect(form.locator("[name=max_km]")).to_have_value("4")
    expect(transport).to_be_checked()
    page.locator("#reset").click()
    expect(form.locator("[name=city]")).to_have_value(original_city)


def check_independent_sources(page, origin):
    for file, failed, intact in [
        ("pharmacies.json", "#market", "#hist-chart"),
        ("data.json", "#hero", "#hist-chart"),
        ("price_history.json", "#history", "#market .stats"),
    ]:
        # HTTP 200 con JSON malformato riproduce un aggiornamento illeggibile;
        # l'errore atteso riguarda solo le sezioni dipendenti dal file.
        pattern = f"**/tests/fixtures/ui/completo/{file}*"
        page.route(pattern, lambda request: request.fulfill(status=200, content_type="application/json", body="{"))
        page.goto(origin + "?data=tests/fixtures/ui/completo/")
        ready(page)
        expect(page.locator(failed)).to_contain_text(file)
        expect(page.locator(intact).first).to_be_visible()
        if file == "data.json":
            expect(page.locator("#market .stats").first).to_be_visible()
        else:
            assert "76,40" in page.locator("#hero").inner_text()
        page.unroute(pattern)


def check_case(page, origin, case, width, out, colors):
    prefix = "" if case == "reale" else f"tests/fixtures/ui/{case}/"
    page.goto(origin + (f"?data={prefix}" if prefix else ""))
    ready(page)
    banner = page.get_by_text("DATI DI TEST, NON REALI", exact=True)
    assert banner.is_visible() == bool(prefix)
    no_overflow(page)
    expect(page.get_by_text("Miglior prezzo trovato tra le farmacie monitorate", exact=True).first).to_be_visible()
    assert page.locator("h1").count() == 1
    assert page.locator("html").get_attribute("lang") == "it"
    assert not page.locator('a[target="_blank"]:not([rel~="noopener"][rel~="noreferrer"])').count()
    assert not page.locator("table:not(:has(caption)), table th:not([scope])").count()
    page.screenshot(path=out / f"{case}-{width}.png", full_page=True, animations="disabled")
    if case == "reale":
        page.screenshot(path=out / f"apertura-{width}.png", animations="disabled")
    if case != "reale" and width == 375:
        region_shot(page, "#storico", out / f"grafico-{case}-375.png")
        expect(page.locator('.nav-strip [href="#storico"]')).to_have_attribute("aria-current", "location")
        if case == "storico-due":
            expect(page.locator("#hist-chart")).to_be_visible()
            expect(page.locator("#hist-panel")).to_contain_text("Due giorni")
        elif case == "mancanti":
            expect(page.locator("#hist-panel")).to_contain_text("un solo giorno")
        elif case == "nessuna-offerta":
            expect(page.locator("#hist-panel")).to_contain_text("Nessuna rilevazione")
    if case == "errore":
        expect(page.locator("#history")).to_contain_text("price_history.json")
        data = json.loads((ROOT / prefix / "data.json").read_text(encoding="utf-8"))
        total = data["carts"]["best_overall"]["total"]
        assert f"{total:.2f}".replace(".", ",") in page.locator("#hero").inner_text()
    if case == "completo" and width == 1280:
        check_keyboard(page)
        check_preferences(page)
    # Lo scorrimento e i dettagli aperti possono rivelare overflow nascosti nel primo schermo.
    page.locator("details").evaluate_all("els => els.forEach(el => el.open = true)")
    no_overflow(page)
    if width == 375:
        collect_contrast(page, colors)
    if case == "completo" and width == 768:
        search = page.locator('#market input[type="search"]')
        expect(search).to_be_visible()
        search.fill("inesistente-xy")
        expect(page.locator("#reg-results")).to_contain_text("Nessuna farmacia corrispondente")
        search.fill("")
        assert page.locator('#reg-results tbody tr').count() > 0
        region_shot(page, '#market input[type="search"]', out / "registro-768.png")
    if case == "completo" and width == 1280:
        candidate = "Farmacia della Fonte"
        expect(page.locator("#market .candidate-area")).to_contain_text(candidate)
        for section in ["#hero", "#physical", "#products", "#history"]:
            expect(page.locator(section)).not_to_contain_text(candidate)
        check_independent_sources(page, origin)
    if case == "completo":
        control_contrast(page, colors)
        check_loading(page, origin, out, width)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8126/")
    parser.add_argument("--out", type=Path, default=ROOT / "docs/screenshots/dopo")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    origin = args.url.rstrip("/") + "/"
    colors, issues = {}, []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for width, height in VIEWPORTS:
            for case in CASES:
                context = browser.new_context(viewport={"width": width, "height": height},
                                              locale="it-IT", timezone_id="Europe/Rome",
                                              reduced_motion="reduce", device_scale_factor=1)
                page = context.new_page()
                prefix = f"{case}/{width}"
                page.on("pageerror", lambda error: issues.append((prefix, error.stack or str(error))))
                page.on("console", lambda msg: issues.append((prefix, f"{msg.text} · {msg.location.get('url')}")) if msg.type == "error" else None)
                reads = []
                page.on("request", lambda r: reads.append(urlsplit(r.url).path) if r.resource_type == "fetch" else None)
                try:
                    check_case(page, origin, case, width, args.out, colors)
                    for path in reads:
                        assert ("/tests/fixtures/ui/" in path) == (case != "reale"), path
                    print(prefix, "OK", flush=True)
                except (AssertionError, PlaywrightError) as error:
                    issues.append((prefix, str(error)))
                    print(prefix, str(error), flush=True)
                finally:
                    context.close()
        browser.close()
    for (fg, bg, large), (ratio, label) in colors.items():
        if ratio < (3 if large else 4.5):
            issues.append(("contrasto", f"{ratio:.2f}:1 rgb{fg} su rgb{bg} · {label}"))
    if colors:
        print(f"Coppie testo/sfondo e classe dimensionale: {len(colors)}; minimo {min(r[0] for r in colors.values()):.2f}:1")
    for case, error in issues:
        print(f"{case}: {error}")
    raise SystemExit(bool(issues))


if __name__ == "__main__":
    main()
