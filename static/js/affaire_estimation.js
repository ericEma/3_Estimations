/**
 * affaire_estimation.js — Page Estimation d'affaire (double calque référentiel / saisie).
 * Sauvegarde debounce 600 ms vers /api/affaire/<id>/estimation/save
 *
 * Communications internes — En charge du lot électricité
 */

'use strict';

const CHAP_META = {
  'Courants Forts':   { cls: 's-cfo', banner: 'cfo' },
  'Courants faibles': { cls: 's-cfa', banner: 'cfa' },
  'Photovoltaïque':   { cls: 's-pv',  banner: 'pv'  },
};
const CHAP_ORDER = ['Courants Forts', 'Courants faibles', 'Photovoltaïque'];
const CUSTOM_CHAP = '__Hors_catalogue__';

let sdo = INIT_SDO;
let kwc = INIT_KWC;
let ccfo = typeof INIT_CCFO === 'number' ? INIT_CCFO : 1;
let ccfa = typeof INIT_CCFA === 'number' ? INIT_CCFA : 1;
let cpv = typeof INIT_CPV === 'number' ? INIT_CPV : 1;
let tauxPhase = typeof INIT_TAUX_PHASE === 'number' ? INIT_TAUX_PHASE : 3;
const PHASE_PRESETS = { DIAG: 6, APS: 4, APD: 3, PRO: 1 };

let searchQ = '';
let expandedChaps = new Set();
let expandedSecs  = new Set();
let expandedCustom = false;

let localCatalog = (CATALOG_ROWS || []).map(r => Object.assign({}, r));
localCatalog.forEach(r => {
  const q = parseFloat(r.quantity);
  r.quantity = Number.isFinite(q) ? q : 0;
});
let localCustom  = (CUSTOM_ROWS || []).map(r => Object.assign({}, r));
let tempLineSeq  = -1;

const dirtyMap = new Map();
const tempLineToReal = new Map();
let saveTimer = null;
let saveInFlight = false;

let paramsTimer = null;
let chapSaveTimer = null;
let secSaveTimer = null;

function secUiKey(chap, sec) {
  return `${chap}|||${sec}`;
}

function expandStorageKey() {
  return `estim_expand_${AFFAIRE_ID}`;
}

function saveExpandStateBeforeReload() {
  try {
    sessionStorage.setItem(expandStorageKey(), JSON.stringify({
      chaps: [...expandedChaps],
      secs: [...expandedSecs],
      custom: expandedCustom,
    }));
  } catch (e) { /* quota / navigation privée */ }
}

function restoreExpandStateAfterReload() {
  try {
    const raw = sessionStorage.getItem(expandStorageKey());
    if (!raw) return;
    sessionStorage.removeItem(expandStorageKey());
    const st = JSON.parse(raw);
    if (Array.isArray(st.chaps)) st.chaps.forEach(c => expandedChaps.add(c));
    if (Array.isArray(st.secs)) st.secs.forEach(s => expandedSecs.add(s));
    if (st.custom) expandedCustom = true;
  } catch (e) { /* ignore */ }
}

function reloadEstimationPage() {
  saveExpandStateBeforeReload();
  window.location.reload();
}

function sortOrderValue(value, fallback = 999999) {
  const n = parseInt(value, 10);
  return Number.isFinite(n) ? n : fallback;
}

/** État inclusion chapitres (persisté ``affaire_chapter_settings``) — En charge du lot électricité */
const chapterState = {};
function defaultChapterRow(name) {
  return {
    chapter: name,
    chapter_key: `chap:${name}`,
    is_included: true,
    use_macro: false,
    qty: 1.0,
  };
}
for (const name of CHAP_ORDER) chapterState[name] = defaultChapterRow(name);
for (const s of (typeof INIT_CHAPTER_STATE !== 'undefined' && INIT_CHAPTER_STATE) || []) {
  if (!s || !s.chapter) continue;
  chapterState[s.chapter] = {
    chapter: s.chapter,
    chapter_key: s.chapter_key || `chap:${s.chapter}`,
    is_included: s.is_included !== false,
    use_macro: !!s.use_macro,
    qty: parseFloat(s.qty) || 1.0,
    ratio_m2_override: null,
  };
}

/** Inclusion sous-chapitres (clés ``sect:chap|section`` en BDD) */
const sectionState = {};
for (const s of (typeof INIT_SECTION_STATE !== 'undefined' && INIT_SECTION_STATE) || []) {
  if (!s || !s.chapter || s.section == null || s.section === '') continue;
  const k = secUiKey(s.chapter, s.section);
  sectionState[k] = {
    chapter: s.chapter,
    section: s.section,
    chapter_key: s.chapter_key || `sect:${s.chapter}|${s.section}`,
    is_included: s.is_included !== false,
    use_macro: !!s.use_macro,
    qty: parseFloat(s.qty) || 1.0,
    ratio_m2_override: s.ratio_m2_override != null ? parseFloat(s.ratio_m2_override) : null,
    is_local: !!s.is_local,
    sort_order: sortOrderValue(s.sort_order),
  };
}

const sectionSortOrder = {};
for (const s of (typeof INIT_SECTION_STATE !== 'undefined' && INIT_SECTION_STATE) || []) {
  if (s && s.chapter && s.section != null) {
    sectionSortOrder[secUiKey(s.chapter, s.section)] = sortOrderValue(s.sort_order);
  }
}

function sectionSortCompare(chap, secA, secB) {
  const oa = sectionSortOrder[secUiKey(chap, secA)] ?? 999999;
  const ob = sectionSortOrder[secUiKey(chap, secB)] ?? 999999;
  if (oa !== ob) return oa - ob;
  return String(secA).localeCompare(String(secB), 'fr');
}

function sortedSectionEntries(chap, treeChap) {
  return Object.entries(treeChap || {}).sort((a, b) => sectionSortCompare(chap, a[0], b[0]));
}

function ensureSectionState(chap, sec) {
  const k = secUiKey(chap, sec);
  if (!sectionState[k]) {
    sectionState[k] = {
      chapter: chap,
      section: sec,
      chapter_key: `sect:${chap}|${sec}`,
      is_included: true,
      use_macro: false,
      qty: 1.0,
      ratio_m2_override: null,
      is_local: false,
      sort_order: 999999,
    };
  }
  return sectionState[k];
}

function flushSavePromise() {
  return new Promise(resolve => {
    if (saveTimer) {
      clearTimeout(saveTimer);
      saveTimer = null;
    }
    const waitDone = () => {
      if (!saveInFlight && dirtyMap.size === 0) resolve();
      else setTimeout(waitDone, 80);
    };
    if (dirtyMap.size === 0 && !saveInFlight) {
      resolve();
      return;
    }
    flushSave();
    setTimeout(waitDone, 80);
  });
}

async function callEstimationPromote(action, body) {
  await flushSavePromise();
  try {
    const res = await fetch(`/api/affaire/${AFFAIRE_ID}/estimation/promote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...body }),
    });
    const data = await res.json();
    if (!data.ok) {
      showFlash(data.message || 'Promotion refusée', true);
      return null;
    }
    return data;
  } catch (e) {
    console.warn('promote', e);
    showFlash('Erreur réseau (promotion)', true);
    return null;
  }
}

function promoteEstimationSection(chap, sec) {
  if (!confirm(`Ajouter la section « ${sec} » à la base de prix ?\nElle sera disponible pour les prochaines affaires.`)) return;
  callEstimationPromote('promote_section', { chapter: chap, section: sec }).then(data => {
    if (data) {
      showFlash(`Section ajoutée à la base de prix (${data.articles_promoted || 0} article(s))`);
      reloadEstimationPage();
    }
  });
}

function promoteEstimationArticle(lineId, label) {
  const name = label || 'cet article';
  if (!confirm(`Ajouter « ${name} » à la base de prix ?`)) return;
  callEstimationPromote('promote_article', { line_id: lineId }).then(data => {
    if (data) {
      showFlash('Article ajouté à la base de prix');
      reloadEstimationPage();
    }
  });
}

async function callEstimationLayout(action, body) {
  try {
    const res = await fetch(`/api/affaire/${AFFAIRE_ID}/estimation/layout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, ...body }),
    });
    const data = await res.json();
    if (!data.ok) {
      showFlash(data.message || 'Opération refusée', true);
      return null;
    }
    if (data.totals) updateKpiStrip(data.totals);
    if (data.section_sort) {
      for (const s of data.section_sort) {
        sectionSortOrder[secUiKey(s.chapter, s.section)] = sortOrderValue(s.sort_order);
      }
    }
    return data;
  } catch (e) {
    console.warn('layout', e);
    showFlash('Erreur réseau (layout)', true);
    return null;
  }
}

function addEstimationSection(chap, afterSec) {
  callEstimationLayout('add_section', {
    chapter: chap,
    section_name: '',
    after_section: afterSec || '',
  }).then(data => {
    if (data) {
      const sec = data.section;
      if (sec) {
        expandedSecs.add(secUiKey(chap, sec));
        expandedChaps.add(chap);
      }
      showFlash('Sous-chapitre ajouté');
      reloadEstimationPage();
    }
  });
}

function addEstimationArticle(chap, sec, afterDpgf, afterLine) {
  const body = { chapter: chap, section: sec };
  if (afterDpgf) body.after_dpgf_id = afterDpgf;
  if (afterLine) body.after_line_id = afterLine;
  callEstimationLayout('add_article', body).then(data => {
    if (data) {
      showFlash('Article ajouté');
      reloadEstimationPage();
    }
  });
}

function deleteEstimationSection(chap, sec) {
  const secArts = localCatalog.filter(r => r.chapter === chap && r.section === sec);
  const count = secArts.length;
  const st = ensureSectionState(chap, sec);
  const msg = st.is_local
    ? `Supprimer la section « ${sec} » et ses ${count} ligne(s) ?`
    : `Retirer la section « ${sec} » de cette estimation (${count} ligne(s)) ?`;
  if (!confirm(msg)) return;
  callEstimationLayout('delete_section', { chapter: chap, section: sec }).then(data => {
    if (data) {
      showFlash(st.is_local ? 'Section supprimée' : 'Section retirée');
      reloadEstimationPage();
    }
  });
}

function deleteEstimationArticle(chap, sec, dpgfId, lineId, isTree) {
  const msg = isTree
    ? 'Supprimer cet article de l\'estimation ?'
    : 'Retirer cet article de cette estimation ?';
  if (!confirm(msg)) return;
  const body = { chapter: chap, section: sec };
  if (dpgfId) body.dpgf_id = dpgfId;
  if (lineId) body.line_id = lineId;
  callEstimationLayout('delete_article', body).then(data => {
    if (data) {
      showFlash(isTree ? 'Article supprimé' : 'Article retiré');
      reloadEstimationPage();
    }
  });
}

function moveEstimationSection(chap, sec, direction) {
  callEstimationLayout('move_section', { chapter: chap, section: sec, direction }).then(data => {
    if (data) reloadEstimationPage();
  });
}

function moveEstimationArticle(chap, sec, direction, dpgfId, lineId) {
  const body = { chapter: chap, section: sec, direction };
  if (dpgfId) body.dpgf_id = dpgfId;
  if (lineId) body.line_id = lineId;
  callEstimationLayout('move_article', body).then(data => {
    if (data) reloadEstimationPage();
  });
}

function sectionActionButtons(chap, sec, secArts) {
  const lastArt = secArts[secArts.length - 1];
  const afterDpgf = lastArt && lastArt.dpgf_id ? lastArt.dpgf_id : null;
  const afterLine = lastArt && lastArt.is_tree_custom ? lastArt.line_id : null;
  const st = ensureSectionState(chap, sec);
  const delBtn = `<button type="button" class="sec-del-btn" title="Supprimer ce sous-chapitre de l'estimation"
         onclick="event.stopPropagation();window.__est.deleteSection('${escJ(chap)}','${escJ(sec)}')">
         <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 4h10M6 4V3h4v1M5 4v8a1 1 0 001 1h4a1 1 0 001-1V4"/><path d="M7 7v4M9 7v4"/></svg>
       </button>`;
  const promoteSecBtn = st.is_local
    ? `<button type="button" class="estim-promote-btn" title="Ajouter cette section à la base de prix"
         onclick="event.stopPropagation();window.__est.promoteSection('${escJ(chap)}','${escJ(sec)}')">📖</button>`
    : '';
  return `
    <button type="button" class="estim-move-btn" title="Monter la section"
      onclick="event.stopPropagation();window.__est.moveSection('${escJ(chap)}','${escJ(sec)}','up')">▲</button>
    <button type="button" class="estim-move-btn" title="Descendre la section"
      onclick="event.stopPropagation();window.__est.moveSection('${escJ(chap)}','${escJ(sec)}','down')">▼</button>
    <button type="button" class="add-row-btn" title="Ajouter un article"
      onclick="event.stopPropagation();window.__est.addArticle('${escJ(chap)}','${escJ(sec)}',${afterDpgf || 'null'},${afterLine || 'null'})">+</button>
    <button type="button" class="add-sec-btn" title="Section après"
      onclick="event.stopPropagation();window.__est.addSection('${escJ(chap)}','${escJ(sec)}')">§+</button>
    ${promoteSecBtn}
    ${delBtn}`;
}

function sectionHasArticleTotal(secArts) {
  return secArts.some(a => lineTotalCatalog(a) > 0);
}

/** Total section : Σ articles si montant détail > 0, sinon ratio macro × diviseur. */
function sectionDisplayTotal(chap, sec, secArts) {
  const detail = round2(secArts.reduce((s, a) => s + lineTotalCatalog(a), 0));
  if (sectionHasArticleTotal(secArts)) return detail;
  const st = sectionState[secUiKey(chap, sec)];
  if (st && st.use_macro && st.ratio_m2_override != null) {
    const ratio = parseFloat(st.ratio_m2_override);
    const divisor = parseFloat(st.qty) || (isPVChap(chap) ? kwc : sdo);
    if (ratio > 0 && divisor > 0) return round2(ratio * divisor);
  }
  return detail;
}

function round2(x) {
  return Number((Number(x) || 0).toFixed(2));
}

/** Champ désignation — même rendu que le texte catalogue (sans cadre type nombre). */
function estimDesigInput(value, extraAttrs, extraClass) {
  const cls = ['estim-desig-edit', extraClass || ''].filter(Boolean).join(' ');
  return `<input type="text" class="${cls}" ${extraAttrs || ''}
    value="${esc(value || '')}" placeholder="Désignation"
    title="Désignation" onclick="event.stopPropagation()">`;
}

const ESTIM_UNIT_OPTIONS = ['u', 'ens', 'ml', 'm²'];

function normalizeEstimUnit(u) {
  return String(u || 'u').toLowerCase().replace(/\u00b2/g, '2').replace(/\s/g, '');
}

function estimUnitSelect(value, dataAttrs) {
  const cur = normalizeEstimUnit(value);
  const opts = ESTIM_UNIT_OPTIONS.map(u => {
    const sel = normalizeEstimUnit(u) === cur ? ' selected' : '';
    return `<option value="${u}"${sel}>${u}</option>`;
  }).join('');
  return `<select class="estim-inp estim-unit-sel" ${dataAttrs} onclick="event.stopPropagation()">${opts}</select>`;
}

function money(n) {
  if (!n && n !== 0) return '—';
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency: 'EUR',
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(n);
}

/** Totaux estimation : affichage monétaire strict 2 décimales */
function moneyTot(n) {
  const v = round2(n);
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency: 'EUR',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(v);
}

function num(n, dec = 0) {
  if (!n && n !== 0) return '—';
  return new Intl.NumberFormat('fr-FR', {
    minimumFractionDigits: dec, maximumFractionDigits: dec,
  }).format(n);
}

function esc(s) {
  return String(s || '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function escJ(s) {
  return esc(s).replace(/'/g, "\\'");
}

function isPVChap(chap) {
  const c = (chap || '').toLowerCase();
  return c.includes('photov') || c.includes('pv');
}

function isChapterIncluded(chapter) {
  const st = chapterState[chapter];
  return !st || st.is_included !== false;
}

function isSectionIncluded(chap, sec) {
  if (!isChapterIncluded(chap)) return false;
  const st = sectionState[secUiKey(chap, sec)];
  if (!st) return true;
  return st.is_included !== false;
}

/** Unité catalogue strictement m² (propagation SDO) — pas hors catalogue */
function isCatalogM2Unit(unitRaw) {
  const u = String(unitRaw || '').toLowerCase().replace(/\u00b2/g, '2').replace(/\s/g, '');
  return u === 'm2';
}

function isEnsLikeUnit(unitRaw) {
  const u = String(unitRaw || '').toLowerCase().trim();
  return u === 'ens' || u === 'ensemble';
}

function applyLocalM2FromSdo() {
  if (typeof ESTIMATION_INITIALIZED !== 'undefined' && ESTIMATION_INITIALIZED) return;
  const val = round2(sdo);
  for (const r of localCatalog) {
    if (isCatalogM2Unit(r.unit)) r.quantity = val;
  }
}

function buildTree(rows) {
  const tree = {};
  for (const a of rows) {
    const ch = a.chapter;
    if (!tree[ch]) tree[ch] = {};
    if (!tree[ch][a.section]) tree[ch][a.section] = [];
    tree[ch][a.section].push(a);
  }
  return tree;
}

/** Sections locales sans article encore visible dans l'arbre. */
function mergeEmptySectionsIntoTree(tree) {
  for (const chap of CHAP_ORDER) {
    if (!tree[chap]) tree[chap] = {};
    for (const k of Object.keys(sectionState)) {
      if (!k.startsWith(chap + '|||')) continue;
      const sec = sectionState[k].section;
      if (sec == null || sec === '') continue;
      if (!tree[chap][sec]) tree[chap][sec] = [];
    }
  }
  return tree;
}

function filteredCatalog() {
  if (!searchQ) return localCatalog;
  const q = searchQ.toLowerCase();
  return localCatalog.filter(a =>
    (a.designation || '').toLowerCase().includes(q) ||
    (a.section || '').toLowerCase().includes(q) ||
    (a.chapter || '').toLowerCase().includes(q)
  );
}

function filteredCustom() {
  if (!searchQ) return localCustom;
  const q = searchQ.toLowerCase();
  return localCustom.filter(c =>
    (c.line_designation || '').toLowerCase().includes(q) ||
    'hors catalogue'.includes(q)
  );
}

function puEffectiveCatalog(r) {
  const ref = parseFloat(r.ref_pu_ht) || 0;
  const pu = r.unit_price_ht;
  if (pu === null || pu === undefined || pu === '') return ref;
  return parseFloat(pu) || 0;
}

function lineTotalCatalog(r) {
  const qty = parseFloat(r.quantity) || 0;
  return round2(qty * puEffectiveCatalog(r));
}

function lineTotalCustom(c) {
  const qty = parseFloat(c.quantity) || 0;
  const pu = parseFloat(c.unit_price_ht) || 0;
  return round2(qty * pu);
}

function recomputeTotals() {
  const t = { CFO: 0, CFA: 0, PV: 0 };
  const bySection = new Map();
  for (const r of localCatalog) {
    const sk = secUiKey(r.chapter, r.section);
    if (!bySection.has(sk)) bySection.set(sk, { chap: r.chapter, sec: r.section, lot: r.lot || 'CFO', arts: [] });
    bySection.get(sk).arts.push(r);
  }
  const doneSec = new Set();
  for (const r of localCatalog) {
    if (!isChapterIncluded(r.chapter)) continue;
    if (!isSectionIncluded(r.chapter, r.section)) continue;
    const sk = secUiKey(r.chapter, r.section);
    if (doneSec.has(sk)) continue;
    doneSec.add(sk);
    const bucket = bySection.get(sk);
    if (!bucket) continue;
    const lot = bucket.lot || 'CFO';
    const secTot = sectionDisplayTotal(bucket.chap, bucket.sec, bucket.arts);
    t[lot] = round2((t[lot] || 0) + secTot);
  }
  for (const c of localCustom) {
    let lot = (c.line_lot || 'CFO').toUpperCase();
    if (!['CFO', 'CFA', 'PV'].includes(lot)) lot = 'CFO';
    t[lot] = round2(t[lot] + lineTotalCustom(c));
  }
  t.CFO = round2(t.CFO || 0);
  t.CFA = round2(t.CFA || 0);
  t.PV = round2(t.PV || 0);
  t.ALL = round2(t.CFO + t.CFA + t.PV);
  return t;
}

function mergeRefPuFromRatiosApi(ratiosMap) {
  if (!ratiosMap || typeof ratiosMap !== 'object') return;
  for (const row of localCatalog) {
    const ent = ratiosMap[String(row.dpgf_id)];
    if (!ent) continue;
    const up = parseFloat(ent.unit_price);
    const ap = parseFloat(ent.avg_pu_actualise);
    const next = Number.isFinite(up) && up > 0 ? up : (Number.isFinite(ap) && ap > 0 ? ap : null);
    if (next != null) row.ref_pu_ht = round2(next);
  }
}

function fetchRatiosAndMerge() {
  const u = `/api/ratios?sdo=${encodeURIComponent(sdo)}&ccfo=${encodeURIComponent(ccfo)}&ccfa=${encodeURIComponent(ccfa)}&cpv=${encodeURIComponent(cpv)}`;
  return fetch(u)
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('ratios'))))
    .then(data => { mergeRefPuFromRatiosApi(data); });
}

function updateStatusbarCtx() {
  const el = document.getElementById('statusbar-ctx');
  const titleEl = document.getElementById('tbar-title');
  if (!el) return;
  const name = titleEl ? (titleEl.textContent || '').split('—')[0].trim() : '';
  el.textContent = `${name} · SDO ${round2(sdo)} m² · PV ${round2(kwc)} kWc`;
}

function scheduleParamsSave() {
  clearTimeout(paramsTimer);
  paramsTimer = setTimeout(flushParamsSave, 600);
}

function flushParamsSave() {
  const elSdo = document.getElementById('hdr-sdo');
  const elKwc = document.getElementById('hdr-kwc');
  const elPh = document.getElementById('hdr-phase');
  const elTp = document.getElementById('hdr-taux-phase');
  const elIncert = document.getElementById('hdr-taux-incertitude');
  const elRisque = document.getElementById('hdr-coef-risque');
  if (!elSdo || !elKwc || !elPh) return;

  const phase = elPh.value || 'APD';
  const tpRaw = elTp ? parseFloat(elTp.value) : NaN;
  const tp = round2(Number.isFinite(tpRaw) ? tpRaw : (PHASE_PRESETS[phase] ?? 3));
  tauxPhase = tp;

  const body = {
    surface_sdo: parseFloat(elSdo.value) || 0,
    puissance_pv_kwc: parseFloat(elKwc.value) || 0,
    phase_etude: phase,
    taux_phase: tp,
    taux_incertitude: elIncert ? (parseFloat(elIncert.value) || 0) : 3,
    coef_risque: elRisque ? (parseFloat(elRisque.value) || 0) : 1,
  };

  fetch(`/api/affaire/${AFFAIRE_ID}/params`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('params'))))
    .then((data) => {
      sdo = parseFloat(elSdo.value) || 0;
      kwc = parseFloat(elKwc.value) || 0;
      if (typeof ESTIMATION_INITIALIZED !== 'undefined' && ESTIMATION_INITIALIZED) {
        for (const st of Object.values(sectionState)) {
          if (!st.use_macro) continue;
          const chap = st.chapter;
          st.qty = round2(isPVChap(chap) ? kwc : sdo);
        }
        return data;
      }
      applyLocalM2FromSdo();
      return fetchRatiosAndMerge().catch(() => {}).then(() => data);
    })
    .then(() => {
      updateStatusbarCtx();
      render();
      updateKpiStrip(recomputeTotals());
      showFlash('Paramètres projet enregistrés');
    })
    .catch(() => showFlash('Erreur sauvegarde paramètres', true));
}

function syncPhaseSliderLabel() {
  const elTp = document.getElementById('hdr-taux-phase');
  const lbl = document.getElementById('hdr-taux-phase-lbl');
  if (!elTp || !lbl) return;
  const v = round2(parseFloat(elTp.value) || 0);
  lbl.textContent = `${num(v, 2)} %`;
}

function scheduleChapterSave(ch) {
  clearTimeout(chapSaveTimer);
  chapSaveTimer = setTimeout(() => flushChapterSave(ch), 600);
}

function scheduleSectionSave(chap, sec) {
  clearTimeout(secSaveTimer);
  secSaveTimer = setTimeout(() => flushSectionSave(chap, sec), 600);
}

function flushChapterSave(ch) {
  const st = chapterState[ch];
  if (!st) return;
  fetch(`/api/affaire/${AFFAIRE_ID}/chapter_settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      settings: [{
        chapter_key: st.chapter_key,
        is_included: !!st.is_included,
        use_macro: !!st.use_macro,
        qty: parseFloat(st.qty) || 1.0,
      }],
    }),
  })
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('chap'))))
    .then((data) => {
      if (data && data.totals) updateKpiStrip(data.totals);
      showFlash('Chapitre enregistré');
    })
    .catch(() => showFlash('Erreur enregistrement chapitre', true));
}

function flushSectionSave(chap, sec) {
  const st = ensureSectionState(chap, sec);
  const payload = {
    chapter_key: st.chapter_key,
    is_included: !!st.is_included,
    use_macro: !!st.use_macro,
    qty: parseFloat(st.qty) || 1.0,
  };
  if (st.ratio_m2_override != null) {
    payload.ratio_m2_override = parseFloat(st.ratio_m2_override);
  }
  if (st.is_local) payload.is_local = true;
  fetch(`/api/affaire/${AFFAIRE_ID}/chapter_settings`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ settings: [payload] }),
  })
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('sect'))))
    .then((data) => {
      if (data && data.totals) updateKpiStrip(data.totals);
      showFlash('Sous-chapitre enregistré');
    })
    .catch(() => showFlash('Erreur enregistrement sous-chapitre', true));
}

function onChapterIncludedChange(ch, checked) {
  if (!chapterState[ch]) return;
  chapterState[ch].is_included = !!checked;
  render();
  updateKpiStrip(recomputeTotals());
  scheduleChapterSave(ch);
}

function sectionMacroUnitLabel(chap) {
  return isPVChap(chap) ? '€/kWc' : '€/m²';
}

function onSectionMacroInput(chap, sec, field, rawVal, inputEl) {
  const st = ensureSectionState(chap, sec);
  const v = parseFloat(rawVal);
  if (!Number.isFinite(v) || v < 0) return;
  if (field === 'qty') {
    st.qty = round2(v);
  } else if (field === 'ratio') {
    st.ratio_m2_override = round2(v);
    st.use_macro = true;
  }
  const secArts = localCatalog.filter(a => a.chapter === chap && a.section === sec);
  const secTot = sectionDisplayTotal(chap, sec, secArts);
  const secInc = isSectionIncluded(chap, sec);
  if (inputEl) {
    const tr = inputEl.closest('tr.row-sec-macro');
    const totEl = tr && tr.querySelector('.sec-macro-total');
    if (totEl) totEl.textContent = moneyTot(secInc ? secTot : 0);
  }
  updateKpiStrip(recomputeTotals());
  scheduleSectionSave(chap, sec);
}

function onSectionIncludedChange(chap, sec, checked) {
  const st = ensureSectionState(chap, sec);
  st.is_included = !!checked;
  render();
  updateKpiStrip(recomputeTotals());
  scheduleSectionSave(chap, sec);
}

function chapterRatioChipHtml(chap, chapSumInc, chapVisuallyIncluded) {
  if (!chapVisuallyIncluded || chapSumInc <= 0) return '';
  if (isPVChap(chap)) {
    const d = kwc > 0 ? round2(chapSumInc / kwc) : null;
    return d != null && d > 0
      ? `<span class="chap-ratio-chip" title="Total chapitre / puissance PV">${num(d, 2)} €/kWc</span>`
      : '';
  }
  const d = sdo > 0 ? round2(chapSumInc / sdo) : null;
  return d != null && d >= 0
    ? `<span class="chap-ratio-chip" title="Total chapitre / SDO">${num(d, 2)} €/m²</span>`
    : '';
}

function setupSelectOnFocusOnce() {
  const root = document.getElementById('root');
  if (!root || root.dataset.selectOnFocus === '1') return;
  root.dataset.selectOnFocus = '1';
  root.addEventListener('focusin', (ev) => {
    const t = ev.target;
    if (!t || t.tagName !== 'INPUT') return;
    if (t.classList.contains('estim-inp')
        || (t.classList.contains('estim-hdr-inp') && t.type === 'number')) {
      t.select();
    }
  });
}

function updateKpiStrip(t) {
  const sdoVal = sdo > 0 ? sdo : 1;
  const kwcVal = kwc > 0 ? kwc : 1;
  document.getElementById('kpi-cfo').innerHTML = `${num(round2(t.CFO), 2)}<small>€ HT</small>`;
  document.getElementById('kpi-cfa').innerHTML = `${num(round2(t.CFA), 2)}<small>€ HT</small>`;
  document.getElementById('kpi-pv').innerHTML  = `${num(round2(t.PV), 2)}<small>€ HT</small>`;
  document.getElementById('kpi-total').innerHTML = `${num(round2(t.ALL), 2)}<small>€ HT</small>`;
  document.getElementById('kpi-cfo-m2').textContent = sdoVal > 0 ? `${num(round2(t.CFO / sdoVal), 2)} €/m²` : '—';
  document.getElementById('kpi-cfa-m2').textContent = sdoVal > 0 ? `${num(round2(t.CFA / sdoVal), 2)} €/m²` : '—';
  document.getElementById('kpi-pv-m2').textContent  = kwcVal > 0 ? `${num(round2(t.PV / kwcVal), 2)} €/kWc` : '—';
  document.getElementById('kpi-total-m2').textContent = sdoVal > 0 ? `${num(round2(t.ALL / sdoVal), 2)} €/m²` : '—';
}

function showFlash(msg, isErr) {
  const el = document.getElementById('flash-msg');
  if (!el) return;
  el.textContent = msg;
  el.style.display = 'block';
  el.classList.toggle('flash-err', !!isErr);
  clearTimeout(showFlash._t);
  showFlash._t = setTimeout(() => { el.style.display = 'none'; }, 2800);
}

function markDirtyCatalog(row) {
  if (row.is_tree_custom && row.line_id) {
    const key = `tree__${row.line_id}`;
    const pu = row.unit_price_ht;
    dirtyMap.set(key, {
      line_id: row.line_id,
      quantity: parseFloat(row.quantity) || 0,
      unit_price_ht: pu === '' || pu === undefined || pu === null ? 0 : parseFloat(pu),
      line_designation: row.designation || '',
      unit_override: row.unit || 'u',
      line_lot: row.lot || 'CFO',
    });
    schedSave();
    return;
  }
  const key = `cat__${row.dpgf_id}`;
  const pu = row.unit_price_ht;
  const payload = {
    dpgf_article_id: row.dpgf_id,
    quantity: parseFloat(row.quantity) || 0,
    unit_price_ht: pu === '' || pu === undefined ? null : pu,
    line_designation: (row.designation || '').trim(),
  };
  if (row.line_id) payload.line_id = row.line_id;
  dirtyMap.set(key, payload);
  schedSave();
}

function markDirtyCustom(row) {
  const key = `cust__${row.line_id}`;
  if (row._is_new && row.line_id < 0) {
    const real = tempLineToReal.get(row.line_id);
    if (real) {
      dirtyMap.set(key, {
        line_id: real,
        quantity: parseFloat(row.quantity) || 0,
        unit_price_ht: parseFloat(row.unit_price_ht) || 0,
        line_designation: row.line_designation || '',
        unit_override: row.unit_override || 'u',
        line_lot: row.line_lot || 'CFO',
      });
    } else {
      dirtyMap.set(key, {
        is_new_custom: true,
        temp_line_id: row.line_id,
        line_designation: row.line_designation || '',
        unit_override: row.unit_override || 'u',
        line_lot: row.line_lot || 'CFO',
        quantity: parseFloat(row.quantity) || 0,
        unit_price_ht: parseFloat(row.unit_price_ht) || 0,
      });
    }
  } else {
    dirtyMap.set(key, {
      line_id: row.line_id,
      quantity: parseFloat(row.quantity) || 0,
      unit_price_ht: parseFloat(row.unit_price_ht) || 0,
      line_designation: row.line_designation || '',
      unit_override: row.unit_override || 'u',
      line_lot: row.line_lot || 'CFO',
    });
  }
  schedSave();
}

function markDeleteCustom(lineId) {
  dirtyMap.set(`del__${lineId}`, { delete_custom: true, line_id: lineId });
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 10);
}

function schedSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 600);
}

function flushSave() {
  if (dirtyMap.size === 0) return;
  if (saveInFlight) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushSave, 150);
    return;
  }
  const changes = Array.from(dirtyMap.values());
  dirtyMap.clear();
  saveInFlight = true;

  fetch(`/api/affaire/${AFFAIRE_ID}/estimation/save`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ changes }),
  })
    .then(async r => {
      const text = await r.text();
      let data = {};
      try { data = JSON.parse(text); } catch (_) {}
      if (!r.ok) {
        showFlash(data.message || `Erreur ${r.status}`, true);
        return;
      }
      if (data.new_ids && data.new_ids.length) {
        for (const m of data.new_ids) {
          tempLineToReal.set(m.temp_line_id, m.line_id);
          const row = localCustom.find(c => c.line_id === m.temp_line_id);
          if (row) {
            row.line_id = m.line_id;
            row._is_new = false;
          }
        }
      }
      if (data.totals) updateKpiStrip(data.totals);
      showFlash(`Sauvegardé (${changes.length})`);
    })
    .catch(err => {
      console.error(err);
      showFlash('Erreur réseau — non sauvegardé', true);
    })
    .finally(() => {
      saveInFlight = false;
      if (dirtyMap.size > 0) flushSave();
    });
}

function setExpandAll(open) {
  if (open) {
    expandedChaps = new Set(CHAP_ORDER);
    expandedChaps.add(CUSTOM_CHAP);
    expandedSecs = new Set();
    for (const r of localCatalog) {
      expandedSecs.add(`${r.chapter}|||${r.section}`);
    }
    expandedCustom = true;
  } else {
    expandedChaps = new Set();
    expandedSecs = new Set();
    expandedCustom = false;
  }
  render();
}

function toggleChap(ev, chap) {
  const ch = typeof ev === 'string' ? ev : chap;
  const event = typeof ev === 'string' ? null : ev;
  if (event && event.target && event.target.closest) {
    if (event.target.closest('.estim-chap-cb-wrap') || event.target.closest('.estim-chap-cb')) return;
  }
  if (!ch) return;
  if (expandedChaps.has(ch)) expandedChaps.delete(ch);
  else expandedChaps.add(ch);
  render();
}

function toggleSec(ev, chap, sec) {
  const event = ev && typeof ev.preventDefault === 'function' ? ev : null;
  if (event && event.target && event.target.closest) {
    if (event.target.closest('.estim-sec-cb-wrap') || event.target.closest('.estim-sec-cb')) return;
  }
  const k = `${chap}|||${sec}`;
  if (expandedSecs.has(k)) expandedSecs.delete(k);
  else expandedSecs.add(k);
  render();
}

function toggleCustomBlock() {
  expandedCustom = !expandedCustom;
  render();
}

let secRenameTimer = null;

function scheduleSectionRename(chap, oldSec, newSec) {
  clearTimeout(secRenameTimer);
  secRenameTimer = setTimeout(() => {
    callEstimationLayout('rename_section', {
      chapter: chap,
      old_section: oldSec,
      new_section: newSec,
    }).then(data => {
      if (!data) return;
      const renamedSec = data.section || newSec;
      const kOld = secUiKey(chap, oldSec);
      const kNew = secUiKey(chap, renamedSec);
      const previousState = sectionState[kOld] || {
        chapter: chap,
        is_included: true,
        use_macro: true,
        qty: isPVChap(chap) ? kwc : sdo,
        ratio_m2_override: 0,
        is_local: true,
      };
      sectionState[kNew] = Object.assign({}, previousState, {
        chapter: chap,
        section: renamedSec,
        chapter_key: `sect:${chap}|${renamedSec}`,
        is_local: true,
      });
      delete sectionState[kOld];
      for (const key of Object.keys(sectionState)) {
        if (key !== kNew && sectionState[key].chapter === chap && sectionState[key].section === oldSec) {
          delete sectionState[key];
        }
      }
      if (sectionSortOrder[kOld] != null && sectionSortOrder[kNew] == null) {
        sectionSortOrder[kNew] = sectionSortOrder[kOld];
      }
      delete sectionSortOrder[kOld];
      for (const r of localCatalog) {
        if (r.chapter === chap && r.section === oldSec) r.section = renamedSec;
      }
      if (expandedSecs.has(kOld)) {
        expandedSecs.delete(kOld);
        expandedSecs.add(kNew);
      }
      showFlash('Sous-chapitre renommé');
      render();
      updateKpiStrip(recomputeTotals());
    });
  }, 600);
}

function onSectionNameInput(chap, oldSec, raw) {
  const name = (raw || '').trim();
  if (!name || name === oldSec) return;
  scheduleSectionRename(chap, oldSec, name);
}

function onCatalogInput(dpgfId, field, raw, elTotal, lineId) {
  let row = dpgfId ? localCatalog.find(x => x.dpgf_id === dpgfId) : null;
  if (!row && lineId) row = localCatalog.find(x => x.line_id === lineId);
  if (!row) return;
  if (field === 'desig') {
    row.designation = (raw || '').trim();
    if (!elTotal) {
      updateKpiStrip(recomputeTotals());
      markDirtyCatalog(row);
      return;
    }
  }
  if (field === 'qty') {
    row.quantity = raw === '' ? 0 : parseFloat(raw);
    if (Number.isNaN(row.quantity)) row.quantity = 0;
  }
  if (field === 'pu') {
    if (raw === '' || raw === null) row.unit_price_ht = null;
    else {
      const v = parseFloat(raw);
      row.unit_price_ht = Number.isNaN(v) ? null : v;
    }
  }
  if (field === 'unit') row.unit = (raw || 'u').trim();
  elTotal.textContent = moneyTot(lineTotalCatalog(row));
  updateKpiStrip(recomputeTotals());
  markDirtyCatalog(row);
}

function onCustomInput(lineId, field, raw, elTotal) {
  const row = localCustom.find(x => x.line_id === lineId);
  if (!row) return;
  if (field === 'desig') row.line_designation = raw;
  if (field === 'unit') row.unit_override = raw;
  if (field === 'lot') row.line_lot = raw;
  if (field === 'qty') {
    row.quantity = raw === '' ? 0 : parseFloat(raw);
    if (Number.isNaN(row.quantity)) row.quantity = 0;
  }
  if (field === 'pu') {
    row.unit_price_ht = raw === '' ? 0 : parseFloat(raw);
    if (Number.isNaN(row.unit_price_ht)) row.unit_price_ht = 0;
  }
  elTotal.textContent = moneyTot(lineTotalCustom(row));
  updateKpiStrip(recomputeTotals());
  markDirtyCustom(row);
}

function addCustomLine() {
  const nid = tempLineSeq--;
  localCustom.push({
    line_id: nid,
    line_designation: '',
    unit_override: 'u',
    line_lot: 'CFO',
    quantity: 0,
    unit_price_ht: 0,
    total_ht: 0,
    _is_new: true,
  });
  expandedChaps.add(CUSTOM_CHAP);
  expandedCustom = true;
  render();
  markDirtyCustom(localCustom[localCustom.length - 1]);
}

function deleteCustomLine(lineId) {
  if (!confirm('Supprimer cette ligne hors catalogue ?')) return;
  if (lineId < 0) {
    const idx = localCustom.findIndex(c => c.line_id === lineId);
    if (idx !== -1) localCustom.splice(idx, 1);
    dirtyMap.delete(`cust__${lineId}`);
    render();
    updateKpiStrip(recomputeTotals());
    return;
  }
  const idx = localCustom.findIndex(c => c.line_id === lineId);
  if (idx !== -1) localCustom.splice(idx, 1);
  render();
  markDeleteCustom(lineId);
}

function render() {
  const arts = filteredCatalog();
  const tree  = mergeEmptySectionsIntoTree(buildTree(arts));
  const custF = filteredCustom();
  const rows  = [];
  let visibleArt = 0;

  for (const chap of CHAP_ORDER) {
    if (!tree[chap]) continue;
    const cm = CHAP_META[chap] || {};
    const chapOpen = expandedChaps.has(chap);
    const allArts = Object.values(tree[chap]).flat();
    const chapInc = isChapterIncluded(chap);
    let chapSumRaw = 0;
    const chapSecsDone = new Set();
    for (const a of allArts) {
      if (!isSectionIncluded(chap, a.section)) continue;
      const sk = secUiKey(chap, a.section);
      if (chapSecsDone.has(sk)) continue;
      chapSecsDone.add(sk);
      const secArts = tree[chap][a.section] || [];
      chapSumRaw = round2(chapSumRaw + sectionDisplayTotal(chap, a.section, secArts));
    }
    const chapSumDisp = chapInc ? chapSumRaw : 0;
    const ratioChip = chapterRatioChipHtml(chap, chapSumRaw, chapInc);

    rows.push(`
      <tr class="row-chap ${chapInc ? '' : 'chap-excluded'}" onclick="window.__est.toggleChap(event,'${escJ(chap)}')">
        <td class="td-stripe ${cm.cls}"></td>
        <td colspan="5">
          <div class="chap-cell">
            <label class="estim-chap-cb-wrap" onclick="event.stopPropagation()">
              <input type="checkbox" class="estim-chap-cb" data-chap="${esc(chap)}" ${chapInc ? 'checked' : ''}
                title="Inclure ce chapitre dans les totaux et KPI">
            </label>
            <span class="chev ${chapOpen ? 'open' : ''}">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
            </span>
            <span class="chap-dot ${cm.cls}"></span>
            <span class="chap-label">${esc(chap)}</span>
            <span class="chap-meta">${allArts.length} art.</span>
            ${ratioChip}
          </div>
        </td>
        <td class="r chap-num-cell cell-est">${moneyTot(chapSumDisp)}</td>
        <td></td>
      </tr>`);

    if (!chapOpen && !searchQ) continue;

    for (const [sec, secArts] of sortedSectionEntries(chap, tree[chap])) {
      ensureSectionState(chap, sec);
      const secKey = chap + '|||' + sec;
      const secOpen = expandedSecs.has(secKey) || !!searchQ;
      const secSumRaw = sectionDisplayTotal(chap, sec, secArts);
      const secInc = isSectionIncluded(chap, sec);
      const secSumDisp = secInc ? secSumRaw : 0;
      const secRowClass = !chapInc ? 'chap-excluded' : (!secInc ? 'sec-excluded' : '');
      const stSec = ensureSectionState(chap, sec);
      const secMacro = stSec.is_local || (stSec.use_macro && stSec.ratio_m2_override != null);
      const secLabelHtml = stSec.is_local
        ? estimDesigInput(sec, `data-sec-name="1" data-chap="${esc(chap)}" data-sec="${esc(sec)}"`, 'sec-desig-edit')
        : `<span class="sec-label">${esc(sec)}</span>`;
      const secDivisor = parseFloat(stSec.qty) || (isPVChap(chap) ? kwc : sdo);
      const secRatioVal = secMacro ? parseFloat(stSec.ratio_m2_override) : 0;

      if (secMacro) {
        rows.push(`
        <tr class="row-sec row-sec-macro ${secRowClass}" onclick="window.__est.toggleSec(event,'${escJ(chap)}','${escJ(sec)}')">
          <td class="td-stripe ${cm.cls}"></td>
          <td>
            <div class="sec-cell">
              <label class="estim-sec-cb-wrap" onclick="event.stopPropagation()">
                <input type="checkbox" class="estim-sec-cb" data-chap="${esc(chap)}" data-sec="${esc(sec)}" ${secInc ? 'checked' : ''}
                  title="Inclure ce sous-chapitre dans les totaux et KPI">
              </label>
              <span class="chev ${secOpen ? 'open' : ''}">
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
              </span>
              ${secLabelHtml}
              <span class="sec-count">${secArts.length} art.</span>
              ${sectionActionButtons(chap, sec, secArts)}
            </div>
          </td>
          <td class="cell-ref r"><span class="estim-readonly sec-macro-unit">${sectionMacroUnitLabel(chap)}</span></td>
          <td class="cell-ref r"><span class="estim-readonly">—</span></td>
          <td class="cell-est r">
            <input type="number" class="estim-inp estim-sec-macro-inp" min="0" step="1"
              data-sec-field="qty" data-chap="${esc(chap)}" data-sec="${esc(sec)}"
              value="${esc(String(secDivisor))}" title="Surface ou puissance (diviseur)"
              onclick="event.stopPropagation()">
          </td>
          <td class="cell-est r">
            <input type="number" class="estim-inp estim-sec-macro-inp" min="0" step="0.01"
              data-sec-field="ratio" data-chap="${esc(chap)}" data-sec="${esc(sec)}"
              value="${esc(String(secRatioVal))}" title="Ratio €/m² ou €/kWc"
              onclick="event.stopPropagation()">
          </td>
          <td class="r sec-num-cell cell-est sec-macro-total">${moneyTot(secSumDisp)}</td>
          <td></td>
        </tr>`);
      } else {
        rows.push(`
        <tr class="row-sec ${secRowClass}" onclick="window.__est.toggleSec(event,'${escJ(chap)}','${escJ(sec)}')">
          <td class="td-stripe ${cm.cls}"></td>
          <td colspan="5">
            <div class="sec-cell">
              <label class="estim-sec-cb-wrap" onclick="event.stopPropagation()">
                <input type="checkbox" class="estim-sec-cb" data-chap="${esc(chap)}" data-sec="${esc(sec)}" ${secInc ? 'checked' : ''}
                  title="Inclure ce sous-chapitre dans les totaux et KPI">
              </label>
              <span class="chev ${secOpen ? 'open' : ''}">
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
              </span>
              ${secLabelHtml}
              <span class="sec-count">${secArts.length} art.</span>
              ${sectionActionButtons(chap, sec, secArts)}
            </div>
          </td>
          <td class="r sec-num-cell cell-est">${moneyTot(secSumDisp)}</td>
          <td></td>
        </tr>`);
      }

      if (!secOpen) continue;

      for (const a of secArts) {
        visibleArt++;
        const isTree = !!a.is_tree_custom;
        const refPu = parseFloat(a.ref_pu_ht) || 0;
        const qtyParsed = parseFloat(a.quantity);
        const qtyVal = Number.isFinite(qtyParsed) ? qtyParsed : 0;
        const puDisp = a.unit_price_ht !== null && a.unit_price_ht !== undefined && a.unit_price_ht !== ''
          ? a.unit_price_ht : '';
        const tot = lineTotalCatalog(a);
        const ratioTag = a.ratio_type === 'SURFACIQUE'
          ? '<span class="ratio-tag ratio-surf">S</span>'
          : '<span class="ratio-tag ratio-unit">U</span>';
        const ut = String(a.unit || '').toLowerCase().replace(/\u00b2/g, '2').replace(/\s/g, '');
        const lineInc = chapInc && secInc;
        const lineM2Ratio = lineInc && !isEnsLikeUnit(a.unit) && ut === 'm2' && qtyVal > 0 && tot > 0
          ? ` <span class="line-unit-ratio" title="PU implicite ligne">${num(round2(tot / qtyVal), 2)} €/m²</span>`
          : '';

        const totId = isTree ? `tot-ln-${a.line_id}` : `tot-${a.dpgf_id}`;
        const dataIdAttr = isTree
          ? `data-line-id="${a.line_id}" data-tree-custom="1"`
          : `data-dpgf-id="${a.dpgf_id}"`;
        const unitCell = isTree
          ? estimUnitSelect(a.unit || 'u', `data-field="unit" data-tree-custom="1" data-line-id="${a.line_id}"`)
          : `<span class="estim-readonly">${esc(a.unit || '—')}</span>`;
        const delTitle = isTree ? 'Supprimer cet article' : 'Retirer cet article de l\'estimation';
        rows.push(`
          <tr class="row-art ${lineInc ? '' : 'row-art-muted'}" ${dataIdAttr}>
            <td class="td-stripe ${cm.cls}"></td>
            <td class="art-desig-cell">${
              estimDesigInput(
                a.designation,
                `data-field="desig"${a.line_id ? ` data-line-id="${a.line_id}"` : ''} ${isTree ? 'data-tree-custom="1"' : `data-dpgf-id="${a.dpgf_id}"`}`,
                'art-desig-edit',
              )
            }${lineM2Ratio} ${ratioTag}${isTree ? ' <span class="ratio-tag ratio-unit">A</span>' : ''}</td>
            <td class="cell-ref r">${unitCell}</td>
            <td class="cell-ref r"><span class="estim-readonly">${refPu ? money(refPu) : '—'}</span></td>
            <td class="cell-est r">
              <input type="number" class="estim-inp" min="0" step="0.01" data-field="qty" ${dataIdAttr}
                value="${esc(String(qtyVal))}" placeholder="0" title="Quantité">
            </td>
            <td class="cell-est r">
              <input type="number" class="estim-inp" min="0" step="0.01" data-field="pu" ${dataIdAttr}
                value="${puDisp !== '' ? esc(String(puDisp)) : ''}" placeholder="${refPu ? esc(String(refPu)) : '0'}" title="Vide = PU référentiel">
            </td>
            <td class="cell-est r cell-total-est" id="${totId}">${moneyTot(tot)}</td>
            <td class="cell-act">
              ${isTree ? `<button type="button" class="estim-promote-btn" title="Ajouter à la base de prix"
                onclick="event.stopPropagation();window.__est.promoteArticle(${a.line_id},'${escJ(a.designation || '')}')">📖</button>` : ''}
              <button type="button" class="estim-move-btn" title="Monter"
                onclick="event.stopPropagation();window.__est.moveArticle('${escJ(chap)}','${escJ(sec)}','up',${a.dpgf_id || 'null'},${a.line_id || 'null'})">▲</button>
              <button type="button" class="estim-move-btn" title="Descendre"
                onclick="event.stopPropagation();window.__est.moveArticle('${escJ(chap)}','${escJ(sec)}','down',${a.dpgf_id || 'null'},${a.line_id || 'null'})">▼</button>
              <button type="button" class="trash-btn" title="${delTitle}"
                onclick="event.stopPropagation();window.__est.deleteArticle('${escJ(chap)}','${escJ(sec)}',${a.dpgf_id || 'null'},${a.line_id || 'null'},${isTree ? 1 : 0})">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 4h10M6 4V3h4v1M5 4v8a1 1 0 001 1h4a1 1 0 001-1V4"/><path d="M7 7v4M9 7v4"/></svg>
              </button>
            </td>
          </tr>`);
      }
    }
  }

  /* Hors catalogue : affiché seulement si des lignes existent (pas de création via UI) */
  if (custF.length > 0) {
  const cOpen = expandedChaps.has(CUSTOM_CHAP);
  const cSum = round2(custF.reduce((s, c) => s + lineTotalCustom(c), 0));
  rows.push(`
    <tr class="row-chap row-chap-custom" onclick="window.__est.toggleChap(event,'${escJ(CUSTOM_CHAP)}')">
      <td class="td-stripe s-cfo"></td>
      <td colspan="5">
        <div class="chap-cell">
          <span class="chev ${cOpen ? 'open' : ''}">
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
          </span>
          <span class="chap-label">Hors catalogue</span>
          <span class="chap-meta">${custF.length} ligne(s)</span>
        </div>
      </td>
      <td class="r chap-num-cell cell-est">${moneyTot(cSum)}</td>
      <td></td>
    </tr>`);

  if (cOpen) {
    const secOpen = expandedCustom || !!searchQ;
    rows.push(`
      <tr class="row-sec" onclick="window.__est.toggleCustom()">
        <td class="td-stripe s-cfo"></td>
        <td colspan="5">
          <div class="sec-cell">
            <span class="chev ${secOpen ? 'open' : ''}">
              <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
            </span>
            <span class="sec-label">Lignes sur mesure</span>
          </div>
        </td>
        <td class="r sec-num-cell cell-est">${moneyTot(cSum)}</td>
        <td></td>
      </tr>`);

    if (secOpen) {
      for (const c of custF) {
        visibleArt++;
        const tot = lineTotalCustom(c);
        rows.push(`
          <tr class="row-art row-art-custom" data-line-id="${c.line_id}">
            <td class="td-stripe s-cfo"></td>
            <td class="cell-est">
              ${estimDesigInput(c.line_designation, `data-field="desig" data-line-id="${c.line_id}"`, 'art-desig-edit')}
            </td>
            <td class="cell-ref r"><span class="estim-readonly">—</span></td>
            <td class="cell-ref r"><span class="estim-readonly">—</span></td>
            <td class="cell-est r">
              <input type="number" class="estim-inp" min="0" step="0.01" data-field="qty" data-line-id="${c.line_id}"
                value="${c.quantity !== undefined && c.quantity !== null ? esc(String(c.quantity)) : ''}">
            </td>
            <td class="cell-est r">
              <input type="number" class="estim-inp" min="0" step="0.01" data-field="pu" data-line-id="${c.line_id}"
                value="${c.unit_price_ht ? esc(String(c.unit_price_ht)) : ''}">
            </td>
            <td class="cell-est r cell-total-est" id="ctot-${c.line_id}">${moneyTot(tot)}</td>
            <td class="cell-act">
              <button type="button" class="tbl-btn" title="Supprimer" data-del-custom="${c.line_id}">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 4h10M6 4V3h4v1M5 4v8a1 1 0 001 1h4a1 1 0 001-1V4"/><path d="M7 7v4M9 7v4"/></svg>
              </button>
            </td>
          </tr>
          <tr class="row-art-sub" data-line-id="${c.line_id}">
            <td></td>
            <td class="cell-est" colspan="3">
              <span class="estim-inline-lbl">Unité</span>
              <input type="text" class="estim-inp estim-inp-narrow" data-field="unit" data-line-id="${c.line_id}"
                value="${esc(c.unit_override || '')}" placeholder="u">
              <span class="estim-inline-lbl">Lot</span>
              <select class="estim-sel" data-field="lot" data-line-id="${c.line_id}">
                <option value="CFO" ${(c.line_lot || 'CFO').toUpperCase() === 'CFO' ? 'selected' : ''}>CFO</option>
                <option value="CFA" ${(c.line_lot || '').toUpperCase() === 'CFA' ? 'selected' : ''}>CFA</option>
                <option value="PV" ${(c.line_lot || '').toUpperCase() === 'PV' ? 'selected' : ''}>PV</option>
              </select>
            </td>
            <td colspan="4"></td>
          </tr>`);
      }
    }
  }
  }

  const tbody = document.getElementById('tree-body');
  if (rows.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8"><div class="empty-state"><div class="empty-ico">🔍</div><div>Aucun résultat</div></div></td></tr>';
  } else {
    tbody.innerHTML = rows.join('');
  }

  document.getElementById('art-count').textContent = `${visibleArt} lignes`;

  tbody.querySelectorAll('.estim-chap-cb').forEach(cb => {
    cb.addEventListener('change', (e) => {
      e.stopPropagation();
      onChapterIncludedChange(cb.getAttribute('data-chap'), cb.checked);
    });
  });

  tbody.querySelectorAll('.estim-sec-cb').forEach(cb => {
    cb.addEventListener('change', (e) => {
      e.stopPropagation();
      onSectionIncludedChange(cb.getAttribute('data-chap'), cb.getAttribute('data-sec'), cb.checked);
    });
  });

  tbody.querySelectorAll('input[data-sec-field]').forEach(inp => {
    const handler = () => {
      onSectionMacroInput(
        inp.getAttribute('data-chap'),
        inp.getAttribute('data-sec'),
        inp.getAttribute('data-sec-field'),
        inp.value,
        inp,
      );
    };
    inp.addEventListener('input', handler);
    inp.addEventListener('change', handler);
  });

  tbody.querySelectorAll('input[data-field="desig"]').forEach(inp => {
    inp.addEventListener('input', () => {
      const dpgfId = inp.dataset.dpgfId ? parseInt(inp.dataset.dpgfId, 10) : null;
      const lineId = inp.dataset.lineId ? parseInt(inp.dataset.lineId, 10) : null;
      const totEl = dpgfId
        ? document.getElementById(`tot-${dpgfId}`)
        : (lineId ? document.getElementById(`tot-ln-${lineId}`) : null);
      onCatalogInput(dpgfId, 'desig', inp.value, totEl, lineId);
    });
  });

  tbody.querySelectorAll('input[data-dpgf-id]:not([data-field="desig"]), input[data-tree-custom]:not([data-field="desig"]), select[data-tree-custom]').forEach(inp => {
    const handler = () => {
      const dpgfId = inp.dataset.dpgfId ? parseInt(inp.dataset.dpgfId, 10) : null;
      const lineId = inp.dataset.lineId ? parseInt(inp.dataset.lineId, 10) : null;
      const totEl = dpgfId
        ? document.getElementById(`tot-${dpgfId}`)
        : document.getElementById(`tot-ln-${lineId}`);
      onCatalogInput(dpgfId, inp.dataset.field, inp.value, totEl, lineId);
    };
    inp.addEventListener('input', handler);
    inp.addEventListener('change', handler);
  });

  tbody.querySelectorAll('input[data-sec-name]').forEach(inp => {
    inp.addEventListener('change', () => {
      onSectionNameInput(
        inp.getAttribute('data-chap'),
        inp.getAttribute('data-sec'),
        inp.value,
      );
    });
  });

  tbody.querySelectorAll('input[data-line-id]:not([data-tree-custom]), select[data-line-id]').forEach(inp => {
    inp.addEventListener('input', () => syncCustom(inp));
    inp.addEventListener('change', () => syncCustom(inp));
  });

  tbody.querySelectorAll('button[data-del-custom]').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      deleteCustomLine(parseInt(btn.dataset.delCustom, 10));
    });
  });

  function syncCustom(inp) {
    const lid = parseInt(inp.dataset.lineId, 10);
    const totEl = document.getElementById(`ctot-${lid}`);
    onCustomInput(lid, inp.dataset.field, inp.value, totEl);
  }
}

window.__est = {
  toggleChap,
  toggleSec,
  toggleCustom: toggleCustomBlock,
  onChapterIncludedChange,
  onSectionIncludedChange,
  addSection: addEstimationSection,
  addArticle: addEstimationArticle,
  deleteSection: deleteEstimationSection,
  deleteArticle: deleteEstimationArticle,
  moveSection: moveEstimationSection,
  moveArticle: moveEstimationArticle,
  promoteSection: promoteEstimationSection,
  promoteArticle: promoteEstimationArticle,
};

document.addEventListener('DOMContentLoaded', () => {
  restoreExpandStateAfterReload();
  const elTpInit = document.getElementById('hdr-taux-phase');
  if (elTpInit && typeof INIT_TAUX_PHASE === 'number') {
    elTpInit.value = String(INIT_TAUX_PHASE);
    tauxPhase = round2(INIT_TAUX_PHASE);
  }
  syncPhaseSliderLabel();
  render();
  updateKpiStrip(recomputeTotals());

  const hdrSdo = document.getElementById('hdr-sdo');
  const hdrKwc = document.getElementById('hdr-kwc');
  const hdrPhase = document.getElementById('hdr-phase');
  const hdrTaux = document.getElementById('hdr-taux-phase');
  const hdrIncert = document.getElementById('hdr-taux-incertitude');
  const hdrRisque = document.getElementById('hdr-coef-risque');
  setupSelectOnFocusOnce();
  if (hdrSdo && hdrKwc && hdrPhase) {
    ['input', 'change'].forEach(ev => {
      hdrSdo.addEventListener(ev, scheduleParamsSave);
      hdrKwc.addEventListener(ev, scheduleParamsSave);
      if (hdrIncert) hdrIncert.addEventListener(ev, scheduleParamsSave);
      if (hdrRisque) hdrRisque.addEventListener(ev, scheduleParamsSave);
    });
    hdrPhase.addEventListener('change', () => {
      const ph = hdrPhase.value;
      const preset = PHASE_PRESETS[ph] ?? 3;
      if (hdrTaux) hdrTaux.value = String(preset);
      syncPhaseSliderLabel();
      scheduleParamsSave();
    });
    if (hdrTaux) {
      hdrTaux.addEventListener('input', () => {
        syncPhaseSliderLabel();
        scheduleParamsSave();
      });
    }
  }

  document.getElementById('search-inp').addEventListener('input', e => {
    searchQ = e.target.value.trim();
    render();
  });

  const btnAddCustom = document.getElementById('btn-add-custom');
  if (btnAddCustom) btnAddCustom.addEventListener('click', addCustomLine);

  document.getElementById('btn-export-xlsx').addEventListener('click', () => {
    alert('Export Excel — fonctionnalité à venir (placeholder).');
  });
});
