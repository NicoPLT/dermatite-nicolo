export const $ = id => document.getElementById(id);
export const state = {data:null, history:null, registry:null, errors:{}};
export const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const currency = new Intl.NumberFormat('it-IT',{style:'currency',currency:'EUR'});
const number = new Intl.NumberFormat('it-IT',{maximumFractionDigits:2});
const percentage = new Intl.NumberFormat('it-IT',{style:'percent',maximumFractionDigits:1,signDisplay:'exceptZero'});
const dateTime = new Intl.DateTimeFormat('it-IT',{dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Rome'});
const day = new Intl.DateTimeFormat('it-IT',{day:'2-digit',month:'2-digit',year:'numeric',timeZone:'Europe/Rome'});
const formatted = (formatter,value) => Number.isFinite(value) ? formatter.format(value) : 'n.d.';
export const money = v => formatted(currency,v);
export const num = v => formatted(number,v);
export const percent = v => formatted(percentage,v);
export const fmtDate = s => formatted(dateTime,Date.parse(s));
export const fmtDay = s => formatted(day,Date.parse(s));
export const km = v => Number.isFinite(v) ? num(v)+' km' : 'n.d.';
export const products = () => [...(state.data?.products || [])].sort((a,b)=>a.id.localeCompare(b.id));
export const pharmacyName = id => state.data?.pharmacies?.[id]?.name || state.registry?.pharmacies?.find(p=>p.id===id)?.name || id || 'n.d.';
export const productName = id => {
  const p = products().find(p=>p.id===id);
  return p ? [p.name,p.format?.label].filter(Boolean).join(' ') : id || 'n.d.';
};
export function isCurrentFormat(entry,product) {
  if (entry.format) return !product?.format?.label || entry.format === product.format.label;
  return !product?.format_history?.length;
}
export function formatChangeNote(id) {
  const p = products().find(p=>p.id===id), last = p?.format_history?.at(-1);
  if (!last) return '';
  const old = state.history?.entries?.filter(e=>e.product_id===id && !isCurrentFormat(e,p)).length;
  return `Formato cambiato da ${last.label} a ${p.format.label} il ${fmtDay(last.until)}. `+
    (old ? `${old} rilevazioni del formato ${last.label} sono escluse dallo storico mostrato.` : 'Lo storico mostra il formato corrente.');
}
const paths = {
 bag:'<path d="M5 8h14l1 13H4L5 8Z"/><path d="M9 8V6a3 3 0 0 1 6 0v2"/>',
 basket:'<path d="M3 10h18l-2 10H5zM7 10l4-7m6 7-4-7M9 14v3m6-3v3"/>',
 truck:'<path d="M2 5h12v12H2zM14 9h4l4 4v4h-8"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/>',
 store:'<path d="m3 10 2-6h14l2 6M4 10v10h16V10M3 10c0 4 5 4 6 0 1 4 5 4 6 0 1 4 6 4 6 0M9 20v-5h6v5"/>',
 pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
 layers:'<path d="m3 8 9-5 9 5-9 5zM3 12l9 5 9-5M3 16l9 5 9-5"/>',
 check:'<path d="m5 12 4 4L19 6"/>', shield:'<path d="M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6z"/><path d="m8 12 3 3 5-6"/>',
 warning:'<path d="M12 3 2 20h20zM12 9v4m0 3v.1"/>', error:'<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6m0-6-6 6"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
 external:'<path d="M14 4h6v6m-1-5-9 9m0-9H5v14h14v-5"/>', chevron:'<path d="m9 5 7 7-7 7"/>',
 arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>', down:'<path d="M12 4v15m-5-5 5 5 5-5"/>',
 bars:'<path d="M4 19h16M6 15v-4m5 4V6m5 9v-7"/>', tag:'<path d="M3 4h8l10 10-7 7L3 10z"/><circle cx="7.5" cy="7.5" r="1"/>',
 target:'<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/>',
 bottle:'<path d="M10 5V2h7m-7 3h4v3h-4zM8 9h8l1 3v9H7v-9zM7 13h10"/>',
 tube:'<path d="M6 3h12l-2 15H8L6 3ZM9 18h6v3H9zM8 7h8"/>',
 jar:'<rect x="5" y="9" width="14" height="12" rx="2"/><path d="M6 5h12v4H6zM5 15h14"/>',
 bus:'<rect x="5" y="3" width="14" height="16" rx="3"/><path d="M5 11h14M8 15h1m6 0h1m-9 4v2m10-2v2"/>',
 car:'<path d="m4 11 2-6h12l2 6v7H4zM4 11h16M7 15h1m8 0h1m-12 3v2m14-2v2"/>',
 walk:'<circle cx="14" cy="4" r="2"/><path d="m7 11 5-4 3 5 4 1m-7-5-2 7 4 3 2 4m-6-7-4 7"/>',
 bike:'<circle cx="5.5" cy="16" r="4"/><circle cx="18.5" cy="16" r="4"/><path d="m6 16 4-8 4 8H6m7-12h3l3 12M8 7h4"/>',
 list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.1M3 12h.1M3 18h.1"/>'
};
export const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${paths[name] || paths.info}</svg>`;
const tones = {success:'shield',warning:'warning',error:'error',info:'info',neutral:'info',estimate:'clock'};
export const pill = (kind,label) => `<span class="pill ${Object.hasOwn(tones,kind)?kind:'neutral'}">${icon(tones[kind])}${esc(label)}</span>`;
export const note = (message,kind='info') => `<p class="notice ${Object.hasOwn(tones,kind)?kind:'info'}">${icon(tones[kind])}<span>${esc(message)}</span></p>`;
export const empty = (title,body) => `<div class="empty">${icon('bag')}<div><strong>${esc(title)}</strong><p>${esc(body)}</p></div></div>`;
export const failure = filename => note(`Impossibile leggere ${filename}. Ricarica la pagina per riprovare.`,'error');
export function link(url,label) {
  try { const u=new URL(url); if (['http:','https:'].includes(u.protocol)) return `<a class="ext" href="${esc(u.href)}" target="_blank" rel="noopener noreferrer">${esc(label || u.hostname)}${icon('external')}</a>`; } catch {}
  return esc(label || 'n.d.');
}
export const details = (title,html) => `<details><summary>${esc(title)}${icon('chevron')}</summary><div class="details-body">${html}</div></details>`;
export const tile = (label,value,detail='') => `<div class="tile"><strong>${esc(value)}</strong><span>${esc(label)}</span>${detail?`<small>${esc(detail)}</small>`:''}</div>`;
export function table(caption,headers,rows) {
  return `<table><caption class="sr-only">${esc(caption)}</caption><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(cells=>`<tr>${cells.map((cell,i)=>i===0?`<th scope="row">${cell}</th>`:`<td data-label="${esc(headers[i])}"><span>${cell}</span></td>`).join('')}</tr>`).join('')}</tbody></table>`;
}
