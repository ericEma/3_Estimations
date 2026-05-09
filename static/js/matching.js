'use strict';

// ─── État global ──────────────────────────────────────────────────────────────
let _data         = null;   // réponse complète de /api/matching/<id>/data
let _modalLineId  = null;   // id ligne en cours d'édition dans le modal
let _modalLot     = null;
let _allCandidates = [];    // candidats chargés pour la ligne courante
let _selectedArtId = null;  // article sélectionné dans le modal

// ─── Init ─────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  if (PROJECT_ID) loadData();
});

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
        if (l.row_type === 'article') {
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
      <th>PU Base</th><th>PU Calculé ⓘ</th><th>Écart</th><th>Actions</th>
    </tr></thead>
    <tbody id="tbody-${sanitizeId(sec.name)}"></tbody>`;

  el.appendChild(wrap);
  wrap.appendChild(table);

  const tbody = table.querySelector('tbody');
  for (const line of sec.lines) {
    if (line.row_type === 'article') tbody.appendChild(renderRow(line, chap_lot));
  }
  return el;
}

function renderRow(line, chap_lot) {
  const tr = document.createElement('tr');
  tr.id = `row-${line.id}`;
  tr.className = line.mapping_status === 'excluded' ? 'mv-excluded' : '';

  const lot  = line.lot || chap_lot;
  const exclu = line.mapping_status === 'excluded';

  // Col 1 — Désignation devis
  const desig = line.original_designation || '—';
  const td1 = `<td class="col-desig" title="${esc(desig)}">
    <span class="cell-desig-text">${esc(trunc(desig, 35))}</span>
    <span class="lot-badge lot-${lot}" style="margin-left:4px">${lot}</span>
  </td>`;

  // Col 2–5 — Numériques
  const td2 = `<td class="col-unit">${esc(line.unit || '—')}</td>`;
  const td3 = `<td class="col-qty">${line.quantity != null ? fmtQty(line.quantity) : '—'}</td>`;
  const td4 = `<td class="col-pu-dev">${line.unit_price_ht != null ? fmt(line.unit_price_ht) : '—'}</td>`;
  const td5 = `<td class="col-tot-dev" style="font-weight:600">${line.total_ht != null ? fmt(line.total_ht) : '—'}</td>`;

  // Col 6 — Sélecteur base
  const hasMapped = !!line.base_designation;
  const selText = hasMapped ? trunc(line.base_designation, 28) : '⊕ Sélectionner…';
  const td6 = `<td class="col-base">
    <button class="btn-selector ${hasMapped ? 'mapped' : ''}"
            onclick="openModal(${line.id}, '${esc(lot)}', '${esc(desig.replace(/'/g,"\\'")}'))"
            id="sel-btn-${line.id}" ${exclu ? 'disabled style="opacity:.4;cursor:default"' : ''}>
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
  return tr;
}

function buildWpCell(line) {
  if (!line.weighted_price) {
    return `<td class="col-pu-wp pu-wp-cell"><span class="pu-wp-val" style="color:#555">—</span></td>`;
  }
  const conf  = (line.wp_tooltip && line.wp_tooltip.confidence) || 'NONE';
  const tt    = line.wp_tooltip || {};
  const baseAge  = tt.base_age_years  != null ? tt.base_age_years.toFixed(1) + 'a'  : '?';
  const devAge   = tt.devis_age_years != null ? tt.devis_age_years.toFixed(1) + 'a' : '?';
  const baseW    = tt.base_weight  != null ? tt.base_weight.toFixed(2)  : '?';
  const devW     = tt.devis_weight != null ? tt.devis_weight.toFixed(2) : '?';
  const baseAct  = tt.base_actualized  != null ? fmt(tt.base_actualized)  : '—';
  const devisAct = tt.devis_actualized != null ? fmt(tt.devis_actualized) : '—';

  const tooltipHtml = `
    <div class="mv-tooltip">
      <table>
        <tr><td class="t-label">Base actualisée</td>
            <td class="t-val">${baseAct} € <span style="color:#888">(âge ${baseAge} · P=${baseW})</span></td></tr>
        <tr><td class="t-label">Devis actualisé</td>
            <td class="t-val">${devisAct} € <span style="color:#888">(âge ${devAge} · P=${devW})</span></td></tr>
        <tr><td class="t-label t-result">Prix pondéré</td>
            <td class="t-val t-result">${fmt(line.weighted_price)} €</td></tr>
        <tr><td colspan="2" style="color:#666;font-size:10px;padding-top:4px">
          Confiance : <span class="conf-dot conf-${conf}" style="display:inline-block;margin:0 3px"></span>${conf}
          · Taux actualisation 3%/an
        </td></tr>
      </table>
    </div>`;

  return `<td class="col-pu-wp pu-wp-cell">
    <span class="pu-wp-val has-data">
      <span class="conf-dot conf-${conf}"></span>
      ${fmt(line.weighted_price)}
    </span>
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
  document.getElementById('btn-confirm').disabled = true;

  // Remplir infos ligne
  document.getElementById('modal-raw-desig').textContent = rawDesig;

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

    // Afficher désignation nettoyée si différente
    const cleanEl = document.getElementById('modal-clean-desig');
    const cleaned = _allCandidates[0]?._cleaned || '';
    cleanEl.style.display = cleaned && cleaned !== rawDesig ? 'block' : 'none';
    cleanEl.textContent = cleaned ? `→ nettoyé : "${cleaned}"` : '';

    renderCandidates(_allCandidates);
  } catch (e) {
    document.getElementById('modal-candidates').innerHTML =
      `<div class="modal-loading" style="color:#f85149">Erreur : ${e.message}</div>`;
  }
}

function renderCandidates(list) {
  const el = document.getElementById('modal-candidates');
  if (!list.length) {
    el.innerHTML = '<div class="modal-loading">Aucun candidat trouvé dans ce lot.</div>';
    return;
  }
  el.innerHTML = list.map(c => {
    const scoreClass = c.score >= 80 ? 'score-high' : c.score >= 50 ? 'score-medium' : 'score-low';
    const puStr = c.pu_ht_ref ? `${fmt(c.pu_ht_ref)} €` : '—';
    return `<div class="candidate-row ${c.article_id === _selectedArtId ? 'selected' : ''}"
                 onclick="selectCandidate(${c.article_id}, this)"
                 data-id="${c.article_id}">
      <span class="cand-score ${scoreClass}">${Math.round(c.score)}</span>
      <div class="cand-info">
        <div class="cand-desig" title="${esc(c.designation)}">${esc(c.designation)}</div>
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
  if (!q) { renderCandidates(_allCandidates); return; }
  renderCandidates(_allCandidates.filter(c =>
    c.designation.toLowerCase().includes(q)
  ));
}

function selectCandidate(artId, rowEl) {
  _selectedArtId = artId;
  document.querySelectorAll('.candidate-row').forEach(r => r.classList.remove('selected'));
  rowEl.classList.add('selected');
  document.getElementById('btn-confirm').disabled = false;
}

function closeModal() {
  document.getElementById('modal-overlay').classList.remove('open');
  _modalLineId = null; _modalLot = null; _selectedArtId = null;
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

  // Sélecteur base
  const selBtn = document.getElementById(`sel-btn-${lineId}`);
  if (selBtn && d.base_designation) {
    selBtn.classList.add('mapped');
    selBtn.querySelector('.sel-text').textContent = trunc(d.base_designation, 28);
  }

  // PU base
  const puBaseCell = document.getElementById(`pu-base-${lineId}`);
  if (puBaseCell && d.base_pu != null) puBaseCell.textContent = fmt(d.base_pu);

  // PU calculé
  const wpCell = tr.querySelector('.col-pu-wp');
  if (wpCell) {
    const fakeLine = { weighted_price: d.weighted_price, wp_tooltip: d.wp_tooltip, ecart_pct: d.ecart_pct };
    wpCell.outerHTML = buildWpCell(fakeLine);
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
