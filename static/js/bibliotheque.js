/**
 * bibliotheque.js — Sprint 9.2
 * Corrections : CFA (casse), ratio €/m² section, suppression articles,
 * validation inputs (bordure rouge + scroll), debounce save 800 ms.
 *
 * Dépend de ARTICLES, SEC_RATIOS, AFFAIRE_ID, INIT_SDO injectés par Jinja.
 */

/* ── CONSTANTES ─────────────────────────────────────────────────────────── */
// Noms exacts tels qu'en BDD (casse respectée)
const CHAP_META = {
  'Courants Forts':  { cls: 's-cfo', banner: 'cfo' },
  'Courants faibles':{ cls: 's-cfa', banner: 'cfa' },   // f minuscule en BDD
  'Photovoltaïque':  { cls: 's-pv',  banner: 'pv'  },
};
const CHAP_ORDER = ['Courants Forts', 'Courants faibles', 'Photovoltaïque'];

/* ── STATE ───────────────────────────────────────────────────────────────── */
let sdo           = INIT_SDO;
let kwc           = INIT_KWC;   // puissance PV cible (kWc) — diviseur ratio sections PV
let searchQ       = '';
let expandedChaps = new Set(CHAP_ORDER);
let expandedSecs  = new Set();
let selectedId    = null;

// Copie mutable (+ nouveaux articles en session avec id < 0) — let : deleteSection réassigne après filtre
let localData  = ARTICLES.map(a => Object.assign({}, a));
let   _tempIdSeq = -1;

// Ratios de section : clé = "chapter|||section", valeur = { ratio, unit, manual }
// Le backend retourne {ratio, unit} depuis Sprint 9.3 (compat float pour anciens enregistrements)
const sectionRatios = {};
for (const [key, val] of Object.entries(SEC_RATIOS || {})) {
  if (val && typeof val === 'object') {
    sectionRatios[key] = { ratio: val.ratio, unit: val.unit || 'm2', manual: true };
  } else {
    sectionRatios[key] = { ratio: val, unit: 'm2', manual: true };
  }
}

// File de modifications à persister
const dirtyMap = new Map();
let   saveTimer = null;

/* ── FORMATAGE ───────────────────────────────────────────────────────────── */
function money(n) {
  if (!n && n !== 0) return '—';
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency: 'EUR',
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  }).format(n);
}
function moneyDec(n) {
  if (!n && n !== 0) return '—';
  return new Intl.NumberFormat('fr-FR', {
    style: 'currency', currency: 'EUR', minimumFractionDigits: 2,
  }).format(n);
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
// Échappe pour injection dans un onclick JS (apostrophes → \' pour ne pas fermer la chaîne)
function escJ(s) {
  return esc(s).replace(/'/g, "\\'");
}

/* ── CALCULS ──────────────────────────────────────────────────────────────── */
function isPVChap(chap) {
  const c = (chap || '').toLowerCase();
  return c.includes('photov') || c.includes('pv');
}

function calcTotal(art) {
  const pu  = art.pu_ht   || 0;
  const qty = art.quantity || 0;
  if (!pu) return 0;
  if (art.ratio_type === 'SURFACIQUE') {
    // PV → diviseur kWc ; CFO/CFA → diviseur SDO
    const divisor = isPVChap(art.chapter) ? (kwc || 1) : (sdo || 1);
    return qty > 0 ? qty * pu : pu * divisor;
  }
  return qty > 0 ? qty * pu : 0;
}

/**
 * Ratio effectif d'une section.
 * PV → diviseur = kwc (kWc), unité = 'kwc'.
 * Autres → diviseur = sdo (m²), unité = 'm2'.
 */
function calcSectionRatio(secKey, secArts, chap) {
  if (sectionRatios[secKey] !== undefined) {
    return {
      ratio:  sectionRatios[secKey].ratio,
      unit:   sectionRatios[secKey].unit || 'm2',
      manual: sectionRatios[secKey].manual,
    };
  }
  const total  = secArts.reduce((s, a) => s + calcTotal(a), 0);
  const pv     = isPVChap(chap);
  const divisor = pv ? (kwc || 1) : (sdo || 1);
  const ratio   = divisor > 0 ? total / divisor : 0;
  return { ratio, unit: pv ? 'kwc' : 'm2', manual: false };
}

/* ── ARBRE ────────────────────────────────────────────────────────────────── */
function buildTree(articles) {
  const tree = {};
  for (const a of articles) {
    if (!tree[a.chapter]) tree[a.chapter] = {};
    if (!tree[a.chapter][a.section]) tree[a.chapter][a.section] = [];
    tree[a.chapter][a.section].push(a);
  }
  return tree;
}

function filteredArticles() {
  if (!searchQ) return localData;
  const q = searchQ.toLowerCase();
  return localData.filter(a =>
    (a.designation || '').toLowerCase().includes(q) ||
    (a.section     || '').toLowerCase().includes(q) ||
    (a.chapter     || '').toLowerCase().includes(q)
  );
}

/* ── PERSISTANCE (debounce 800 ms) ──────────────────────────────────────── */
function markDirty(artId, field, value, extra = {}) {
  const key = `${artId}__${field}`;
  dirtyMap.set(key, { id: artId, field, value, ...extra });
  schedSave();
}

function markNew(art) {
  const key = `new__${art.id}`;
  dirtyMap.set(key, {
    is_new: true,
    chapter:     art.chapter,
    section:     art.section,
    designation: art.designation,
    unit:        art.unit,
    qty_ref:     art.quantity || 0,
    pu_ht:       art.pu_ht   || 0,
  });
  schedSave();
}

function markSectionRatio(chapter, section, ratio, ratioUnit) {
  const key = `secRatio__${chapter}|||${section}`;
  dirtyMap.set(key, { id: null, field: 'section_ratio', chapter, section, value: ratio, ratio_unit: ratioUnit });
  schedSave();
}

function schedSave() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(flushSave, 800);
}

function flushSave() {
  if (dirtyMap.size === 0) return;
  const changes = Array.from(dirtyMap.values());
  dirtyMap.clear();

  const payload = JSON.stringify({ changes });
  console.debug('[bibliotheque] flushSave → payload', payload.length, 'octets', JSON.parse(payload));

  fetch('/api/bibliotheque/save', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    payload,
  })
  .then(async r => {
    const text = await r.text();
    console.debug('[bibliotheque] réponse', r.status, r.url, text.slice(0, 400));
    let data = {};
    try { data = JSON.parse(text); } catch(_) { /* réponse non-JSON */ }

    if (!r.ok) {
      console.error('[bibliotheque] ERREUR SAVE', r.status, data);
      showFlash(
        `Erreur ${r.status} — ${data.message || text.slice(0,120) || 'serveur'} (voir logs/app_errors.log)`,
        true
      );
      return;
    }
    if (data.status === 'ok') {
      if (data.new_ids && data.new_ids.length) {
        let ni = 0;
        for (const art of localData) {
          if (art.id < 0 && ni < data.new_ids.length) art.id = data.new_ids[ni++];
        }
      }
      showFlash(`Sauvegardé (${changes.length} modif.)`);
    } else {
      console.error('[bibliotheque] status KO', data);
      showFlash(`Erreur ${data.code || ''} — ${data.message || 'inconnue'} (voir logs/app_errors.log)`, true);
    }
  })
  .catch(err => {
    console.error('[bibliotheque] fetch FAILED', err);
    showFlash('Erreur réseau — modifications non sauvegardées. Vérifier logs/app_errors.log', true);
  });
}

/* ── RENDU ────────────────────────────────────────────────────────────────── */
function render() {
  const arts  = filteredArticles();
  const tree  = buildTree(arts);
  const tbody = document.getElementById('tree-body');
  const rows  = [];
  let visibleArt = 0;

  for (const chap of CHAP_ORDER) {
    if (!tree[chap]) continue;
    const cm       = CHAP_META[chap] || {};
    const chapOpen = expandedChaps.has(chap);

    // Chapitre total = Σ sections (section-first, respecte les ratios manuels tous lots)
    let chapTotal = 0;
    for (const [sec, secArts] of Object.entries(tree[chap])) {
      const secKey = chap + '|||' + sec;
      const { ratio: sRatio, unit: sUnit, manual: sManual } = calcSectionRatio(secKey, secArts, chap);
      const sDivisor = sUnit === 'kwc' ? (kwc || 0) : (sdo || 0);
      chapTotal += sManual
        ? sRatio * sDivisor
        : secArts.reduce((s, a) => s + calcTotal(a), 0);
    }
    const allArts = Object.values(tree[chap]).flat();

    rows.push(`
      <tr class="row-chap" onclick="toggleChap('${escJ(chap)}')">
        <td class="td-stripe ${cm.cls}"></td>
        <td>
          <div class="chap-cell">
            <span class="chev ${chapOpen ? 'open' : ''}">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
            </span>
            <span class="chap-dot ${cm.cls}"></span>
            <span class="chap-label">${esc(chap)}</span>
            <span class="chap-meta">${allArts.length} art. · ${Object.keys(tree[chap]).length} sections</span>
            <button class="add-row-btn" title="Ajouter un article"
              onclick="event.stopPropagation();addArticle('${escJ(chap)}','')">+</button>
          </div>
        </td>
        <td></td><td></td>
        <td class="chap-ratio-cell">
          ${isPVChap(chap)
            ? (kwc > 0 && chapTotal > 0 ? num(chapTotal / kwc, 0) + ' €/kWc' : '')
            : (sdo > 0 && chapTotal > 0 ? num(chapTotal / sdo, 0) + ' €/m²' : '')}
        </td>
        <td class="chap-num-cell">${chapTotal > 0 ? money(chapTotal) : '—'}</td>
        <td></td>
      </tr>`);

    if (!chapOpen && !searchQ) continue;

    for (const [sec, secArts] of Object.entries(tree[chap])) {
      const secKey    = chap + '|||' + sec;
      const secOpen   = expandedSecs.has(secKey) || !!searchQ;
      const { ratio: secRatio, unit: ratioUnit, manual: ratioManual } = calcSectionRatio(secKey, secArts, chap);
      // Ratio manuel → total piloté par le ratio × diviseur (tous lots, pas seulement PV)
      const secDivisor = ratioUnit === 'kwc' ? (kwc || 0) : (sdo || 0);
      const secTotal   = ratioManual
        ? secRatio * secDivisor
        : secArts.reduce((s, a) => s + calcTotal(a), 0);
      const ratioLbl  = ratioUnit === 'kwc' ? '€/kWc' : '€/m²';
      const ratioTip  = ratioUnit === 'kwc'
        ? 'Ratio €/kWc installé (PV) — cliquer pour modifier'
        : 'Ratio €/m² SDO — cliquer pour modifier';

      rows.push(`
        <tr class="row-sec" onclick="toggleSec('${escJ(chap)}','${escJ(sec)}')">
          <td class="td-stripe ${cm.cls}"></td>
          <td>
            <div class="sec-cell">
              <span class="chev ${secOpen ? 'open' : ''}">
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 4l4 4-4 4"/></svg>
              </span>
              <span class="sec-label" title="Double-cliquer pour renommer"
                onclick="event.stopPropagation();startSecNameEdit(this,'${escJ(chap)}','${escJ(sec)}')">${esc(sec)}</span>
              <span class="sec-count">${secArts.length} art.</span>
              <button class="add-row-btn" title="Ajouter un article dans cette section"
                onclick="event.stopPropagation();addArticle('${escJ(chap)}','${escJ(sec)}')">+</button>
              <button class="add-sec-btn" title="Nouvelle section après celle-ci"
                onclick="event.stopPropagation();addSectionAfter('${escJ(chap)}','${escJ(sec)}')">§+</button>
              <button class="sec-del-btn" title="Supprimer cette section et tous ses articles"
                onclick="event.stopPropagation();deleteSection('${escJ(chap)}','${escJ(sec)}')">
                <svg width="11" height="11" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M3 4h10M6 4V3h4v1M5 4v8a1 1 0 001 1h4a1 1 0 001-1V4"/><path d="M7 7v4M9 7v4"/></svg>
              </button>
            </div>
          </td>
          <td></td><td></td>
          <td class="sec-ratio-cell ${ratioManual ? 'manual' : ''}"
              data-chap="${esc(chap)}" data-sec="${esc(sec)}"
              onclick="event.stopPropagation();startSecRatioEdit(this,'${escJ(chap)}','${escJ(sec)}',${secRatio.toFixed(4)},'${ratioUnit}')"
              title="${ratioTip}">
            <span class="sec-ratio-lbl">${ratioLbl}</span>${secRatio > 0 ? num(secRatio, 1) : '—'}
          </td>
          <td class="sec-num-cell">${secTotal > 0 ? money(secTotal) : '—'}</td>
          <td></td>
        </tr>`);

      if (!secOpen) continue;

      for (const a of secArts) {
        visibleArt++;
        const pu    = a.pu_ht   || 0;
        const qty   = a.quantity || 0;
        const total = calcTotal(a);
        const isSel = selectedId === a.id;
        const isNew = a.id < 0;
        const manualCls = a._manual ? 'cell-manual' : '';

        let qtyDisplay = '—';
        if (a.ratio_type === 'SURFACIQUE') {
          qtyDisplay = qty > 0 ? num(qty, 0) : `×${num(sdo, 0)} m²`;
        } else if (qty > 0) {
          qtyDisplay = num(qty, 0);
        }

        rows.push(`
          <tr class="row-art ${isSel ? 'selected' : ''}" data-id="${a.id}">
            <td class="td-stripe ${cm.cls}"></td>
            <td class="art-desig-cell editable ${manualCls}" data-id="${a.id}" data-field="designation"
                onclick="startEdit(this,event)">
              ${esc(a.designation)}
              <span class="ratio-tag ${a.ratio_type === 'SURFACIQUE' ? 'ratio-surf' : 'ratio-unit'}">${a.ratio_type === 'SURFACIQUE' ? 'S' : 'U'}</span>
              ${a.lot ? `<span class="lot-tag lot-${a.lot}">${a.lot}</span>` : ''}
              ${isNew ? '<span class="custom-tag">NEW</span>' : (a.is_custom ? '<span class="custom-tag">CUSTOM</span>' : '')}
            </td>
            <td class="art-unit-cell editable ${manualCls}" data-id="${a.id}" data-field="unit"
                onclick="startEdit(this,event)">
              <span class="unit-tag">${esc(a.unit)}</span>
            </td>
            <td class="art-qty-cell editable ${manualCls}" data-id="${a.id}" data-field="qty_ref"
                onclick="startEdit(this,event)">
              <span class="${a.ratio_type === 'SURFACIQUE' ? 'qty-surf' : (qty > 0 ? 'qty-fix' : 'qty-none')}">${esc(qtyDisplay)}</span>
            </td>
            <td class="art-pu-cell editable ${manualCls}" data-id="${a.id}" data-field="pu_ht"
                onclick="startEdit(this,event)">
              ${pu ? moneyDec(pu) : '<span class="val-empty">—</span>'}
            </td>
            <td class="art-total-cell" id="art-total-${a.id}">
              ${total > 0 ? money(total) : '<span class="val-empty">—</span>'}
            </td>
            <td class="art-action-cell">
              <button class="tbl-btn" onclick="selectArt(${a.id})" title="Inspecter">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M11 2l3 3-8 8H3v-3l8-8z"/></svg>
              </button>
              <button class="dup-btn" onclick="event.stopPropagation();duplicateArticle(${a.id})" title="Dupliquer cet article juste en dessous">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><rect x="5" y="5" width="8" height="8" rx="1.5"/><path d="M3 11V3h8"/></svg>
              </button>
              <button class="trash-btn" onclick="event.stopPropagation();deleteArticle(${a.id},${a.is_custom ? 1 : 0})" title="${a.is_custom ? 'Supprimer définitivement' : 'Masquer de la bibliothèque'}">
                <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M3 4h10M6 4V3h4v1M5 4v8a1 1 0 001 1h4a1 1 0 001-1V4"/><path d="M7 7v4M9 7v4"/></svg>
              </button>
            </td>
          </tr>`);
      }
    }
  }

  if (rows.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7"><div class="empty-state"><div class="empty-ico">🔍</div><div>Aucun article ne correspond à "${esc(searchQ)}"</div></div></td></tr>`;
  } else {
    tbody.innerHTML = rows.join('');
  }

  document.getElementById('art-count').textContent = `${visibleArt} articles`;
  updateKPIs();
}

/* ── VALIDATION ──────────────────────────────────────────────────────────── */
/**
 * Valide une valeur selon le champ.
 * Retourne null si OK, sinon un message d'erreur.
 */
function validate(field, value) {
  if (field === 'designation') {
    if (!value || !value.trim()) return 'La désignation ne peut pas être vide.';
  }
  if (field === 'pu_ht') {
    if (isNaN(value)) return 'Le PU HT doit être un nombre.';
    if (value < 0)    return 'Le PU HT ne peut pas être négatif.';
  }
  if (field === 'qty_ref') {
    if (isNaN(value)) return 'La quantité doit être un nombre.';
    if (value < 0)    return 'La quantité ne peut pas être négative.';
  }
  return null;
}

function showInputError(td, msg) {
  td.classList.add('cell-error');
  td.classList.remove('editing');
  showFlash(`Erreur saisie : ${msg}`, true);
  // Auto-scroll vers la cellule en erreur
  td.scrollIntoView({ behavior: 'smooth', block: 'center' });
  // Retire la bordure rouge après 3 s
  setTimeout(() => td.classList.remove('cell-error'), 3000);
}

/* ── INLINE EDITING (articles) ──────────────────────────────────────────── */
function startEdit(td, evt) {
  evt.stopPropagation();
  if (td.classList.contains('editing')) return;

  const artId = parseInt(td.dataset.id);
  const field = td.dataset.field;
  const art   = localData.find(a => a.id === artId);
  if (!art) return;

  let curVal = '';
  if      (field === 'designation') curVal = art.designation || '';
  else if (field === 'unit')        curVal = art.unit        || '';
  else if (field === 'qty_ref')     curVal = (art.quantity   || 0).toString();
  else if (field === 'pu_ht')       curVal = (art.pu_ht      || 0).toString();

  const isNum = (field === 'qty_ref' || field === 'pu_ht');
  td.classList.add('editing');
  td.innerHTML = `<input class="inline-inp${isNum ? ' r' : ''}"
    type="${isNum ? 'number' : 'text'}"
    step="${field === 'pu_ht' ? '0.01' : '1'}" min="0"
    value="${esc(curVal)}">`;

  const inp = td.querySelector('input');
  inp.focus();
  inp.select();

  let committed = false;
  function commit() {
    if (committed) return;
    committed = true;
    const rawVal  = inp.value;
    const newVal  = isNum ? parseFloat(rawVal) : rawVal.trim();
    const errMsg  = validate(field, newVal);
    if (errMsg) {
      showInputError(td, errMsg);
      return;
    }
    td.classList.remove('editing');

    // Mise à jour locale
    if      (field === 'designation') art.designation = newVal;
    else if (field === 'unit')        art.unit        = newVal;
    else if (field === 'qty_ref')     art.quantity    = newVal;
    else if (field === 'pu_ht')       art.pu_ht       = newVal;

    art._manual = true;

    if (art.id < 0) markNew(art);
    else            markDirty(art.id, field, newVal);

    render();
  }

  inp.addEventListener('blur',    commit);
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { inp.blur(); }
    if (e.key === 'Escape') { committed = true; td.classList.remove('editing'); render(); }
  });
}

/* ── INLINE EDITING (ratio €/m² ou €/kWc section) ──────────────────────── */
function startSecRatioEdit(td, chap, sec, currentRatio, ratioUnit) {
  if (td.classList.contains('editing')) return;
  const unit = ratioUnit || (isPVChap(chap) ? 'kwc' : 'm2');
  const lbl  = unit === 'kwc' ? '€/kWc' : '€/m²';
  td.classList.add('editing');
  td.innerHTML = `<input class="inline-inp r inline-inp-ratio"
    type="number" step="0.1" min="0"
    value="${currentRatio > 0 ? currentRatio.toFixed(2) : ''}"
    placeholder="${lbl}">`;

  const inp = td.querySelector('input');
  inp.focus();
  inp.select();

  let committed = false;
  function commit() {
    if (committed) return;
    committed = true;
    const val = parseFloat(inp.value);
    if (isNaN(val) || val < 0) {
      showInputError(td, 'Le ratio doit être un nombre positif.');
      return;
    }
    td.classList.remove('editing');
    const secKey = chap + '|||' + sec;
    sectionRatios[secKey] = { ratio: val, unit, manual: true };
    markSectionRatio(chap, sec, val, unit);
    render();
  }

  inp.addEventListener('blur',    commit);
  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter')  { inp.blur(); }
    if (e.key === 'Escape') { committed = true; td.classList.remove('editing'); render(); }
  });
}

/* ── SUPPRESSION ARTICLE ─────────────────────────────────────────────────── */
function deleteArticle(artId, isCustom) {
  const a = localData.find(x => x.id === artId);
  if (!a) return;

  const msg = isCustom
    ? `Supprimer définitivement "${a.designation}" ?`
    : `Masquer "${a.designation}" de la bibliothèque ? (conservé dans le référentiel PSA)`;
  if (!confirm(msg)) return;

  // Suppression locale immédiate
  const idx = localData.findIndex(x => x.id === artId);
  if (idx !== -1) localData.splice(idx, 1);
  if (selectedId === artId) { selectedId = null; closePanel(); }

  // Persistance
  if (artId > 0) {
    fetch('/api/bibliotheque/article/delete', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ id: artId, is_custom: !!isCustom }),
    })
    .then(async r => {
      const text = await r.text();
      let d = {};
      try { d = JSON.parse(text); } catch(_) {}
      if (!r.ok) {
        console.error('[bibliotheque] delete ERREUR', r.status, d);
        showFlash(`Erreur ${r.status} — ${d.message || 'suppression échouée'} (voir logs/app_errors.log)`, true);
        return;
      }
      showFlash(d.action === 'deleted' ? 'Article supprimé' : 'Article masqué');
    })
    .catch(err => {
      console.error('[bibliotheque] delete fetch FAILED', err);
      showFlash('Erreur réseau lors de la suppression. Vérifier logs/app_errors.log', true);
    });
  }

  render();
}

/* ── AJOUT ARTICLE ───────────────────────────────────────────────────────── */
function addArticle(chap, sec) {
  const tree      = buildTree(localData);
  const targetSec = sec || (tree[chap] ? Object.keys(tree[chap])[0] : 'Nouvelle section');

  const newArt = {
    id:          _tempIdSeq--,
    chapter:     chap,
    section:     targetSec,
    designation: 'Nouvel article',
    unit:        'u',
    ratio_type:  'UNITAIRE',
    quantity:    0,
    pu_ht:       0,
    is_custom:   1,
    _manual:     true,
    row_order:   9999,
  };
  // Insère après le dernier article de la section cible (ordre d'affichage)
  let insertIdx = localData.length;
  for (let i = localData.length - 1; i >= 0; i--) {
    if (localData[i].chapter === chap && localData[i].section === targetSec) {
      insertIdx = i + 1; break;
    }
  }
  localData.splice(insertIdx, 0, newArt);
  expandedChaps.add(chap);
  expandedSecs.add(chap + '|||' + targetSec);
  markNew(newArt);
  render();

  requestAnimationFrame(() => {
    const tr = document.querySelector(`tr[data-id="${newArt.id}"]`);
    if (tr) { const td = tr.querySelector('[data-field="designation"]'); if (td) td.click(); }
  });
}

/* ── RENOMMAGE DE SECTION ────────────────────────────────────────────────── */
function startSecNameEdit(span, chap, sec) {
  if (span.querySelector('input')) return; // déjà en édition
  const oldName = sec;
  const inp = document.createElement('input');
  inp.type  = 'text';
  inp.value = oldName;
  inp.className = 'sec-name-inp';

  span.textContent = '';
  span.appendChild(inp);
  inp.focus();
  inp.select();

  function commit() {
    const newName = inp.value.trim();
    inp.removeEventListener('blur', commit);
    if (!newName || newName === oldName) { render(); return; }

    // Unicité dans le chapitre
    const tree = buildTree(localData);
    if (tree[chap] && tree[chap][newName]) {
      showFlash(`La section "${newName}" existe déjà dans ce chapitre.`, true);
      render(); return;
    }

    // Mise à jour locale
    const affected = localData.filter(a => a.chapter === chap && a.section === oldName);
    for (const a of affected) a.section = newName;

    // Renommer la clé dans sectionRatios
    const oldKey = chap + '|||' + oldName;
    const newKey = chap + '|||' + newName;
    if (sectionRatios[oldKey] !== undefined) {
      sectionRatios[newKey] = sectionRatios[oldKey];
      delete sectionRatios[oldKey];
    }

    // Renommer dans expandedSecs
    if (expandedSecs.has(oldKey)) { expandedSecs.delete(oldKey); expandedSecs.add(newKey); }

    // Dirty : tous les articles affectés + renommage ratio
    for (const a of affected) {
      if (a.id > 0) {
        dirtyMap.set(`${a.id}__section`, { id: a.id, field: 'section', value: newName });
      }
    }
    dirtyMap.set(`secren__${chap}___${oldName}`, {
      id: null, field: 'section_ratio_rename',
      chapter: chap, old_section: oldName, value: newName,
    });

    schedSave();
    render();
  }

  inp.addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); inp.removeEventListener('blur', commit); commit(); }
    if (e.key === 'Escape') { inp.removeEventListener('blur', commit); render(); }
  });
  inp.addEventListener('blur', commit);
}

/* ── SUPPRESSION SECTION (+ tous ses articles) ───────────────────────────── */
function deleteSection(chap, sec) {
  const secArts = localData.filter(a => a.chapter === chap && a.section === sec);
  const count   = secArts.length;
  const msg = count > 0
    ? `Supprimer la section "${sec}" et ses ${count} articles ?\n\nArticles PSA → masqués. Articles custom → supprimés définitivement.`
    : `Supprimer la section vide "${sec}" ?`;
  if (!confirm(msg)) return;

  // Suppression locale
  localData = localData.filter(a => !(a.chapter === chap && a.section === sec));

  const secKey = chap + '|||' + sec;
  delete sectionRatios[secKey];
  expandedSecs.delete(secKey);
  if (selectedId !== null && !localData.find(a => a.id === selectedId)) {
    selectedId = null; closePanel();
  }

  // Persistance : toujours envoyer section_delete (articles PSA/custom + ligne bibliotheque_section_ratios)
  try {
    dirtyMap.set(`secdel__${chap}|||${sec}`, {
      id: null, field: 'section_delete', chapter: chap, section: sec, value: null,
    });
    schedSave();
  } catch (err) {
    console.error('[bibliotheque] deleteSection — mise en file save impossible', err);
  }

  try {
    render();
  } catch (err) {
    console.error('[bibliotheque] deleteSection — render() après suppression section', err);
  }
}

/* ── AJOUT SECTION après une section existante ───────────────────────────── */
function addSectionAfter(chap, afterSec) {
  const secName = prompt('Nom de la nouvelle section :', '');
  if (!secName || !secName.trim()) return;
  const name = secName.trim();

  // Vérifie unicité dans le chapitre
  const tree = buildTree(localData);
  if (tree[chap] && tree[chap][name]) {
    showFlash(`La section "${name}" existe déjà dans ce chapitre.`, true);
    return;
  }

  const newArt = {
    id:          _tempIdSeq--,
    chapter:     chap,
    section:     name,
    designation: 'Nouvel article',
    unit:        'u',
    ratio_type:  'UNITAIRE',
    quantity:    0,
    pu_ht:       0,
    is_custom:   1,
    _manual:     true,
    row_order:   9999,
  };

  // Insère dans localData juste après le dernier article de afterSec
  // → le tree builder placera la nouvelle section immédiatement après
  let insertIdx = localData.length;
  for (let i = localData.length - 1; i >= 0; i--) {
    if (localData[i].chapter === chap && localData[i].section === afterSec) {
      insertIdx = i + 1; break;
    }
  }
  localData.splice(insertIdx, 0, newArt);

  expandedChaps.add(chap);
  expandedSecs.add(chap + '|||' + name);
  markNew(newArt);
  render();

  requestAnimationFrame(() => {
    const tr = document.querySelector(`tr[data-id="${newArt.id}"]`);
    if (tr) {
      tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const td = tr.querySelector('[data-field="designation"]');
      if (td) td.click();
    }
  });
}

/* ── DUPLIQUER UN ARTICLE ────────────────────────────────────────────────── */
function duplicateArticle(artId) {
  const src = localData.find(a => a.id === artId);
  if (!src) return;

  const copy = Object.assign({}, src, {
    id:        _tempIdSeq--,
    _manual:   true,
    is_custom: 1,
  });

  // Insère juste après la source dans localData
  const srcIdx = localData.findIndex(a => a.id === artId);
  localData.splice(srcIdx + 1, 0, copy);

  expandedChaps.add(copy.chapter);
  expandedSecs.add(copy.chapter + '|||' + copy.section);
  markNew(copy);
  render();

  requestAnimationFrame(() => {
    const tr = document.querySelector(`tr[data-id="${copy.id}"]`);
    if (tr) {
      tr.scrollIntoView({ behavior: 'smooth', block: 'center' });
      tr.style.outline = '2px solid var(--accent-b)';
      setTimeout(() => { tr.style.outline = ''; }, 1200);
    }
  });
}

/* ── KPIs ────────────────────────────────────────────────────────────────── */
function updateKPIs() {
  // Même logique section-first que le rendu : respecte les ratios manuels de section
  const totals = { cfo: 0, cfa: 0, pv: 0 };
  const tree = buildTree(filteredArticles());
  for (const chap of CHAP_ORDER) {
    if (!tree[chap]) continue;
    const lot = isPVChap(chap) ? 'pv'
      : ((chap.toLowerCase().includes('faible') || chap.toLowerCase().includes('cfa')) ? 'cfa' : 'cfo');
    for (const [sec, secArts] of Object.entries(tree[chap])) {
      const secKey  = chap + '|||' + sec;
      const { ratio: sRatio, unit: sUnit, manual: sManual } = calcSectionRatio(secKey, secArts, chap);
      const sDivisor = sUnit === 'kwc' ? (kwc || 0) : (sdo || 0);
      totals[lot] += sManual
        ? sRatio * sDivisor
        : secArts.reduce((s, a) => s + calcTotal(a), 0);
    }
  }
  const total = totals.cfo + totals.cfa + totals.pv;

  function setKpi(id, val, m2Id, divisor, unit) {
    const el = document.getElementById(id); if (!el) return;
    const e2 = document.getElementById(m2Id);
    if (val > 0) {
      // num() sans symbole € pour éviter le doublon avec <small>€ HT</small>
      el.innerHTML = `${num(val, 0)}<small>€ HT</small>`;
      if (e2) e2.textContent = divisor > 0 ? `${Math.round(val / divisor)} ${unit}` : '—';
    } else {
      el.innerHTML = '—<small>€ HT</small>';
      if (e2) e2.textContent = '—';
    }
  }
  setKpi('kpi-cfo',   totals.cfo, 'kpi-cfo-m2',   sdo, '€/m²');
  setKpi('kpi-cfa',   totals.cfa, 'kpi-cfa-m2',   sdo, '€/m²');
  setKpi('kpi-pv',    totals.pv,  'kpi-pv-m2',    kwc, '€/kWc');
  setKpi('kpi-total', total,      'kpi-total-m2',  sdo, '€/m²');
}

/* ── INSPECTEUR ──────────────────────────────────────────────────────────── */
function selectArt(id) {
  selectedId = id;
  const a = localData.find(x => x.id === id);
  if (!a) return;
  render();
  openPanel(a);
}

function closePanel() {
  selectedId = null;
  document.getElementById('rpanel').classList.add('closed');
  render();
}

function openPanel(a) {
  const rpanel    = document.getElementById('rpanel');
  const rpcontent = document.getElementById('rp-content');
  const rpempty   = document.getElementById('rp-empty');
  rpanel.classList.remove('closed');
  rpempty.style.display   = 'none';
  rpcontent.style.display = 'block';

  const cm    = CHAP_META[a.chapter] || { banner: 'cfo' };
  const pu    = a.pu_ht    || 0;
  const qty   = a.quantity || 0;
  const total = calcTotal(a);
  const secKey = a.chapter + '|||' + a.section;
  const { ratio: secRatio } = calcSectionRatio(secKey, localData.filter(x => x.chapter === a.chapter && x.section === a.section));

  rpcontent.innerHTML = `
    <div class="rp-banner ${cm.banner}">
      <div class="rp-banner-chap">${esc(a.chapter)} — ${esc(a.section)}</div>
      <div class="rp-banner-desig">${esc(a.designation)}</div>
      <div style="margin-top:6px;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        ${a.lot ? `<span class="lot-tag lot-${a.lot}" style="font-size:10px">${a.lot}</span>` : ''}
        ${a.last_updated ? `<span style="font-size:10px;color:rgba(255,255,255,.5)">Mis à jour : ${a.last_updated}</span>` : ''}
      </div>
    </div>

    <div class="rp-sec-lbl">Prix unitaire HT</div>
    <div class="rp-field">
      <div class="rp-field-lbl">PU HT (€)</div>
      <input class="rp-input" id="rp-pu-inp" type="number" step="0.01" min="0"
             value="${pu ? pu.toFixed(2) : ''}" placeholder="Saisir un prix…">
      ${a.has_override ? `<div class="rp-hint rp-hint-warn">Override actif : ${esc(a.override_raison || '')}</div>` : ''}
    </div>
    <div class="rp-field">
      <div class="rp-field-lbl">Raison (si correction)</div>
      <input class="rp-input" id="rp-raison-inp" type="text" placeholder="Ex : Devis 2025 Vinci…">
    </div>

    <div class="rp-sec-lbl">Simulation — ${num(sdo, 0)} m² SDO</div>
    <div class="rp-row">
      <span class="rp-key">Type ratio</span>
      <span class="rp-val">${a.ratio_type}</span>
    </div>
    <div class="rp-row">
      <span class="rp-key">Unité</span>
      <span class="rp-val mono">${esc(a.unit)}</span>
    </div>
    ${qty > 0 ? `<div class="rp-row">
      <span class="rp-key">Quantité</span>
      <span class="rp-val hi">${num(qty, 0)} ${esc(a.unit)}</span>
    </div>` : ''}
    <div class="rp-row">
      <span class="rp-key">Total HT calculé</span>
      <span class="rp-val hi" id="rp-total-calc">${total > 0 ? moneyDec(total) : '—'}</span>
    </div>
    <div class="rp-row">
      <span class="rp-key">Ratio section €/m²</span>
      <span class="rp-val mono">${secRatio > 0 ? num(secRatio, 1) + ' €/m²' : '—'}</span>
    </div>

    <div class="rp-actions">
      <button class="rp-save-btn" onclick="saveOverride(${a.id})">Enregistrer correction</button>
    </div>
  `;

  document.getElementById('rp-pu-inp').addEventListener('input', function() {
    const newPu = parseFloat(this.value) || 0;
    let newTotal = 0;
    if (a.ratio_type === 'SURFACIQUE') newTotal = qty > 0 ? qty * newPu : newPu * sdo;
    else                                newTotal = qty > 0 ? qty * newPu : 0;
    const el = document.getElementById('rp-total-calc');
    if (el) el.textContent = newTotal > 0 ? moneyDec(newTotal) : '—';
  });
}

function saveOverride(artId) {
  const puEl     = document.getElementById('rp-pu-inp');
  const raisonEl = document.getElementById('rp-raison-inp');
  const pu       = parseFloat(puEl ? puEl.value : '');
  const raison   = raisonEl ? raisonEl.value.trim() : '';
  if (!pu || pu <= 0) { showFlash('Saisir un PU valide (> 0).', true); return; }

  fetch('/api/ratio/correct', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ dpgf_article_id: artId, pu_override: pu, raison, scope: 'base' }),
  })
  .then(r => r.json())
  .then(data => {
    if (data.status === 'ok') {
      const a = localData.find(x => x.id === artId);
      if (a) { a.pu_ht = pu; a.has_override = 1; a.override_raison = raison; a._manual = true; }
      render();
      openPanel(localData.find(x => x.id === artId));
      showFlash('Correction enregistrée');
    } else {
      showFlash('Erreur : ' + (data.error || 'inconnue'), true);
    }
  })
  .catch(() => showFlash('Erreur réseau.', true));
}

/* ── ARBRE — INTERACTIONS ────────────────────────────────────────────────── */
function toggleChap(chap) {
  if (expandedChaps.has(chap)) expandedChaps.delete(chap);
  else expandedChaps.add(chap);
  render();
}
function toggleSec(chap, sec) {
  const key = chap + '|||' + sec;
  if (expandedSecs.has(key)) expandedSecs.delete(key);
  else expandedSecs.add(key);
  render();
}
function setExpandAll(open) {
  if (open) {
    CHAP_ORDER.forEach(c => expandedChaps.add(c));
    const tree = buildTree(localData);
    for (const chap of CHAP_ORDER) {
      if (!tree[chap]) continue;
      for (const sec of Object.keys(tree[chap])) expandedSecs.add(chap + '|||' + sec);
    }
  } else {
    expandedChaps.clear();
    expandedSecs.clear();
  }
  render();
}

/* ── FLASH ───────────────────────────────────────────────────────────────── */
function showFlash(msg, isErr = false) {
  const el = document.createElement('div');
  el.className  = 'flash-ok';
  if (isErr) el.style.background = 'var(--danger)';
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

/* ── SDO ─────────────────────────────────────────────────────────────────── */
document.getElementById('sdo-inp').addEventListener('input', function() {
  sdo = parseInt(this.value) || 0;
  render();
  if (selectedId) { const a = localData.find(x => x.id === selectedId); if (a) openPanel(a); }
});

/* ── PUISSANCE PV (kWc) ──────────────────────────────────────────────────── */
let _kwcSaveTimer = null;
document.getElementById('kwc-inp').addEventListener('input', function() {
  kwc = parseInt(this.value) || 0;
  // Recalcul uniquement du chapitre PV — render() est section-first et isPVChap-aware
  render();
  if (selectedId) { const a = localData.find(x => x.id === selectedId); if (a) openPanel(a); }

  // Persistance vers /api/affaire/<id>/params si une affaire est chargée
  if (!AFFAIRE_ID) return;
  clearTimeout(_kwcSaveTimer);
  _kwcSaveTimer = setTimeout(() => {
    fetch(`/api/affaire/${AFFAIRE_ID}/params`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ kva_cible: kwc }),
    })
    .then(r => r.ok ? null : Promise.reject(r.status))
    .catch(err => console.error('[bibliotheque] kwc save failed', err));
  }, 600);
});

/* ── RECHERCHE ───────────────────────────────────────────────────────────── */
let _searchTimer;
document.getElementById('search-inp').addEventListener('input', function() {
  clearTimeout(_searchTimer);
  _searchTimer = setTimeout(() => { searchQ = this.value.trim(); render(); }, 150);
});

/* ── INIT ────────────────────────────────────────────────────────────────── */
render();
