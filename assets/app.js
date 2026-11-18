import { $, state, esc, money, num, km, fmtDate, icon, products, productName, pharmacyName, isCurrentFormat, formatChangeNote, pill, note, empty, failure, link, details, table } from './ui.js';
import { renderHistory, renderMarket, renderSystem } from './secondary.js';

const preview = new URLSearchParams(location.search).get('data') || '';
const fixture = /^tests\/fixtures\/ui\/[a-z0-9-]+\/$/.test(preview) ? preview : '';
const prefKey = fixture ? 'dpt.location.preview.v1' : 'dpt.location.v1';
const channels = {
  home:['A domicilio','truck'], pickup:['Ritiro','bag'], store:['In farmacia','store'], mixed:['Misto','layers']
};
const transport = {mezzi_pubblici:['Mezzi','bus'],auto:['Auto','car'],piedi:['A piedi','walk'],bici:['Bici','bike']};
const availability = {'DISPONIBILE':'Disponibile','NON DISPONIBILE':'Esaurito','SCONOSCIUTA':'Da verificare'};
const formatAvailability = v => pill(v==='DISPONIBILE'?'success':v==='NON DISPONIBILE'?'error':'neutral',availability[v] || 'n.d.');
const glyphs = {P001:'bottle', P002:'bottle', P003:'tube', P004:'tube', P005:'jar'};
const purchase = {domicilio:'A domicilio',ritiro:'Ritiro',negozio:'In farmacia'};
const pref = () => {try {return JSON.parse(localStorage.getItem(prefKey)) || {};} catch {return {};}};
const itemList = items => '<ul class="row-list">'+items.map(x=>'<li>'+x+'</li>').join('')+'</ul>';
const cartQuality = c => !c?.complete || c.reliability==='incompleta' ? pill('warning','Non verificato')
  : c.reliability==='alta' ? pill('success','Verificato') : pill('warning','Parziale');
const catalogCount = () => state.data?.products?.length;
const countText = v => v===1?'1 farmacia':num(v)+' farmacie';

function renderFreshness() {
  const d=state.data, t=Date.parse(d?.generated_at);
  if (!Number.isFinite(t)) {
    $('freshness').innerHTML=pill('neutral','Aggiornamento n.d.');
    $('alerts').innerHTML='';
    return;
  }
  const hours=Math.max(0,(Date.now()-t)/3600000);
  const relative=hours<1?'Meno di un’ora fa':hours<2?'1 ora fa':num(Math.floor(hours))+' ore fa';
  const tone=hours<36?'success':hours<72?'warning':'error';
  $('freshness').innerHTML=`<span class="fresh-label"><span class="fresh-prefix">Ultimo aggiornamento</span><strong><time datetime="${esc(d.generated_at)}">${fmtDate(d.generated_at)}</time></strong></span>${pill(tone,relative)}`;
  let alerts=hours>=36?note('Dati da aggiornare. Prezzi e disponibilità risalgono a '+fmtDate(d.generated_at)+'.',tone):'';
  if (d.last_attempt?.at && d.last_attempt.at!==d.generated_at) alerts+=note(`Ultimo tentativo del ${fmtDate(d.last_attempt.at)}. ${d.last_attempt.message || d.last_attempt.status || 'Scansione non riuscita.'} Sono mostrati i dati precedenti.`,'error');
  $('alerts').innerHTML=alerts;
}
function cartDetails(c) {
  const best=c.best_overall;
  const orders=(best?.orders || []).map(o=>`<strong>${esc(o.seller_name)}</strong> · Prodotti ${money(o.subtotal)} · ${o.channel==='domicilio'?'Spedizione '+money(o.shipping):'Ritiro '+money(o.pickup_cost)}`);
  const trips=(best?.travel || []).map(t=>`${esc(t.name)} · ${km(t.distance_km)} · ${money(t.cost)} ${pill('estimate','Stima viaggio')}`);
  const choice=(label,cart)=>cart?`<strong>${esc(label)}</strong> · ${money(cart.total)} · ${esc(cart.mode_label)} · ${esc(cart.pharmacies?.map(pharmacyName).join(', '))} ${cartQuality(cart)}`:'';
  const alternatives=[choice('Una farmacia',c.best_single_pharmacy),choice('Al massimo due farmacie',c.best_max_two_pharmacies)].filter(Boolean);
  const ranks=[['domicilio','A domicilio'],['ritiro_fisico','Ritiro e farmacia'],['misto','Misto']].map(([key,label])=>{
    const rows=c.rankings?.[key]?.slice(0,5) || [];
    return `<h3>${label}</h3>`+(rows.length?itemList(rows.map(cart=>choice(cart.pharmacies.map(pharmacyName).join(', '),cart))):'<p class="muted">Nessuna combinazione calcolata.</p>');
  }).join('');
  const incomplete=(c.incomplete_alternatives || []).map(a=>`${esc(a.pharmacies?.map(pharmacyName).join(', '))} · Costo noto ≥ ${money(a.known_cost)} · ${esc(a.missing?.join('. ') || 'Costi da verificare')}`);
  return details('Ordini, classifiche e alternative',itemList([...orders,...trips,...alternatives])+ranks+
    (incomplete.length?'<h3>Alternative con costi incompleti</h3>'+itemList(incomplete):'')+
    `<p>${num(c.evaluated_combinations)} combinazioni valutate.</p>`+itemList([...(c.notes || []),...(best?.warnings || []),...(best?.missing || [])].map(esc)));
}
function heroBreakdown(best) {
  return '<div class="hero-cost">'+[['Prodotti',best.products_cost],['Spedizioni',best.shipping_cost],['Ritiro',best.pickup_cost],['Viaggio¹',best.travel_cost]].map(([name,value])=>
    `<div><strong>${money(value)}</strong><span>${name}</span></div>`).join('')+'</div>';
}
function heroItems(best,c) {
  const rows=(best.items || []).map(it=>{
    const symbol=glyphs[it.product_id] || 'bag';
    return `<li class="item-row"><span class="glyph ${symbol}">${icon(symbol)}</span><div class="item-copy"><strong>${esc(productName(it.product_id))}</strong><span>${link(it.url,it.seller_name)}</span></div><strong class="item-price">${money(it.price)}</strong></li>`;
  }).join('');
  const itemData=(best.items || []).map(it=>[esc(productName(it.product_id)),esc(purchase[it.channel] || it.channel || 'n.d.'),esc(it.match_level==='ID'?'EAN / Minsan':it.match_level || 'n.d.')]);
  return `<div class="hero-items"><div class="item-heading"><h3>Prodotti nel carrello</h3><small>${num(c.products_considered?.length)} di ${num(catalogCount())}</small></div><ul class="item-list">${rows}</ul>${details('Modalità e verifica dei prodotti',table('Verifica dei prodotti nel carrello',['Prodotto','Modalità','Corrispondenza'],itemData))}</div>`;
}
function renderHero() {
  const c=state.data?.carts, best=c?.best_overall;
  if (!best) {
    $('hero').innerHTML=empty('Nessun carrello disponibile','Servono offerte acquistabili con costi verificati. Controlla dopo la prossima scansione.')+(c?cartDetails(c):'');
    return;
  }
  const uncertain=c.complete_possible!==true || !best.complete || best.reliability!=='alta' || (c.incomplete_alternatives || []).some(a=>Number.isFinite(a.known_cost) && a.known_cost<best.total);
  const badge=uncertain?pill('warning','Parziale'):cartQuality(best);
  const saving=c.savings_vs_single;
  const reason=[];
  if (!c.complete_possible && c.products_without_offers?.length) reason.push('Senza offerta acquistabile: '+c.products_without_offers.map(productName).join(', ')+'.');
  if (uncertain) reason.push('Il risultato copre solo i prodotti e i costi verificabili.');
  const savingsText=saving?.amount===0?'Il miglior totale coincide con l’acquisto in un solo punto.':
    `Risparmio rispetto a un solo punto: <b>${money(saving?.amount)}</b>${saving?.single_pharmacy?' ('+esc(pharmacyName(saving.single_pharmacy))+', '+money(saving.single_total)+')':''}.`;
  const price=money(best.total).replace(/\s*€$/, ' <span class="currency">€</span>');
  $('hero').innerHTML=`<div class="hero-grid"><div class="hero-total"><div class="hero-label">${icon('basket')} Totale del carrello</div><div class="total">${price}</div>
    <p class="hero-mode">${esc(best.mode_label || 'Modalità n.d.')}</p>
    <div class="hero-meta">${badge}<span>${num(c.products_considered?.length)} su ${num(catalogCount())} prodotti · ${countText(best.n_pharmacies)}</span></div>
    ${heroBreakdown(best)}<p class="saving">${savingsText}</p></div>${heroItems(best,c)}</div>
    ${reason.length?note(reason.join(' '),'warning'):''}
    <div class="hero-extra">${cartDetails(c)}${details('Come leggere l’affidabilità e i costi',`<p>Verificato indica che prodotti e costi richiesti sono documentati. Parziale indica copertura o certezza limitata. Non verificato segnala dati mancanti o fonti in valutazione.</p><p>¹ Stima viaggio: costo monetario di andata e ritorno per il mezzo configurato, escluso il tempo. Una spedizione non verificata ha tariffa sconosciuta e può cambiare il confronto.</p>`)}</div>`;
}
function renderProducts() {
  const d=state.data, catalogue=products();
  if (!catalogue.length) { $('products').innerHTML=empty('Catalogo indisponibile','Attendi una scansione con i dati dei prodotti.');return; }
  const rows=[], all=[];
  for (const p of catalogue) {
    const offers=(d.offers || []).filter(o=>o.product_id===p.id && isCurrentFormat(o,p));
    const best=d.best_prices?.[p.id];
    const currentBest=best && isCurrentFormat(best,p) || best && offers.some(o=>o.usable && o.pharmacy_id===best.pharmacy_id && o.price===best.price);
    const b=currentBest?best:null;
    rows.push([`<strong>${esc(p.name)}</strong><small>${esc(p.id)} · ${esc(p.format?.label || 'Formato n.d.')}</small>`,
      `<b class="best-price">${money(b?.price)}</b>`, b?link(b.url,b.pharmacy_name):'n.d.',money(b?.single_item_shipping),money(b?.single_item_total),
      b?formatAvailability(b.availability):pill('neutral','Nessuna offerta')]);
    const diagnostics=offers.sort((a,b)=>Number(Boolean(b.usable))-Number(Boolean(a.usable)) || (a.price??Infinity)-(b.price??Infinity)).map(o=>[link(o.url,o.pharmacy_name || pharmacyName(o.pharmacy_id)),
      money(o.price),money(o.single_item_shipping),money(o.single_item_total),o.status==='OK'?formatAvailability(o.availability):pill('warning',o.status || 'Non verificato')]);
    const changed=formatChangeNote(p.id);
    all.push(details(p.id+' · '+p.name,`<p>EAN ${esc(p.ean || 'n.d.')} · Minsan ${esc(p.minsan || 'n.d.')}</p>`+(changed?note(changed):'')+
      (diagnostics.length?table('Offerte per '+p.name,['Farmacia','Prezzo','Spedizione','Totale','Esito'],diagnostics):empty('Nessuna offerta','Controlla dopo la prossima scansione.'))));
  }
  $('products').innerHTML=(!d.offers?.length?note('Nessuna offerta rilevata. Controlla dopo la prossima scansione.','warning'):'')+
    `<p class="table-hint">Prezzo e spedizione per l’acquisto di un singolo prodotto.</p><div class="table-wrap">${table('Migliori offerte per prodotto',['Prodotto','Miglior prezzo','Farmacia','Spedizione','Totale','Disponibilità'],rows)}</div><div class="product-extra">${details('Tutte le offerte per prodotto',all.join(''))}</div>`;
}

function modeCards(c) {
  const modes=c?.by_mode || {};
  // Gli snapshot precedenti alle differenze per canale contengono comunque le classifiche.
  const legacy={home:c?.rankings?.domicilio?.[0],pickup:modes.B,store:modes.C,mixed:c?.rankings?.misto?.[0] || modes.BC};
  return '<div class="mode-grid">'+Object.entries(channels).map(([key,[name,symbol]])=>{
    const entry=c?.channel_comparison?.[key], cart=modes[entry?.mode] || legacy[key];
    const chosen=cart && cart.mode===c.best_overall?.mode;
    const delta=entry?.difference;
    const detail=chosen?'Miglior totale calcolato':cart && delta!=null?(delta===0?'Stesso totale':'+ '+money(delta)+' sul migliore'):cart?'Differenza n.d.':'Totale non disponibile';
    return `<article class="mode-card ${chosen?'chosen':''}"><div class="mode-top">${icon(symbol)}${chosen?pill('info','Consigliata'):''}</div><h3>${name}</h3><strong class="mode-price ${cart?'':'absent'}">${money(cart?.total)}</strong><small class="${delta>0?'delta':''}">${detail}</small></article>`;
  }).join('')+'</div>';
}
function localComparison(ph,c) {
  if (!ph) return empty('Farmacie fisiche indisponibili','Aggiorna i dati delle farmacie fisiche con la prossima scansione.');
  const prefs=pref(), mode=prefs.mode || ph.travel?.mode, radius=prefs.max_km ?? ph.travel?.max_distance_km;
  const stores=(ph.pharmacies || []).filter(s=>s.distance_km!=null && (radius==null || s.distance_km<=radius)).sort((a,b)=>a.distance_km-b.distance_km);
  const pc=c?.physical_comparison, nearest=stores[0];
  function trip(store) {
    const estimate=c?.travel_previews?.[store.id]?.[mode];
    if (estimate) return estimate;
    if (mode===store.travel?.mode) return {cost:store.travel.cost,threshold:pc?.nearest_store?.id===store.id?pc.break_even_store_total:null};
    return {};
  }
  const costs=nearest?trip(nearest):{};
  const summary=nearest?`<div class="threshold">${icon('pin')}<div><strong>${esc(nearest.name)}</strong> · ${esc(ph.location?.label || 'Località n.d.')} · ${km(nearest.distance_km)}<p>Viaggio A/R ${money(costs.cost)} (${esc(transport[mode]?.[0] || 'mezzo n.d.')}, stima). Soglia di convenienza in negozio <b>${money(costs.threshold)}</b> per ${num(c?.products_considered?.length)} prodotti.</p></div></div>`:
    note('Nessuna farmacia fisica entro la distanza selezionata. Controlla la località e il raggio nelle impostazioni.','info');
  const rows=stores.slice(0,12).map(s=>{
    const confirmed=Object.values(s.availability || {}).filter(v=>v==='confermata').length;
    return [link(s.website || s.osm_url,s.name)+(s.kind==='parafarmacia'?'<small>Parafarmacia</small>':''),esc(s.address || 'n.d.'),km(s.distance_km),money(trip(s).cost),
      confirmed?pill('success',confirmed+' confermati'):pill('warning','Non verificato')];
  });
  const pickup=(ph.pickup_points || []).map(p=>`<strong>${esc(p.name || pharmacyName(p.pharmacy_id))}</strong> · ${esc([p.address,p.city].filter(Boolean).join(', '))} · ${km(p.distance_km)} · Ritiro ${money(p.pickup_cost_eur)} · ${esc(p.distance_km==null?'Distanza n.d.':p.within_max_distance?'Prenotabile':'Oltre la distanza della scansione')}`);
  const modified=mode!==ph.travel?.mode || radius!==ph.travel?.max_distance_km;
  return summary+details('Farmacie vicine, disponibilità e ritiro', (modified?note('Anteprima locale per mezzo e distanza selezionati. I totali dei carrelli usano i parametri dell’ultima scansione.'):'')+
    `<p>Distanze in linea d’aria dal centro di ${esc(ph.location?.label || 'località n.d.')}. Fattore strada ${num(ph.travel?.road_factor)}.</p>`+
    (rows.length?table('Farmacie vicine',['Farmacia','Indirizzo','Distanza','Viaggio (stima)','Disponibilità'],rows):'')+
    (pickup.length?'<h3>Punti di ritiro degli ordini online</h3>'+itemList(pickup):'')+
    `<p>${esc(ph.notes?.join(' ') || '')} ${esc(ph.attribution || '')}</p>`);
}
function renderPhysical() {
  const d=state.data;
  $('physical').innerHTML=modeCards(d?.carts)+(d?.carts?.complete_possible===false?'<p class="table-hint">Confronto riferito ai soli prodotti acquistabili.</p>':'')+
   '<div class="physical-detail">'+localComparison(d?.physical,d?.carts)+'</div>';
}
function renderSettings() {
  const f=$('loc-form').elements, prefs=pref(), ph=state.data?.physical;
  f.city.value=prefs.city ?? ph?.location?.city ?? '';
  f.postcode.value=prefs.postcode ?? ph?.location?.postcode ?? '';
  f.mode.value=prefs.mode || ph?.travel?.mode || 'mezzi_pubblici';
  f.max_km.value=prefs.max_km ?? ph?.travel?.max_distance_km ?? '';
  const differs=(prefs.city && prefs.city.toLocaleLowerCase('it')!==ph?.location?.city?.toLocaleLowerCase('it')) || (prefs.postcode && prefs.postcode!==ph?.location?.postcode);
  $('loc-info').innerHTML=`Ultima scansione: <strong>${esc(ph?.location?.label || 'n.d.')}</strong> · ${esc(transport[ph?.travel?.mode]?.[0] || 'mezzo n.d.')} · ${km(ph?.travel?.max_distance_km)}.`+
    (differs?note('Per aggiornare le farmacie della nuova località, avvia il workflow Scrape prezzi con il parametro location. Le distanze mostrate si riferiscono ancora all’ultima scansione.','warning'):'');
}
function bindSettings() {
  $('travel-modes').innerHTML=Object.entries(transport).map(([v,[label,symbol]])=>`<label><input type="radio" name="mode" value="${v}"><span>${icon(symbol)}${label}</span></label>`).join('');
  $('loc-form').addEventListener('submit',e=>{
    e.preventDefault();
    const f=e.currentTarget.elements, city=f.city.value.trim();
    if (/\d|\b(via|viale|piazza|corso|largo)\b/i.test(city)) {
      $('saved').textContent='Inserisci solo il nome della città. Per il codice postale usa il campo CAP.';
      f.city.focus();return;
    }
    try {
      localStorage.setItem(prefKey,JSON.stringify({city,postcode:f.postcode.value.trim(),mode:f.mode.value,max_km:f.max_km.value?Number(f.max_km.value):null}));
      refreshPreferences('Preferenze salvate in questo browser.');
    } catch { $('saved').textContent='Salvataggio non riuscito. Controlla le impostazioni di archiviazione del browser.'; }
  });
  $('reset').addEventListener('click',()=>{
    try {localStorage.removeItem(prefKey);refreshPreferences('Preferenze ripristinate.');}
    catch {$('saved').textContent='Impossibile ripristinare le preferenze in questo browser.';}
  });
}
function refreshPreferences(message) {
  if(state.data) renderPhysical();
  renderSettings(); $('saved').textContent=message;
}
function bindNav() {
  const anchors=[...document.querySelectorAll('.nav-strip a')], mast=document.querySelector('.mast');
  function update() {
    const cutoff=mast.getBoundingClientRect().bottom+32;
    let active=anchors[0];
    for(const a of anchors) {
      const target=document.querySelector(a.hash);
      if(target.getBoundingClientRect().top<=cutoff && (a.hash!=='#localita' || innerWidth<1000 || location.hash==='#localita')) active=a;
    }
    for(const a of anchors) {if(a===active)a.setAttribute('aria-current','location');else a.removeAttribute('aria-current');}
    const nav=active.parentElement;
    if(nav.scrollWidth>nav.clientWidth && nav.dataset.active!==active.hash) nav.scrollLeft=active.offsetLeft-nav.offsetLeft-(nav.clientWidth-active.offsetWidth)/2;
    nav.dataset.active=active.hash;
  }
  new ResizeObserver(()=>{document.documentElement.style.setProperty('--mast-height',mast.offsetHeight+'px');update();}).observe(mast);
  window.addEventListener('scroll',update,{passive:true});window.addEventListener('hashchange',update); update();
}
function section(id,fn,errorFile) {
  const root=$(id);
  try {if(errorFile) root.innerHTML=failure(errorFile);else fn();}
  catch (error) {root.innerHTML=note('Impossibile mostrare questa sezione. Verifica la struttura dei dati e riprova.','error');console.error(error);}
  root.classList.remove('loading','hero-load');root.setAttribute('aria-busy','false');
}
async function read(key,path) {
  try {
    const url=fixture+path.replace(/^\.\//,'');
    const response=await fetch(url,{cache:'no-store'});
    if (!response.ok) throw Error(response.status);
    const data=await response.json();
    if(!data || typeof data!=='object' || Array.isArray(data) ||
      (key==='history' && !Array.isArray(data.entries)) || (key==='registry' && !Array.isArray(data.pharmacies))) throw Error('Formato');
    state[key]=data;
  } catch {state.errors[key]=path;}
}
$('fixture-banner').hidden=!fixture;
bindNav();bindSettings();
const paths=document.body.dataset;
const mainData=read('data',paths.source).then(()=>{
  renderFreshness();
  for(const [id,render] of [['hero',renderHero],['physical',renderPhysical],['products',renderProducts],['system',renderSystem]]) section(id,render,state.errors.data && 'data.json');
  renderSettings();
});
const registry=read('registry',paths.registry);
const history=read('history',paths.history);
Promise.all([mainData,registry]).then(()=>section('market',renderMarket));
Promise.all([mainData,history]).then(()=>section('history',renderHistory));
