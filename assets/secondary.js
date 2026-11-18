import {
  $, state, esc, money, num, percent, fmtDay, fmtDate, products, productName, pharmacyName,
  isCurrentFormat, formatChangeNote, pill, note, empty, failure, link, table, tile, details,
} from "./ui.js";

const registryLabels = {
  ACTIVE: ["success", "Attiva"], VERIFIED: ["info", "Verificata"],
  UNDER_REVIEW: ["warning", "In valutazione"], DISCOVERED: ["neutral", "Individuata"],
  BLOCKED: ["error", "Bloccata"], INACTIVE: ["neutral", "Inattiva"],
};
const scraperLabels = {
  OK: ["success", "Operativo"], BLOCKED: ["error", "Bloccato"], RATE_LIMITED: ["warning", "Limitato (429)"],
  ERROR: ["error", "Errore"], NOT_VERIFIED: ["warning", "Non verificato"], NO_SCRAPER: ["neutral", "Assente"],
  PROBE_FULL: ["success", "Verifica completa"], PROBE_PARTIAL: ["warning", "Verifica parziale"],
  PROBE_NONE: ["neutral", "Dati assenti"], PROBE_BLOCKED: ["error", "Verifica bloccata"],
};
const compatibilityLabels = {
  FULL: ["success", "Completa"], PARTIAL: ["warning", "Parziale"], NONE: ["neutral", "Assente"],
  BLOCKED: ["error", "Bloccata"],
};
const labelled = (map, key) => pill(...(map[key] || ["neutral", key || "n.d."]));
const list = (items, render) => `<ul class="row-list">${items.map(item => `<li>${render(item)}</li>`).join("")}</ul>`;
const stats = items => `<div class="stats">${items.join("")}</div>`;
const plain = value => esc(value ?? "n.d.");
const unavailable = () => empty("Dati assenti", "Controlla questa sezione dopo la prossima scansione.");

function coverage(data) {
  if (!Array.isArray(data.offers)) return unavailable();
  const byPharmacy = new Map();
  const catalogue = new Map(products().map(p => [p.id, p]));
  for (const offer of data.offers) {
    if (!isCurrentFormat(offer, catalogue.get(offer.product_id))) continue;
    if (!byPharmacy.has(offer.pharmacy_id)) byPharmacy.set(offer.pharmacy_id, { count: 0, sum: 0 });
    if (offer.usable && Number.isFinite(offer.price)) {
      const row = byPharmacy.get(offer.pharmacy_id);
      row.count++;
      row.sum += offer.price;
    }
  }
  if (!byPharmacy.size) return empty("Nessuna offerta monitorata", "La copertura comparirà dopo una scansione con offerte.");
  const ordered = [...byPharmacy].sort((a, b) => b[1].count - a[1].count || a[1].sum - b[1].sum);
  return table("Copertura delle farmacie monitorate", ["Farmacia", "Prodotti coperti", "Somma prezzi", "Spedizione"], ordered.map(([id, r]) => {
    const sh = data.shipping?.[id];
    let shipping = sh?.standard_cost_eur == null ? pill("warning", "Non verificata") : money(sh.standard_cost_eur);
    if (sh?.free_threshold_verified && sh.free_threshold_eur != null) {
      shipping += `<small>Gratuita ${sh.free_threshold_inclusive ? "da" : "oltre"} ${money(sh.free_threshold_eur)}</small>`;
    }
    if (sh?.status && sh.status !== "verificata" && sh.standard_cost_eur != null) {
      shipping += labelled(shippingLabels, sh.status) + `<small>${esc(sh.status_reason || "")}</small>`;
    }
    return [plain(pharmacyName(id)), `${num(r.count)} / ${num(catalogue.size)}`, money(r.count ? r.sum : null), shipping];
  }));
}

const shippingLabels = {
  verificata: ["success", "Verificata"], scaduta: ["warning", "Verifica scaduta"],
  discordante: ["error", "Tariffa cambiata"], sconosciuta: ["warning", "Da verificare"],
};

function candidateOffers(data) {
  const all = data.candidate_offers;
  const catalogue = new Map(products().map(p => [p.id, p]));
  const candidates = all?.filter(c => isCurrentFormat(c, catalogue.get(c.product_id)));
  const availability = { "DISPONIBILE": "Disponibile", "NON DISPONIBILE": "Esaurito", "SCONOSCIUTA": "Da verificare" };
  let content = unavailable();
  if (candidates) {
    content = candidates.length ? table("Offerte candidate dei siti in valutazione", ["Prodotto", "Farmacia", "Prezzo", "Disponibilità dichiarata", "Corrispondenza"], candidates.map(c => [
      plain(productName(c.product_id)), link(c.url, c.pharmacy_name || c.domain), money(c.price),
      plain(availability[c.availability]), plain(c.match_level === "ID" ? "EAN / Minsan" : c.match_level),
    ])) : empty("Nessuna offerta candidata", "Le prossime verifiche dei siti in valutazione possono aggiungere nuove segnalazioni.");
  }
  return details(`Offerte candidate non verificate · ${num(candidates?.length)}`,
    `<div class="candidate-area">${pill("warning", "Non verificato")}<p>Offerte non verificate, non incluse nel carrello. I prezzi dei siti in valutazione sono esclusi anche dai migliori prezzi e dallo storico.</p>${content}</div>`);
}

function registryTable(pharmacies) {
  if (!pharmacies.length) return empty("Nessuna farmacia corrispondente", "Prova un altro nome, dominio o stato.");
  return table("Registro delle farmacie", ["Farmacia", "Stato", "Scansione", "Dati strutturati", "Fonte", "Prodotti candidati"], pharmacies.map(p => {
    const title = link(p.base_url, p.name ?? p.domain);
    const info = [p.domain, p.platform, p.scraper === "generic_jsonld" ? "Lettura JSON-LD" : null].filter(Boolean);
    const probe = p.jsonld_compatibility ? labelled(compatibilityLabels, p.jsonld_compatibility) : "n.d.";
    return [`${title}<small>${esc(info.join(" · "))}</small>`, labelled(registryLabels, p.status),
      labelled(scraperLabels, p.scraper_status), probe + (p.probe ? `<small>${num(p.probe.products_found?.length)} prodotti</small>` : ""),
      plain(p.source), plain(p.products_candidate_from_sitemap?.join(", ") || null)];
  }));
}

function registryContent(registry) {
  const phs = registry.pharmacies;
  if (!phs.length) return empty("Registro vuoto", "Le farmacie compariranno dopo la prossima ricerca di nuovi siti.");
  const count = test => phs.filter(test).length;
  const recent = count(p => {
    const age = Date.now() - Date.parse(p.discovered_at);
    return age >= 0 && age < 7 * 864e5;
  });
  const summary = stats([
    tile("Farmacie individuate", num(phs.length)),
    tile("Monitorate", num(count(p => p.status === "ACTIVE"))),
    tile("Da verificare", num(count(p => ["DISCOVERED", "UNDER_REVIEW"].includes(p.status)))),
  ]);
  const filter = `<label class="registry-filter">Cerca nel registro
    <input type="search" id="reg-filter" placeholder="Nome, dominio o stato" aria-controls="reg-results"></label>
    <p class="muted" id="reg-count" role="status"></p><div id="reg-results"></div>
    <p class="muted">L’attivazione di ogni farmacia richiede una verifica manuale. L’autorizzazione alla vendita di medicinali online copre una categoria diversa dai dermocosmetici.</p>`;
  const evaluated = phs.filter(p => p.status !== "ACTIVE" && p.probe)
    .sort((a, b) => (b.probe.products_found?.length || 0) - (a.probe.products_found?.length || 0));
  const probeList = evaluated.length ? table("Verifiche dei dati strutturati", ["Sito", "Compatibilità", "Prodotti trovati", "Verifica"], evaluated.map(p => [
    link(p.base_url, p.name), labelled(compatibilityLabels, p.jsonld_compatibility), num(p.probe.products_found?.length), fmtDate(p.probe.at),
  ])) : empty("Nessun sito valutato", "Attendi la prossima verifica dei dati strutturati.");
  const discovery = registry.last_discovery;
  const foot = discovery ? list(Object.entries(discovery.sources || {}), ([source, v]) =>
    `<strong>${esc(source)}</strong> · ${plain(v.status)}${v.found != null ? ` · ${num(v.found)} siti trovati` : ""}${v.note ? `<small>${esc(v.note)}</small>` : ""}`) : unavailable();
  return summary + details("Copertura e nuove farmacie", stats([
    tile("Verificate", num(count(p => ["VERIFIED", "ACTIVE"].includes(p.status)))),
    tile("Irraggiungibili o bloccate", num(count(p => p.status === "BLOCKED" || ["BLOCKED", "RATE_LIMITED"].includes(p.scraper_status)))),
    tile("Nuove negli ultimi 7 giorni", num(recent)),
  ]) + `<h3>Siti in valutazione</h3>${probeList}<h3>Ultima ricerca automatica</h3><p>${fmtDate(discovery?.at)}</p>${foot}`)
    + details(`Registro delle farmacie · ${num(phs.length)}`, filter);
}

function bindFilter(pharmacies) {
  const input = $("reg-filter");
  if (!input) return;
  const draw = () => {
    const query = input.value.trim().toLocaleLowerCase("it-IT");
    const filtered = pharmacies.filter(p => [p.name, p.domain, p.status, registryLabels[p.status]?.[1], p.jsonld_compatibility]
      .filter(Boolean).join(" ").toLocaleLowerCase("it-IT").includes(query));
    $("reg-results").innerHTML = registryTable(filtered);
    $("reg-count").textContent = `Risultati: ${num(filtered.length)} su ${num(pharmacies.length)}`;
  };
  const disclosure = input.closest("details");
  disclosure.addEventListener("toggle", () => { if (disclosure.open && !$("reg-results").hasChildNodes()) draw(); });
  input.addEventListener("input", draw);
}

export function renderMarket() {
  const { registry, data, errors } = state;
  let html = errors.registry ? failure("pharmacies.json") : "";
  if (registry) html += Array.isArray(registry.pharmacies) ? registryContent(registry) : unavailable();
  if (data) html += details("Classifica per copertura e prezzo", coverage(data)) + candidateOffers(data);
  if (errors.data) html += failure("data.json");
  $("market").innerHTML = html || unavailable();
  bindFilter(registry?.pharmacies);
}

export function renderSystem() {
  const { data, errors } = state;
  if (errors.data || !data?.generated_at) {
    $("system").innerHTML = errors.data ? failure("data.json") : empty("Nessuna scansione registrata", "Attendi la prima scansione automatica o avvia lo scraper.");
    return;
  }
  const s = data.stats || {};
  const unchecked = Number.isFinite(s.price_unverified) && Number.isFinite(s.match_unreliable)
    ? s.price_unverified + s.match_unreliable : null;
  const summary = stats([
    tile("Offerte acquistabili", `${num(s.offers_valid)} / ${num(s.offers_total)}`,
      s.offers_uncertain ? `${num(s.offers_uncertain)} con disponibilità da verificare` : ""),
    tile("Prezzi da verificare", num(unchecked)),
    tile("Spedizioni da verificare", num(s.shipping_unverified?.length)),
  ]);
  const phs = Object.values(data.pharmacies || {}).filter(p => p.last_run)
    .sort((a, b) => (a.name || "").localeCompare(b.name || "", "it"));
  const systems = phs.length ? table("Esito per farmacia", ["Farmacia", "Registro", "Scansione", "Prezzi verificati", "Ultimo successo", "Note"], phs.map(p => [
    link(p.base_url, p.name), labelled(registryLabels, p.status), labelled(scraperLabels, p.scraper_status),
    `${num(p.products_with_verified_price?.length)} / ${num(data.products?.length)}`, fmtDate(p.last_success),
    plain(p.errors?.slice(0, 2).join(" · ") || null),
  ])) : unavailable();
  const scope = data.scope?.type === "parziale" ? "Scansione parziale" : data.scope?.type === "completa" ? "Scansione completa" : "";
  const diagnostics = stats([
    tile("Ultima scansione", fmtDate(data.generated_at),
      [({ OK: "Completata", PARZIALE: "Parziale", ERRORE: "Errore" })[data.status] || "n.d.", scope].filter(Boolean).join(" · ")),
    tile("Durata", data.run?.duration_s == null ? "n.d." : `${num(Math.round(data.run.duration_s))} s`),
    tile("Prodotti non trovati", num(s.not_found)),
  ]);
  const reasons = (s.shipping_unverified || []).map(id => {
    const sh = data.shipping?.[id];
    return `${esc(pharmacyName(id))}${sh?.status_reason ? ` (${esc(sh.status_reason)})` : ""}`;
  });
  const shipping = reasons.length ? `<p>Tariffe di spedizione da verificare, escluse dal carrello verificato: ${reasons.join("; ")}.</p>` : "";
  const problems = data.errors?.filter(e => e.status !== "NON TROVATO") || [];
  const incidents = problems.length ? details(`Dettaglio problemi · ${num(problems.length)}`, list(problems, e =>
    `${pill("warning", e.status || "Da verificare")} <strong>${plain(e.product_id)} · ${plain(pharmacyName(e.pharmacy_id))}</strong><small>${plain(e.message)}</small>`)) : "";
  $("system").innerHTML = summary + details("Dettagli della scansione", diagnostics + shipping + systems) + incidents;
}

let chart;
let selectedKey;
let tablePreferred = false;

function historyOptions(entries) {
  const catalogue = products();
  if (catalogue.length) return catalogue.map(p => ({
    key: p.id, label: p.id, name: productName(p.id), product: p, id: p.id,
  }));
  // Senza catalogo, ogni formato storico rimane una serie distinta.
  const choices = new Map();
  for (const { product_id: id, format } of entries) {
    const key = JSON.stringify([id, format ?? null]);
    const label = `${id} · ${format || "formato n.d."}`;
    choices.set(key, { key, id, format, label, name: label });
  }
  return [...choices.values()].sort((a, b) => a.label.localeCompare(b.label, "it"));
}

function dailyPrices(entries, option) {
  const days = new Map();
  for (const entry of entries) {
    if (entry.product_id !== option.id || !Number.isFinite(entry.price) || !entry.date) continue;
    const current = option.product ? isCurrentFormat(entry, option.product) : (entry.format ?? null) === (option.format ?? null);
    if (!current) continue;
    const day = days.get(entry.date) || { date: entry.date, min: Infinity, total: 0, count: 0 };
    if (entry.price < day.min) {
      day.min = entry.price;
      day.pharmacy = entry.pharmacy_id;
    }
    day.total += entry.price;
    day.count++;
    days.set(entry.date, day);
  }
  return [...days.values()].sort((a, b) => a.date.localeCompare(b.date)).map(day => ({ ...day, mean: day.total / day.count }));
}

function historyChart(series) {
  const css = getComputedStyle(document.documentElement);
  const token = key => css.getPropertyValue(key).trim();
  const datasets = [
    { label: "Miglior prezzo", values: series.map(d => d.min), color: token("--blue-700"), dash: [] },
    { label: "Media del giorno", values: series.map(d => d.mean), color: token("--neutral-600"), dash: [6, 4] },
  ].map(d => ({
    label: d.label, data: d.values, borderColor: d.color, backgroundColor: d.color, borderDash: d.dash,
    borderWidth: 2, pointRadius: series.length < 20 ? 3 : 0, pointHoverRadius: 5, tension: 0,
  }));
  return new window.Chart($("hist-chart"), {
    type: "line", data: { labels: series.map(d => fmtDay(d.date)), datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false, locale: "it-IT",
      interaction: { mode: "index", intersect: false },
      color: token("--neutral-600"), font: { family: css.fontFamily },
      scales: {
        x: { grid: { display: false }, ticks: { color: token("--neutral-600"), maxTicksLimit: 6, maxRotation: 0 } },
        y: { beginAtZero: true, grid: { color: token("--neutral-200") }, ticks: { color: token("--neutral-600"), maxTicksLimit: 6, callback: money } },
      },
      plugins: {
        legend: { display: false },
        tooltip: { backgroundColor: token("--blue-900"), titleColor: token("--white"), bodyColor: token("--hero-ink"), callbacks: {
          label: item => `${item.dataset.label}: ${money(item.parsed.y)}`,
          afterBody: items => {
            const day = series[items[0].dataIndex];
            return [`Miglior prezzo presso ${pharmacyName(day.pharmacy)}`, `Offerte: ${num(day.count)}`];
          },
        } },
      },
    },
  });
}

function historyStats(series) {
  const first = series[0], last = series.at(-1);
  const cheapest = series.reduce((a, b) => a.min <= b.min ? a : b);
  const average = series.reduce((sum, day) => sum + day.min, 0) / series.length;
  const change = series.length > 1 && first.min > 0 ? (last.min - first.min) / first.min : null;
  return stats([
    tile("Minimo storico", money(cheapest.min), `${fmtDay(cheapest.date)} · ${pharmacyName(cheapest.pharmacy)}`),
    tile("Media", money(average), "Dei migliori prezzi giornalieri"),
    tile("Attuale", money(last.min), `${fmtDay(last.date)} · ${pharmacyName(last.pharmacy)}`),
    tile("Variazione", change == null ? "n.d." : percent(change), series.length > 1 ? `Dal ${fmtDay(first.date)}` : "Servono due giorni di dati"),
  ]);
}

function historyTable(series) {
  return table("Rilevazioni giornaliere", ["Giorno", "Miglior prezzo", "Farmacia", "Media", "Offerte"], [...series].reverse().map(d => [
    fmtDay(d.date), money(d.min), plain(pharmacyName(d.pharmacy)), money(d.mean), num(d.count),
  ]));
}

function drawHistory(option, entries) {
  chart?.destroy();
  chart = null;
  const series = dailyPrices(entries, option);
  const formatNote = option.product ? formatChangeNote(option.id) : "Catalogo indisponibile. La serie usa il formato indicato nella rilevazione.";
  const info = formatNote ? note(formatNote, "info") : "";
  const panel = $("hist-panel");
  panel.setAttribute("aria-labelledby", `hist-tab-${option.index}`);
  panel.innerHTML = `<h3>${esc(option.name)}</h3><div id="hist-stats">${info}${series.length ? historyStats(series) : ""}</div>`;
  if (!series.length) {
    panel.insertAdjacentHTML("beforeend", empty("Nessuna rilevazione per questo formato", "Scegli un altro prodotto o attendi una scansione con prezzi verificati."));
    return;
  }
  const canChart = series.length >= 2 && typeof window.Chart === "function";
  const short = series.length === 1 ? "È disponibile un solo giorno di dati. Il grafico apparirà dalla seconda rilevazione giornaliera."
    : series.length === 2 ? "Due giorni di dati disponibili. Il confronto descrive solo questo intervallo." : "";
  const chartFailed = series.length >= 2 && !canChart ? note("Il grafico è indisponibile. I prezzi giornalieri sono consultabili nella tabella.", "info") : "";
  const chartMarkup = canChart ? `<button class="btn" id="hist-toggle" type="button" aria-controls="hist-table" aria-pressed="${tablePreferred}">Vista tabella</button>
    <div id="hist-chart-view"><div class="chart-legend" role="group" aria-label="Serie visibili nel grafico"><button type="button" aria-pressed="true">Miglior prezzo</button><button type="button" aria-pressed="true">Media del giorno</button></div>
      <div class="chart-wrap"><canvas id="hist-chart" role="img" aria-label="Miglior prezzo e media giornaliera di ${esc(option.name)}. Periodo dal ${fmtDay(series[0].date)} al ${fmtDay(series.at(-1).date)}. Asse dei prezzi da zero euro. Valori consultabili in vista tabella."></canvas></div></div>` : "";
  panel.insertAdjacentHTML("beforeend", `${short ? `<p class="muted">${short}</p>` : ""}${chartFailed}${chartMarkup}<div id="hist-table">${historyTable(series)}</div>`);
  if (!canChart) return;
  const toggle = $("hist-toggle");
  const switchView = () => {
    $("hist-chart-view").hidden = tablePreferred;
    $("hist-table").hidden = !tablePreferred;
    toggle.setAttribute("aria-pressed", String(tablePreferred));
    if (!tablePreferred && !chart) chart = historyChart(series);
  };
  toggle.addEventListener("click", () => { tablePreferred = !tablePreferred; switchView(); });
  panel.querySelectorAll(".chart-legend button").forEach((button, i) => {
    button.addEventListener("click", () => {
      const visible = !chart.isDatasetVisible(i);
      chart.setDatasetVisibility(i, visible);
      chart.update();
      button.setAttribute("aria-pressed", String(visible));
    });
  });
  switchView();
}

export function renderHistory() {
  const entries = state.history?.entries;
  const root = $("history");
  if (state.errors.history || !Array.isArray(entries)) {
    chart?.destroy();
    chart = null;
    root.innerHTML = failure("price_history.json");
    return;
  }
  const options = historyOptions(entries).map((option, index) => ({ ...option, index }));
  if (!options.length) {
    chart?.destroy();
    chart = null;
    root.innerHTML = empty("Nessuna rilevazione nello storico", "Attendi la prossima scansione con prezzi verificati.");
    return;
  }
  const selected = options.find(o => o.key === selectedKey) || options[0];
  root.innerHTML = `<div class="tabs hist-tabs" role="tablist" aria-label="Prodotto dello storico">${options.map(o =>
    `<button type="button" role="tab" id="hist-tab-${o.index}" aria-label="${esc(o.name)}" aria-controls="hist-panel" aria-selected="${o === selected}" tabindex="${o === selected ? 0 : -1}">${esc(o.label)}</button>`).join("")}</div>
    <div role="tabpanel" id="hist-panel" tabindex="0"></div>`;
  const buttons = [...root.querySelectorAll('[role="tab"]')];
  const select = (index, focus) => {
    selectedKey = options[index].key;
    buttons.forEach((b, i) => {
      b.setAttribute("aria-selected", String(i === index));
      b.tabIndex = i === index ? 0 : -1;
    });
    if (focus) buttons[index].focus();
    drawHistory(options[index], entries);
  };
  buttons.forEach((button, i) => {
    button.addEventListener("click", () => select(i, false));
    button.addEventListener("keydown", event => {
      const n = options.length;
      const next = { ArrowLeft: (i + n - 1) % n, ArrowRight: (i + 1) % n, Home: 0, End: n - 1 }[event.key];
      if (next == null) return;
      event.preventDefault();
      select(next, true);
    });
  });
  select(selected.index, false);
}
