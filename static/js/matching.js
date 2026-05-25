'use strict';

// ─── État global ──────────────────────────────────────────────────────────────
let _data         = null;   // réponse complète de /api/matching/<id>/data
let _modalLineId  = null;   // id ligne en cours d'édition dans le modal
let _modalLot     = null;
let _allCandidates = [];    // candidats chargés pour la ligne courante
let _selectedArtId = null;  // article sélectionné dans le modal
let _modalLineInfo = null;  // infos ligne devis (unit, pu, context_path, chapter, section)
let _modalMode = 'suggestions'; // 'suggestions' | 'create'
let _browseCache = null; // liste des articles du sous-chapitre (browse)
let _browseOptions = null; // {chapters, sections, selected_chapter}

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  enableDraggableModal();
  if (PROJECT_ID) loadData();
});

/** En-têtes Excel (Nature « Titre » + ratio / vide, sans montant) — hors postes devis (voir app.py). */
function isExcelStructureMetaRow(line) {
  if (line.row_type !== 'article') return false;
  const u = (line.unit || '').trim().toLowerCase();
  if (u !== 'titre') return false;
  const d = (line.original_designation || '').trim().toLowerCase();
  if ((line.unit_price_ht || 0) || (line.total_ht || 0)) return false;
  const ratioOnly = new Set(['surfacique', 'unitaire']);
  return ratioOnly.has(d) || !d;
}

function goProject() {
  const sel = document.getElementById('sel-project');
  if (sel && sel.value) window.location.href = `/matching/${sel.value}`;
}

// ─── Chargement données ───────────────────────────────────────────────────────
async function loadData() {
  try {
    const r = await fetch(`/api/matching/${PROJECT_ID}/data`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    _data = await r.json();
    renderAll();
  } catch (e) {
    document.getElementById('mv-loading').textContent = `Erreur de chargement : ${e.message}`;
  }
}

// ─── Rendu principal ──────────────────────────────────────────────────────────
function renderAll() {
  const proj = _data.project;
  document.getElementById('mv-title').textContent =
    `⚙ Revue Matching — ${proj.name || 'Projet #' + PROJECT_ID}`;

  // Sync champs header
  if (proj.devis_date) document.getElementById('inp-devis-date').value = proj.devis_date;
  if (proj.surface_sdo) document.getElementById('inp-sdo').value = proj.surface_sdo;

  // Statistiques
  let total = 0, mapped = 0, pending = 0, exclu = 0;
  for (const chap of _data.chapters)
    for (const sec of chap.sections)
      for (const l of sec.lines)
        if (l.row_type === 'article' && !isExcelStructureMetaRow(l)) {
          total++;
          if (l.mapping_status === 'excluded') exclu++;
          else if (l.mapping_status === 'auto' || l.mapping_status === 'manual') mapped++;
          else pending++;
        }
  document.getElementById('mv-stats').innerHTML =
    `${total} lignes · <span style="color:#3fb950">${mapped} mappées</span> · ` +
    `<span style="color:#d29922">${pending} en attente</span> · ` +
    `<span style="color:#666">${exclu} exclues</span>`;

  if (mapped > 0) document.getElementById('btn-validate').disabled = false;

  // Rendu chapitres
  const body = document.getElementById('mv-body');
  body.innerHTML = '';
  for (const chap of _data.chapters) {
    body.appendChild(renderChapter(chap));
  }
}

function renderChapter(chap) {
  const el = document.createElement('div');
  el.className = 'mv-chapter';
  el.dataset.chap = chap.name;

  const header = document.createElement('div');
  header.className = 'mv-chapter-header';
  header.innerHTML = `
    <span class="mv-chapter-title">${esc(chap.name)}</span>
    <span class="lot-badge lot-${chap.lot}">${chap.lot}</span>
    <span class="mv-chapter-toggle">▼</span>`;
  header.addEventListener('click', () => toggleChapter(header));

  el.appendChild(header);
  for (const sec of chap.sections) el.appendChild(renderSection(sec, chap.lot));
  return el;
}

function toggleChapter(header) {
  header.classList.toggle('collapsed');
  const chap = header.parentElement;
  chap.querySelectorAll('.mv-section').forEach(s => {
    s.style.display = header.classList.contains('collapsed') ? 'none' : '';
  });
}

function renderSection(sec, chap_lot) {
  const el = document.createElement('div');
  el.className = 'mv-section';

  const hdr = document.createElement('div');
  hdr.className = 'mv-section-header';
  hdr.innerHTML = `
    <span class="mv-section-name">${esc(sec.name)}</span>
    <div class="mv-section-kpi">
      <div class="kpi-item">
        <span class="kpi-label">Σ Total HT :</span>
        <span class="kpi-value">${fmt(sec.total_ht)} €</span>
      </div>
      <div class="kpi-item">
        <span class="kpi-label">Ratio :</span>
        <span class="kpi-value kpi-ratio">${sec.ratio_m2} €/m²</span>
        <span class="kpi-label" style="font-size:10px">(SDO ${_data.sdo} m² — exclues incluses ✓)</span>
      </div>
    </div>`;
  el.appendChild(hdr);

  // Table
  const wrap = document.createElement('div');
  wrap.className = 'mv-table-wrap';
  const table = document.createElement('table');
  table.className = 'mv-table';
  table.innerHTML = `
    <colgroup>
      <col class="col-desig"><col class="col-unit"><col class="col-qty">
      <col class="col-pu-dev"><col class="col-tot-dev"><col class="col-base">
      <col class="col-pu-base"><col class="col-pu-wp"><col class="col-ecart">
      <col class="col-actions">
    </colgroup>
    <thead><tr>
      <th>Désignation Devis</th><th>U.</th><th>Qté</th>
      <th>PU Devis</th><th>Total HT</th><th>Désignation Base</th>
      <th>PU Base</th><th title="Saisie directe ; laisser vide puis valider pour rétablir le calcul auto après une saisie manuelle.">PU Calculé ⓘ</th><th>Écart</th><th>Actions</th>
    </tr></thead>
    <tbody id="tbody-${sanitizeId(sec.name)}"></tbody>`;

  el.appendChild(wrap);
  wrap.appendChild(table);

  const tbody = table.querySelector('tbody');
  for (const line of sec.lines) {
    if (line.row_type === 'article' && !isExcelStructureMetaRow(line)) {
      tbody.appendChild(renderRow(line, chap_lot));
    }
  }
  return el;
}

function findLineById(lineId) {
  if (!_data || !_data.chapters) return null;
  for (const chap of _data.chapters) {
    for (const sec of chap.sections) {
      for (const l of sec.lines) {
        if (l.id === lineId) return l;
      }
    }
  }
  return null;
}

/** Chapitre et sous-chapitre du référentiel DPGF | désignation article base.
 *  Si la ligne n'est pas encore mappée, affiche le 1er candidat (même source DPGF). */
function formatBaseBreadcrumb(line, lineInfo, firstCandidate) {
  const info = lineInfo || {};
  let titre = (info.base_chapter != null ? String(info.base_chapter) : '').trim();
  let sous = (info.base_section != null ? String(info.base_section) : '').trim();
  if (!titre && line && line.base_chapter != null) titre = String(line.base_chapter).trim();
  if (!sous && line && line.base_section != null) sous = String(line.base_section).trim();
  let article = (line && line.base_designation) ? String(line.base_designation).trim() : '';

  const mapped = line && Number(line.dpgf_article_id) > 0;
  if (!mapped && firstCandidate) {
    const tc = (firstCandidate.chapter || '').trim();
    const sc = (firstCandidate.section || '').trim();
    const ac = (firstCandidate.designation || '').trim();
    if (tc || sc || ac) {
      titre = tc || titre;
      sous = sc || sous;
      article = ac || article;
    }
  }

  if (!titre) titre = '—';
  if (!sous) sous = '—';
  const articleDisp = article || '—';
  return `${titre} | ${sous} | ${articleDisp}`;
}

/** Texte complet pour attribut title (sélecteur base). */
function baseSelectorTitle(line) {
  if (!line) return 'Cliquer pour choisir un article base';
  const d = (line.base_designation || '').trim();
  return d || 'Cliquer pour choisir un article base';
}

function parsePuCalculInput(str) {
  const t = (str || '').trim().replace(/\s/g, '').replace(',', '.');
  if (!t) return null;
  const n = parseFloat(t);
  if (Number.isNaN(n) || n < 0) return NaN;
  return n;
}

function fmtForPuInput(n) {
  if (n == null || Number.isNaN(n)) return '';
  return String(n).replace('.', ',');
}

async function postPuWp(lineId, value) {
  const body = { weighted_price: value == null ? null : value };
  const r = await fetch(`/api/matching/line/${lineId}/weighted_price`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok || d.status !== 'ok') throw new Error(d.error || 'Erreur');
  return d;
}

function updateWpRowFromServer(tr, lineId, d) {
  const line = findLineById(lineId);
  if (line) {
    line.weighted_price = d.weighted_price;
    line.wp_tooltip = d.wp_tooltip;
    line.ecart_pct = d.ecart_pct;
    line.wp_manual = !!d.wp_manual;
  }
  const model = line || {
    id: lineId,
    mapping_status: tr.classList.contains('mv-excluded') ? 'excluded' : 'manual',
    weighted_price: d.weighted_price,
    wp_tooltip: d.wp_tooltip,
    wp_manual: !!d.wp_manual,
  };
  const wpCell = tr.querySelector('.col-pu-wp');
  if (wpCell) {
    wpCell.outerHTML = buildWpCell(model);
  }
  const ecartCell = tr.querySelector('.col-ecart');
  if (ecartCell) ecartCell.outerHTML = buildEcartCell(d.ecart_pct);
  attachPuWpEditor(tr);
}

async function savePuWpFromInput(tr, inp, td) {
  const lineId = parseInt(td.dataset.lineId, 10);
  const wasManual = td.dataset.wpManual === '1';
  const raw = (inp.value || '').trim();
  const sw = td.dataset.stableWp;
  const stable = sw === '' || sw == null ? NaN : parseFloat(String(sw).replace(',', '.'));

  if (raw === '') {
    if (wasManual) {
      try {
        const d = await postPuWp(lineId, null);
        updateWpRowFromServer(tr, lineId, d);
        toast('PU calculé : valeur automatique rétablie', 'ok');
      } catch (e) {
        toast(e.message, 'err');
        if (!Number.isNaN(stable)) inp.value = fmtForPuInput(stable);
      }
    } else if (!Number.isNaN(stable)) {
      inp.value = fmtForPuInput(stable);
    }
    return;
  }

  const parsed = parsePuCalculInput(raw);
  if (Number.isNaN(parsed)) {
    toast('PU calculé : nombre invalide', 'err');
    if (!Number.isNaN(stable)) inp.value = fmtForPuInput(stable);
    return;
  }

  try {
    const d = await postPuWp(lineId, parsed);
    updateWpRowFromServer(tr, lineId, d);
    toast('PU calculé enregistré', 'ok');
  } catch (e) {
    toast(e.message, 'err');
    if (!Number.isNaN(stable)) inp.value = fmtForPuInput(stable);
  }
}

function attachPuWpEditor(tr) {
  const td = tr.querySelector('.col-pu-wp.pu-wp-cell');
  if (!td || tr.classList.contains('mv-excluded')) return;
  const inp = td.querySelector('.pu-wp-input');
  if (!inp || td.dataset.puBound === '1') return;
  td.dataset.puBound = '1';

  inp.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      inp.blur();
    }
  });

  inp.addEventListener('blur', () => {
    savePuWpFromInput(tr, inp, td);
  });
}

function renderRow(line, chap_lot) {
  const tr = document.createElement('tr');
  tr.id = `row-${line.id}`;
  tr.className = line.mapping_status === 'excluded' ? 'mv-excluded' : '';

  const lot  = line.lot || chap_lot;
  const exclu = line.mapping_status === 'excluded';

  // Col 1 — toujours le libellé importé depuis le devis (col. B), jamais la base DPGF
  const desig = line.original_designation || '—';
  const td1 = `<td class="col-desig" title="${esc(desig)}">
    <span class="cell-desig-text">${esc(desig)}</span>
  </td>`;

  // Col 2–5 — Numériques
  const td2 = `<td class="col-unit">${esc(line.unit || '—')}</td>`;
  const td3 = `<td class="col-qty">${line.quantity != null ? fmtQty(line.quantity) : '—'}</td>`;
  const td4 = `<td class="col-pu-dev">${line.unit_price_ht != null ? fmt(line.unit_price_ht) : '—'}</td>`;
  const td5 = `<td class="col-tot-dev" style="font-weight:600">${line.total_ht != null ? fmt(line.total_ht) : '—'}</td>`;

  // Col 6 — Sélecteur base
  const hasMapped = !!line.base_designation;
  const score = (line.mapping_score != null) ? parseFloat(line.mapping_score) : null;
  const parsingOk = (score != null && !isNaN(score) && score >= 80);
  const needsAttention = !exclu && (!hasMapped || !parsingOk || (line.mapping_status !== 'auto' && line.mapping_status !== 'manual'));
  const selText = hasMapped ? (line.base_designation || '') : '⊕ Sélectionner…';
  const baseTitleEsc = esc(baseSelectorTitle(line));
  const td6 = `<td class="col-base" title="${baseTitleEsc}">
    <button class="btn-selector ${hasMapped ? 'mapped' : ''} ${needsAttention ? 'needs-attention' : ''}"
            id="sel-btn-${line.id}" title="${baseTitleEsc}" ${exclu ? 'disabled style="opacity:.4;cursor:default"' : ''}>
      <span class="sel-text">${esc(selText)}</span>
      <span class="sel-icon">▾</span>
    </button>
  </td>`;

  // Col 7 — PU base
  const td7 = `<td class="col-pu-base" id="pu-base-${line.id}">${line.base_pu != null ? fmt(line.base_pu) : '—'}</td>`;

  // Col 8 — PU calculé + tooltip
  const td8 = buildWpCell(line);

  // Col 9 — % Écart
  const td9 = buildEcartCell(line.ecart_pct);

  // Col 10 — Actions
  const statusLabel = { auto:'auto', manual:'modifié', pending:'en attente', excluded:'exclu' }[line.mapping_status] || line.mapping_status;
  const td10 = `<td class="col-actions">
    <button class="btn-exclu ${exclu ? 'is-excluded' : ''}"
            onclick="toggleExclude(${line.id})"
            title="${exclu ? 'Réinclure la ligne' : 'Exclure la ligne'}">
      ${exclu ? '↩ inclure' : '⊘ exclure'}
    </button>
  </td>`;

  tr.innerHTML = td1 + td2 + td3 + td4 + td5 + td6 + td7 + td8 + td9 + td10;

  // Bind click handler safely (avoid breaking HTML with apostrophes)
  const btn = tr.querySelector(`#sel-btn-${line.id}`);
  if (btn && !btn.disabled) {
    btn.addEventListener('click', () => openModal(line.id, lot, desig));
  }

  attachPuWpEditor(tr);

  return tr;
}

function buildWpCell(line) {
  const excluded = line.mapping_status === 'excluded';
  const hasWp = line.weighted_price != null && line.weighted_price === line.weighted_price;
  const wpManual = !!line.wp_manual;
  const manualCls = wpManual ? ' pu-wp-cell-manual' : '';
  const conf = (line.wp_tooltip && line.wp_tooltip.confidence) || 'NONE';
  const tt = line.wp_tooltip || {};
  const baseAge = tt.base_age_years != null ? tt.base_age_years.toFixed(1) + 'a' : '?';
  const devAge = tt.devis_age_years != null ? tt.devis_age_years.toFixed(1) + 'a' : '?';
  const baseW = tt.base_weight != null ? tt.base_weight.toFixed(2) : '?';
  const devW = tt.devis_weight != null ? tt.devis_weight.toFixed(2) : '?';
  const baseAct = tt.base_actualized != null ? fmt(tt.base_actualized) : '—';
  const devisAct = tt.devis_actualized != null ? fmt(tt.devis_actualized) : '—';
  const computedRow =
    tt.computed_weighted_price != null && tt.computed_weighted_price === tt.computed_weighted_price
      ? `<tr><td class="t-label">Calcul auto</td><td class="t-val">${fmt(tt.computed_weighted_price)} €</td></tr>`
      : '';

  const tooltipHtml = `
    <div class="mv-tooltip">
      <table>
        <tr><td class="t-label">Base actualisée</td>
            <td class="t-val">${baseAct} € <span style="color:#888">(âge ${baseAge} · P=${baseW})</span></td></tr>
        <tr><td class="t-label">Devis actualisé</td>
            <td class="t-val">${devisAct} € <span style="color:#888">(âge ${devAge} · P=${devW})</span></td></tr>
        ${computedRow}
        <tr><td class="t-label t-result">Prix pondéré</td>
            <td class="t-val t-result">${fmt(line.weighted_price)} €</td></tr>
        <tr><td colspan="2" style="color:#666;font-size:10px;padding-top:4px">
          Confiance : <span class="conf-dot conf-${conf}" style="display:inline-block;margin:0 3px"></span>${conf}
          · Taux actualisation 3%/an
        </td></tr>
      </table>
    </div>`;

  if (!hasWp) {
    return `<td class="col-pu-wp pu-wp-cell${manualCls}" data-line-id="${line.id}" data-wp-manual="0">
      <span class="pu-wp-val" style="color:#555">—</span></td>`;
  }

  if (excluded) {
    return `<td class="col-pu-wp pu-wp-cell${manualCls}" data-line-id="${line.id}" data-wp-manual="${wpManual ? '1' : '0'}" data-stable-wp="${line.weighted_price}">
      <span class="pu-wp-val has-data">
        <span class="conf-dot conf-${conf}"></span>
        ${fmt(line.weighted_price)}
      </span>
      ${tooltipHtml}
    </td>`;
  }

  return `<td class="col-pu-wp pu-wp-cell${manualCls}" data-line-id="${line.id}" data-wp-manual="${wpManual ? '1' : '0'}" data-stable-wp="${line.weighted_price}">
    <div class="pu-wp-input-row">
      <span class="conf-dot conf-${conf}"></span>
      <input type="text" class="pu-wp-input" value="${fmtForPuInput(line.weighted_price)}" inputmode="decimal" aria-label="PU calculé" />
    </div>
    ${tooltipHtml}
  </td>`;
}

function buildEcartCell(ecart) {
  if (ecart == null) return `<td class="col-ecart" style="color:#555">—</td>`;
  const abs = Math.abs(ecart);
  const cls = abs < 5 ? 'ecart-ok' : abs < 20 ? 'ecart-warn' : 'ecart-bad';
  const sign = ecart > 0 ? '+' : '';
  return `<td class="col-ecart ${cls}">${sign}${ecart.toFixed(1)}%</td>`;
}

// ─── Exclude / Include ────────────────────────────────────────────────────────
async function toggleExclude(lineId) {
  try {
    const r = await fetch(`/api/matching/line/${lineId}/exclude`, { method: 'POST' });
    const d = await r.json();
    if (d.status !== 'ok') throw new Error(d.error || 'Erreur');

    const tr = document.getElementById(`row-${lineId}`);
    const exclu = d.new_status === 'excluded';
    tr.className = exclu ? 'mv-excluded' : '';

    const btn = tr.querySelector('.btn-exclu');
    btn.classList.toggle('is-excluded', exclu);
    btn.textContent = exclu ? '↩ inclure' : '⊘ exclure';
    btn.title       = exclu ? 'Réinclure la ligne' : 'Exclure la ligne';

    const selBtn = document.getElementById(`sel-btn-${lineId}`);
    if (selBtn) { selBtn.disabled = exclu; selBtn.style.opacity = exclu ? '.4' : ''; }

    const line = findLineById(lineId);
    if (line) line.mapping_status = d.new_status;
    const wpTd = tr.querySelector('.col-pu-wp');
    if (line && wpTd) {
      wpTd.outerHTML = buildWpCell(line);
      attachPuWpEditor(tr);
    }

    // Recalcul ratio section
    refreshSectionRatios();
    toast(exclu ? 'Ligne exclue (comptée dans le ratio ✓)' : 'Ligne réincorporée', 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

// ─── Recalcul ratios sections (RÈGLE D'OR — inclut les exclues) ──────────────
function refreshSectionRatios() {
  const sdo = parseFloat(document.getElementById('inp-sdo').value) || _data.sdo;
  document.querySelectorAll('.mv-section').forEach(secEl => {
    const rows  = secEl.querySelectorAll('tbody tr');
    let total   = 0;
    rows.forEach(tr => {
      const tdTot = tr.querySelector('.col-tot-dev');
      if (tdTot) {
        const val = parseFloat(tdTot.textContent.replace(/\s/g, '').replace(',', '.')) || 0;
        total += val;
      }
    });
    const ratio = sdo > 0 ? (total / sdo).toFixed(2) : 0;
    const kpiRatio = secEl.querySelector('.kpi-ratio');
    if (kpiRatio) kpiRatio.textContent = `${ratio} €/m²`;
    const kpiTotal = secEl.querySelector('.kpi-value:not(.kpi-ratio)');
    if (kpiTotal) kpiTotal.textContent = `${fmt(total)} €`;
  });
}

// ─── Modal sélecteur ──────────────────────────────────────────────────────────
async function openModal(lineId, lot, rawDesig) {
  _modalLineId  = lineId;
  _modalLot     = lot;
  _selectedArtId = null;
  _modalLineInfo = null;
  _modalMode = 'suggestions';
  _browseCache = null;
  _browseOptions = null;
  document.getElementById('btn-confirm').disabled = true;

  // Reset UI modes
  document.getElementById('mv-create-form')?.classList.remove('open');
  document.getElementById('btn-back-suggestions')?.classList.remove('show');
  const btnCreateApply = document.getElementById('btn-create-apply');
  if (btnCreateApply) btnCreateApply.style.display = 'none';
  const btnConfirm = document.getElementById('btn-confirm');
  if (btnConfirm) btnConfirm.style.display = '';
  const searchWrap = document.querySelector('.modal-search-wrap');
  if (searchWrap) searchWrap.style.display = '';
  document.getElementById('modal-candidates').style.display = '';
  const openCreateBtn = document.getElementById('btn-open-create');
  if (openCreateBtn) openCreateBtn.style.display = '';
  const synWrap = document.getElementById('syn-check-wrap');
  if (synWrap) synWrap.style.display = '';

  // Remplir infos ligne
  document.getElementById('modal-raw-desig').textContent = rawDesig;

  const trailEl = document.getElementById('modal-base-breadcrumb');
  if (trailEl) {
    const rowLine0 = findLineById(lineId);
    const t0 = formatBaseBreadcrumb(rowLine0, null, null);
    trailEl.textContent = t0;
    trailEl.setAttribute('title', t0);
  }

  // Badge lot
  document.getElementById('modal-lot-badge').outerHTML =
    `<span class="lot-badge lot-${lot}" id="modal-lot-badge">${lot}</span>`;

  // Champ recherche
  const search = document.getElementById('modal-search');
  search.value = '';

  // Ouvrir overlay
  document.getElementById('modal-overlay').classList.add('open');

  // Charger candidats
  document.getElementById('modal-candidates').innerHTML =
    '<div class="modal-loading">⏳ Recherche dans le lot ' + lot + '…</div>';

  try {
    const r = await fetch(`/api/matching/line/${lineId}/candidates`);
    const d = await r.json();
    _allCandidates = d.candidates || [];
    _modalLineInfo = d.line || null;

    if (trailEl) {
      const top = (_allCandidates && _allCandidates.length) ? _allCandidates[0] : null;
      const t1 = formatBaseBreadcrumb(findLineById(lineId), _modalLineInfo, top);
      trailEl.textContent = t1;
      trailEl.setAttribute('title', t1);
    }

    // Affiche chemin devis
    const pathEl = document.getElementById('modal-devis-path');
    if (pathEl) {
      const rawPath = (_modalLineInfo && _modalLineInfo.context_path) ? String(_modalLineInfo.context_path) : '';
      pathEl.textContent = rawPath ? rawPath.replace(/\s>\s/g, ' ➔ ') : '—';
    }

    // Désignation nettoyée (matching) — toujours affichée si non vide, même si identique au brut
    const cleanEl = document.getElementById('modal-clean-desig');
    const cleaned = (d.cleaned_designation || '').trim();
    if (cleaned) {
      cleanEl.style.display = 'block';
      cleanEl.textContent = `→ nettoyé : "${cleaned}"`;
    } else {
      cleanEl.style.display = 'none';
      cleanEl.textContent = '';
    }

    renderCandidates(_allCandidates);
  } catch (e) {
    document.getElementById('modal-candidates').innerHTML =
      `<div class="modal-loading" style="color:#f85149">Erreur : ${e.message}</div>`;
  }
}

function renderCandidates(list) {
  const el = document.getElementById('modal-candidates');
  if (!list.length) {
    const canBrowse = !!(_modalLineId);
    el.innerHTML = `
      <div class="create-cta-box">
        <div class="cta-title">Aucun candidat pertinent dans ce lot.</div>
        <div style="margin:10px 0 14px 0">Tu peux parcourir tous les articles du sous-chapitre (si détecté), ou créer un article personnalisé.</div>
        <div id="browse-box"></div>
        <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;margin-top:10px">
          <button class="btn-create-article" onclick="openCreateArticleForm()">Créer un nouvel article</button>
        </div>
      </div>`;

    if (canBrowse) {
      ensureBrowseOptions().then(() => renderBrowseBox());
    }
    return;
  }
  el.innerHTML = list.map(c => {
    const puStr = c.pu_ht_ref ? `${fmt(c.pu_ht_ref)} €` : '—';
    const breadcrumb = esc((c.breadcrumb && String(c.breadcrumb).trim()) || '');
    const article = esc((c.designation && String(c.designation).trim()) || '');
    const tip = esc((c.path && String(c.path).trim()) || c.designation || '');
    const lot = esc((c.lot && String(c.lot).trim()) || '');
    return `<div class="candidate-row ${c.article_id === _selectedArtId ? 'selected' : ''}"
                 onclick="selectCandidate(${c.article_id}, this)"
                 data-id="${c.article_id}">
      <div class="cand-info">
        <div class="cand-topline">
          ${lot ? `<span class="lot-badge lot-${lot} cand-lot">${lot}</span>` : ''}
          <div class="cand-text">
            ${breadcrumb ? `<div class="cand-breadcrumb" title="${tip}">${breadcrumb}</div>` : ''}
            <div class="cand-article" title="${tip}">${article}</div>
          </div>
        </div>
        <div class="cand-meta">
          <span class="cand-pu">${puStr}</span>
          <span class="cand-unit">${c.unit || ''}</span>
          <span class="cand-type">${c.match_type || ''}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

function filterCandidates() {
  const q = document.getElementById('modal-search').value.toLowerCase().trim();
  const base = (_modalMode === 'browse' && _browseCache) ? _browseCache : _allCandidates;
  if (!q) { renderCandidates(base); return; }
  renderCandidates(base.filter(c => {
    const path = (c.path || '').toLowerCase();
    const bc   = (c.breadcrumb || '').toLowerCase();
    const des  = (c.designation || '').toLowerCase();
    return path.includes(q) || bc.includes(q) || des.includes(q);
  }));
}

function selectCandidate(artId, rowEl) {
  _selectedArtId = artId;
  document.querySelectorAll('.candidate-row').forEach(r => r.classList.remove('selected'));
  rowEl.classList.add('selected');
  document.getElementById('btn-confirm').disabled = false;
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  _modalLineId = null; _modalLot = null; _selectedArtId = null; _modalLineInfo = null; _modalMode = 'suggestions';
  _browseCache = null;
  const btnCreateApply = document.getElementById('btn-create-apply');
  if (btnCreateApply) btnCreateApply.style.display = 'none';
  const btnConfirm = document.getElementById('btn-confirm');
  if (btnConfirm) btnConfirm.style.display = '';
  const synWrap = document.getElementById('syn-check-wrap');
  if (synWrap) synWrap.style.display = '';
}

async function browseSectionArticles() {
  if (!_modalLineId) return;
  try {
    // IMPORTANT: lire la sélection AVANT de modifier le DOM (sinon <select> supprimé)
    const lot = getSelectedBrowseLot() || (_modalLineInfo && _modalLineInfo.lot) || _modalLot || 'CFO';
    const choice = getSelectedBrowseSection();
    if (!choice) { toast('Choisir un sous-chapitre.', 'err'); return; }
    const parts = choice.split('|||');
    const chapter = (parts[0] || '').trim();
    const section = (parts[1] || '').trim();
    if (!chapter || !section) { toast('Choisir un sous-chapitre.', 'err'); return; }

    // feedback (après lecture sélection)
    const el = document.getElementById('modal-candidates');
    if (el) el.innerHTML = '<div class="modal-loading">⏳ Chargement du sous-chapitre…</div>';

    const qs = new URLSearchParams();
    if (lot) qs.set('lot', lot);
    if (chapter) qs.set('chapter', chapter);
    if (section) qs.set('section', section);
    const url = `/api/matching/line/${_modalLineId}/section_articles?` + qs.toString();
    const r = await fetch(url);
    const d = await r.json();
    _browseCache = d.articles || [];
    _modalMode = 'browse';
    // reset search to make browsing easier
    const search = document.getElementById('modal-search');
    if (search) search.value = '';
    renderCandidates(_browseCache);
    if (!_browseCache.length) {
      toast(`Aucun article trouvé pour "${chapter} ➔ ${section}" (lot ${lot}).`, 'err');
    }
  } catch (e) {
    toast(e.message || 'Erreur', 'err');
  }
}

async function ensureBrowseOptions() {
  if (_browseOptions || !_modalLineId) return;
  try {
    const r = await fetch(`/api/matching/line/${_modalLineId}/browse_options`);
    const d = await r.json();
    _browseOptions = d;
  } catch {
    _browseOptions = { chapters: [], sections: [], selected_chapter: null };
  }
}

function renderBrowseBox() {
  const box = document.getElementById('browse-box');
  if (!box) return;
  const opt = _browseOptions || {};
  const sectionChoices = opt.section_choices || [];
  const lot = (opt.lot || (_modalLineInfo && _modalLineInfo.lot) || _modalLot || 'CFO');

  const secOptions = ['<option value="">— Choisir un sous-chapitre —</option>'].concat(
    sectionChoices.map(o => {
      const v = `${o.chapter}|||${o.section}`;
      return `<option value="${esc(v)}">${esc(o.label)}</option>`;
    })
  ).join('');

  box.innerHTML = `
    <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
      <select id="sel-browse-lot" style="background:#1a2d38;border:1px solid rgba(255,255,255,.15);border-radius:6px;color:#eee;padding:8px 10px;min-width:120px" onchange="onBrowseLotChange()">
        <option value="CFO" ${lot === 'CFO' ? 'selected' : ''}>CFO</option>
        <option value="CFA" ${lot === 'CFA' ? 'selected' : ''}>CFA</option>
        <option value="PV"  ${lot === 'PV'  ? 'selected' : ''}>PV</option>
      </select>
      <select id="sel-browse-section" style="background:#1a2d38;border:1px solid rgba(255,255,255,.15);border-radius:6px;color:#eee;padding:8px 10px;min-width:420px" onchange="onBrowseSectionChange()">
        ${secOptions}
      </select>
      <button class="btn-create-article" onclick="browseSectionArticles()">Afficher</button>
    </div>
    <div style="font-size:11px;color:#6f7f88;margin-top:8px">
      Si le devis nomme le sous-chapitre différemment de la base, choisis-le ici pour parcourir les articles correspondants.
    </div>`;
}

async function onBrowseLotChange() {
  const lot = getSelectedBrowseLot();
  if (!_modalLineId || !lot) return;
  try {
    const r = await fetch(`/api/matching/line/${_modalLineId}/browse_options?` + new URLSearchParams({ lot }).toString());
    const d = await r.json();
    _browseOptions = d;
  } catch {}
  renderBrowseBox();
}

function getSelectedBrowseSection() {
  const el = document.getElementById('sel-browse-section');
  return el ? (el.value || '').trim() : '';
}
function getSelectedBrowseLot() {
  const el = document.getElementById('sel-browse-lot');
  return el ? (el.value || '').trim() : '';
}

function onBrowseSectionChange() {
  const el = document.getElementById('sel-browse-section');
}

function enableDraggableModal() {
  const overlay = document.getElementById('modal-overlay');
  const modal = overlay ? overlay.querySelector('.mv-modal') : null;
  const header = overlay ? overlay.querySelector('.mv-modal-header') : null;
  if (!overlay || !modal || !header) return;

  let dragging = false;
  let startX = 0, startY = 0;
  let startLeft = 0, startTop = 0;

  function onMove(e) {
    if (!dragging) return;
    const x = e.clientX;
    const y = e.clientY;
    const dx = x - startX;
    const dy = y - startY;
    modal.style.left = `${startLeft + dx}px`;
    modal.style.top = `${startTop + dy}px`;
  }

  function onUp() {
    dragging = false;
    window.removeEventListener('mousemove', onMove);
    window.removeEventListener('mouseup', onUp);
  }

  header.addEventListener('mousedown', (e) => {
    // only when modal open
    if (!overlay.classList.contains('open')) return;
    // ignore click on close button
    if ((e.target && e.target.closest && e.target.closest('.btn-close-modal'))) return;
    const rect = modal.getBoundingClientRect();
    modal.style.position = 'fixed';
    modal.style.margin = '0';
    modal.style.left = `${rect.left}px`;
    modal.style.top = `${rect.top}px`;
    modal.style.transform = 'none';

    dragging = true;
    startX = e.clientX;
    startY = e.clientY;
    startLeft = rect.left;
    startTop = rect.top;

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  });
}

function openCreateArticleForm() {
  _modalMode = 'create';
  document.getElementById('btn-confirm').disabled = true;
  document.getElementById('btn-back-suggestions')?.classList.add('show');
  const btnCreateApply = document.getElementById('btn-create-apply');
  if (btnCreateApply) btnCreateApply.style.display = '';
  const btnConfirm = document.getElementById('btn-confirm');
  if (btnConfirm) btnConfirm.style.display = 'none';
  const synWrap = document.getElementById('syn-check-wrap');
  if (synWrap) synWrap.style.display = 'none';

  // Masquer recherche + suggestions
  const searchWrap = document.querySelector('.modal-search-wrap');
  if (searchWrap) searchWrap.style.display = 'none';
  document.getElementById('modal-candidates').style.display = 'none';
  const openCreateBtn = document.getElementById('btn-open-create');
  if (openCreateBtn) openCreateBtn.style.display = 'none';

  // Ouvrir form
  const form = document.getElementById('mv-create-form');
  form?.classList.add('open');

  const lot = (_modalLineInfo && _modalLineInfo.lot) ? _modalLineInfo.lot : _modalLot;
  const ctx = (_modalLineInfo && _modalLineInfo.context_path) ? _modalLineInfo.context_path : '';
  const chapter = (_modalLineInfo && _modalLineInfo.chapter) ? _modalLineInfo.chapter : '';
  const section = (_modalLineInfo && _modalLineInfo.section) ? _modalLineInfo.section : '';
  const unit = (_modalLineInfo && _modalLineInfo.unit) ? _modalLineInfo.unit : 'u';
  const pu = (_modalLineInfo && _modalLineInfo.unit_price_ht != null) ? _modalLineInfo.unit_price_ht : '';

  // context display with ➔
  const ctxNice = (ctx || '').replace(/\s>\s/g, ' ➔ ');

  document.getElementById('mv-create-lot').innerHTML =
    `<span class="lot-badge lot-${esc(lot)}">${esc(lot)}</span>`;
  document.getElementById('mv-create-context').textContent = ctxNice || '—';

  const rawDesig = document.getElementById('modal-raw-desig').textContent || '';
  const cleaned = document.getElementById('modal-clean-desig').textContent || '';
  const pref = (cleaned && cleaned.includes('→ nettoyé :')) ? cleaned.replace('→ nettoyé :', '').replace(/"/g, '').trim() : rawDesig;

  document.getElementById('inp-create-designation').value = pref || '';
  document.getElementById('inp-create-unit').value = unit || 'u';
  document.getElementById('inp-create-pu').value = (pu === '' || pu == null) ? '' : String(pu).replace('.', ',');
  document.getElementById('inp-create-chapter').value = chapter || '';
  document.getElementById('inp-create-section').value = section || '';

  hideCreateError();
}

function backToSuggestions() {
  _modalMode = 'suggestions';
  document.getElementById('btn-back-suggestions')?.classList.remove('show');
  const btnCreateApply = document.getElementById('btn-create-apply');
  if (btnCreateApply) btnCreateApply.style.display = 'none';
  const btnConfirm = document.getElementById('btn-confirm');
  if (btnConfirm) btnConfirm.style.display = '';
  const synWrap = document.getElementById('syn-check-wrap');
  if (synWrap) synWrap.style.display = '';

  const searchWrap = document.querySelector('.modal-search-wrap');
  if (searchWrap) searchWrap.style.display = '';
  document.getElementById('modal-candidates').style.display = '';

  document.getElementById('mv-create-form')?.classList.remove('open');
  const openCreateBtn = document.getElementById('btn-open-create');
  if (openCreateBtn) openCreateBtn.style.display = '';

  hideCreateError();
}

function showCreateError(msg) {
  const el = document.getElementById('mv-create-error');
  if (!el) return;
  el.textContent = msg;
  el.classList.add('show');
}

function hideCreateError() {
  const el = document.getElementById('mv-create-error');
  if (!el) return;
  el.textContent = '';
  el.classList.remove('show');
}

async function createAndApplyArticle() {
  if (!_modalLineId) return;
  const designation = (document.getElementById('inp-create-designation').value || '').trim();
  const unit = (document.getElementById('inp-create-unit').value || '').trim();
  const puRaw = (document.getElementById('inp-create-pu').value || '').trim();
  const chapter = (document.getElementById('inp-create-chapter').value || '').trim();
  const section = (document.getElementById('inp-create-section').value || '').trim();

  if (!designation) { showCreateError('La désignation est obligatoire.'); return; }
  if (!unit) { showCreateError('L’unité est obligatoire.'); return; }
  if (!chapter) { showCreateError('Le chapitre est requis pour créer un article.'); return; }

  let puVal = 0;
  if (puRaw) {
    const n = parseFloat(puRaw.replace(/\s/g,'').replace(',', '.'));
    if (isNaN(n) || n < 0) { showCreateError('Le PU doit être un nombre positif.'); return; }
    puVal = n;
  }

  hideCreateError();

  try {
    const btnCreateApply = document.getElementById('btn-create-apply');
    if (btnCreateApply) btnCreateApply.disabled = true;
    const r = await fetch(`/api/matching/line/${_modalLineId}/create_article`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ designation, unit, pu_ht: puVal, chapter, section }),
    });
    const d = await r.json();
    if (!r.ok || d.status !== 'ok') throw new Error(d.error || 'Erreur serveur');

    updateRowAfterSelect(_modalLineId, d);
    toast('✓ Article créé et appliqué à la ligne devis.', 'ok');
    closeModal();
    refreshStats();
  } catch (e) {
    showCreateError(e.message);
  } finally {
    const btnCreateApply = document.getElementById('btn-create-apply');
    if (btnCreateApply) btnCreateApply.disabled = false;
  }
}

async function confirmSelection() {
  if (!_selectedArtId || !_modalLineId) return;

  const memorize    = document.getElementById('chk-memorize').checked;
  const rawDesig    = document.getElementById('modal-raw-desig').textContent || '';
  const cleanedText = rawDesig; // le backend applique clean_designation côté engine

  try {
    const r = await fetch(`/api/matching/line/${_modalLineId}/select`, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        dpgf_article_id: _selectedArtId,
        memorize_synonym: memorize,
        cleaned_term: cleanedText,
      }),
    });
    const d = await r.json();
    if (d.status !== 'ok') throw new Error(d.error || 'Erreur serveur');

    // Mise à jour de la ligne dans le DOM
    updateRowAfterSelect(_modalLineId, d);
    if (memorize) toast('✓ Synonyme mémorisé — score amélioré pour les prochains matchings', 'ok');
    else toast('✓ Article sélectionné', 'ok');
    closeModal();
    refreshStats();
  } catch (e) { toast(e.message, 'err'); }
}

function updateRowAfterSelect(lineId, d) {
  const tr = document.getElementById(`row-${lineId}`);
  if (!tr) return;

  const line = findLineById(lineId);
  if (line) {
    line.weighted_price = d.weighted_price;
    line.wp_tooltip = d.wp_tooltip;
    line.ecart_pct = d.ecart_pct;
    line.wp_manual = !!d.wp_manual;
    if (d.base_designation) {
      line.base_designation = d.base_designation;
      line.base_pu = d.base_pu;
    }
    if (d.base_chapter !== undefined) line.base_chapter = d.base_chapter;
    if (d.base_section !== undefined) line.base_section = d.base_section;
  }

  // Sélecteur base
  const selBtn = document.getElementById(`sel-btn-${lineId}`);
  if (selBtn && d.base_designation) {
    selBtn.classList.add('mapped');
    const full = d.base_designation;
    selBtn.title = full;
    const tdBase = selBtn.closest('td.col-base');
    if (tdBase) tdBase.title = full;
    selBtn.querySelector('.sel-text').textContent = d.base_designation || '';
  }

  // PU base
  const puBaseCell = document.getElementById(`pu-base-${lineId}`);
  if (puBaseCell && d.base_pu != null) puBaseCell.textContent = fmt(d.base_pu);

  // PU calculé
  const wpCell = tr.querySelector('.col-pu-wp');
  if (wpCell) {
    const ms = tr.classList.contains('mv-excluded')
      ? 'excluded'
      : (line && line.mapping_status) || 'manual';
    const fakeLine = {
      id: lineId,
      weighted_price: d.weighted_price,
      wp_tooltip: d.wp_tooltip,
      ecart_pct: d.ecart_pct,
      wp_manual: !!d.wp_manual,
      mapping_status: ms,
    };
    wpCell.outerHTML = buildWpCell(fakeLine);
    attachPuWpEditor(tr);
  }

  // Écart
  const ecartCell = tr.querySelector('.col-ecart');
  if (ecartCell) ecartCell.outerHTML = buildEcartCell(d.ecart_pct);

  // Status (implicit update via class)
  tr.className = '';
}

function refreshStats() {
  // Recount from DOM
  const rows   = document.querySelectorAll('tbody tr');
  let total=0, mapped=0, pending=0, exclu=0;
  rows.forEach(tr => {
    total++;
    if (tr.classList.contains('mv-excluded')) exclu++;
    else {
      const btn = tr.querySelector('.btn-exclu');
      const sel = tr.querySelector('.btn-selector');
      if (sel && sel.classList.contains('mapped')) mapped++;
      else pending++;
    }
  });
  document.getElementById('mv-stats').innerHTML =
    `${total} lignes · <span style="color:#3fb950">${mapped} mappées</span> · ` +
    `<span style="color:#d29922">${pending} en attente</span> · ` +
    `<span style="color:#666">${exclu} exclues</span>`;
  document.getElementById('btn-validate').disabled = mapped === 0;
}

// ─── Validation finale ────────────────────────────────────────────────────────
async function validateProject() {
  if (!confirm('Valider et intégrer ce projet en base ?\n\nLes prix pondérés seront utilisés pour le référentiel.')) return;
  try {
    const r = await fetch(`/api/matching/${PROJECT_ID}/validate`, { method: 'POST' });
    const d = await r.json();
    if (d.status === 'ok') {
      toast('✓ Projet validé et intégré en base', 'ok');
      setTimeout(() => window.location.href = '/import', 1500);
    }
  } catch (e) { toast(e.message, 'err'); }
}

// ─── Utilitaires ─────────────────────────────────────────────────────────────
function fmt(v) {
  if (v == null || isNaN(v)) return '—';
  return new Intl.NumberFormat('fr-FR', { minimumFractionDigits:2, maximumFractionDigits:2 }).format(v);
}
function fmtQty(v) {
  if (v == null) return '—';
  return new Intl.NumberFormat('fr-FR', { maximumFractionDigits:1 }).format(v);
}
function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
                  .replace(/"/g,'&quot;');
}
function trunc(s, n) { return s && s.length > n ? s.slice(0, n) + '…' : (s || ''); }
function sanitizeId(s) { return (s || '').replace(/[^a-z0-9]/gi, '_'); }

let _toastTimer = null;
function toast(msg, type = 'ok') {
  const el = document.getElementById('mv-toast');
  el.textContent  = msg;
  el.className    = `mv-toast show ${type}`;
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), 3200);
}

// Fermeture modal sur clic overlay
document.getElementById('modal-overlay')?.addEventListener('click', e => {
  if (e.target === document.getElementById('modal-overlay')) closeModal();
});
