/**
 * affaire.js — Calculateur DPGF interactif
 * Sprint 4 : Estimation Élec
 */

'use strict';

// ── État global ───────────────────────────────────────────────────────────────

const AFFAIRE_ID = window.AFFAIRE_ID || null;
const RATIO_REFS = window.RATIO_REFS || {};   // {article_id: avg_pu_actualise}

// Sprint 7 — unités fixes : ne varient PAS avec la SDO
const SDO_FIXED_UNITS = (window.SDO_FIXED_UNITS || ['u','ens','ml','U','ENS','ML'])
    .map(u => u.toLowerCase());

// Sprint 7 — état des chapitres / sections (checkbox + mode Macro/Détail)
const CHAPTER_STATE  = window.CHAPTER_STATE || {};
let chapterStateTimer = null;

const DEVIATION_WARN  = 0.20;
const DEVIATION_ALERT = 0.40;

// Paramètres mutables (synchronisés avec la DB via /params)
let currentSDO         = window.AFFAIRE_SDO         || 1000;
let currentKVA         = window.AFFAIRE_KVA         || 800;
let currentPhase       = window.AFFAIRE_PHASE        || 'APD';
let tauxPhase          = window.AFFAIRE_TAUX_PHASE   ?? 3;
let tauxIncertitude    = window.AFFAIRE_INCERTITUDE  ?? 3;
let tauxRisque         = window.AFFAIRE_RISQUE       ?? 1;
let complexity         = Object.assign({ cfo: 1, cfa: 1, pv: 1 },
                                       window.AFFAIRE_COMPLEXITY || {});
let currentTotal       = 0;   // total HT effectif (ratio fallback inclus) — envoyé à la sauvegarde

// Paliers Egis : le choix de phase PRESET le Taux Phase (éditable ensuite)
const PHASE_PRESETS = { DIAG: 6, APS: 4, APD: 3, PRO: 1 };

let isDirty          = false;
let sdoDebounceTimer = null;
let paramsTimer      = null;

// ── Initialisation ────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', () => {
    initChapterToggles();
    initInputListeners();
    initCheckboxListeners();
    initChapterCheckListeners();   // Sprint 7
    initParamsListeners();
    applyMacroNeutralization();    // Sprint 7 : griser articles si mode Macro
    updateAllTotals();

    // Raccourci clavier : Ctrl+S pour sauvegarder
    document.addEventListener('keydown', e => {
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {
            e.preventDefault();
            saveAffaire();
        }
    });

    // Alerte si l'utilisateur quitte sans sauvegarder
    window.addEventListener('beforeunload', e => {
        if (isDirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
});


// ── Chapters : collapse / expand ─────────────────────────────────────────────

function initChapterToggles() {
    document.querySelectorAll('.chapter-toggle').forEach(toggle => {
        toggle.addEventListener('click', () => toggleChapter(toggle));
    });
}

function toggleChapter(toggle) {
    const chapterId = toggle.closest('tr').dataset.chapterId;
    const isCollapsed = toggle.classList.toggle('collapsed');

    document.querySelectorAll(`[data-chapter-id="${chapterId}"]`).forEach(row => {
        if (!row.classList.contains('row-chapter')) {
            row.style.display = isCollapsed ? 'none' : '';
        }
    });
}

function collapseAll() {
    document.querySelectorAll('.chapter-toggle:not(.collapsed)').forEach(toggleChapter);
}

function expandAll() {
    document.querySelectorAll('.chapter-toggle.collapsed').forEach(toggle => {
        toggle.classList.remove('collapsed');
        const chapterId = toggle.closest('tr').dataset.chapterId;
        document.querySelectorAll(`[data-chapter-id="${chapterId}"]`).forEach(row => {
            row.style.display = '';
        });
    });
}

/** Sprint 7 — 3 modes d'affichage :
 *   mode 1 : uniquement têtes de chapitres (CFO / CFA / PV)
 *   mode 2 : têtes de chapitres + titres de sous-chapitres
 *   mode 3 : toutes les lignes
 */
function setViewMode(mode) {
    document.querySelectorAll('.row-chapter').forEach(r => r.style.display = '');
    document.querySelectorAll('.row-section').forEach(r => r.style.display = (mode === 1) ? 'none' : '');
    document.querySelectorAll('.row-article').forEach(r => r.style.display = (mode === 3) ? '' : 'none');

    // MAJ état visuel des boutons (primary vs secondary)
    [1,2,3].forEach(m => {
        const btn = document.getElementById(`btn-mode-${m}`);
        if (!btn) return;
        btn.classList.toggle('btn-primary',   m === mode);
        btn.classList.toggle('btn-secondary', m !== mode);
    });
}


// ── Listeners ─────────────────────────────────────────────────────────────────

function initInputListeners() {
    // Unités éditables (Sprint 7) — marque la ligne comme "manual" et met à jour
    // data-unit pour la décorrélation SDO.
    document.querySelectorAll('.input-unit').forEach(input => {
        input.addEventListener('change', () => {
            const tr = input.closest('tr');
            tr.dataset.unit = input.value.trim() || 'u';
            input.classList.add('input-manual');
            input.dataset.source = 'manual';
            isDirty = true;
        });
        input.addEventListener('focus', () => input.select());
    });

    document.querySelectorAll('.input-qty, .input-pu').forEach(input => {
        input.addEventListener('input', () => {
            computeRow(input.closest('tr'));
            isDirty = true;
        });

        // Détection de saisie manuelle → marquer le champ
        input.addEventListener('change', () => {
            input.classList.add('input-manual');
            input.dataset.source = 'manual';

            // Si c'est un PU qui change, proposer la correction
            if (input.classList.contains('input-pu')) {
                const articleId = parseInt(input.closest('tr').dataset.articleId);
                const ratioRef  = parseFloat(RATIO_REFS[articleId] || 0);
                const newVal    = parseFloat(input.value) || 0;
                if (ratioRef > 0 && Math.abs(newVal - ratioRef) / ratioRef > DEVIATION_WARN) {
                    showCorrectionPopup(input, articleId, newVal, ratioRef);
                }
            }
        });

        input.addEventListener('focus', () => {
            input.select();
        });
    });

    // Tooltip sur prix unitaire (visible uniquement si ratio de référence connu)
    document.querySelectorAll('.input-pu').forEach(input => {
        const artId = parseInt(input.closest('tr')?.dataset.articleId);
        if (artId && RATIO_REFS[artId]) {
            input.title = 'Source : PSA Urgences 2024 — Actualisé 2026 (+6.5%)';
        }
    });
}

function initCheckboxListeners() {
    document.querySelectorAll('.line-check').forEach(cb => {
        cb.addEventListener('change', () => {
            const row = cb.closest('tr');
            row.classList.toggle('excluded', !cb.checked);
            updateAllTotals();
            isDirty = true;
        });
    });
}


// ── Sprint 7 : Checkbox chapitre/section + mode Macro ─────────────────────────

function initChapterCheckListeners() {
    // Checkbox chapitre : coche/décoche tout le bloc d'articles enfants
    document.querySelectorAll('.row-chapter .chapter-check').forEach(cb => {
        // empêche le clic sur la checkbox de propager au toggle expand/collapse
        cb.addEventListener('click', e => e.stopPropagation());
        cb.addEventListener('change', () => {
            const tr       = cb.closest('tr');
            const chapId   = tr.dataset.chapterId;
            const key      = tr.dataset.chapterKey;
            const included = cb.checked;

            if (!CHAPTER_STATE[key]) CHAPTER_STATE[key] = {};
            CHAPTER_STATE[key].is_included = included;

            // Propage aux sections et articles du chapitre
            document.querySelectorAll(`.row-section[data-chapter-id="${chapId}"] .section-check`)
                .forEach(sc => {
                    sc.checked = included;
                    const skey = sc.closest('tr').dataset.sectionKey;
                    if (!CHAPTER_STATE[skey]) CHAPTER_STATE[skey] = {};
                    CHAPTER_STATE[skey].is_included = included;
                });
            document.querySelectorAll(`.row-article[data-chapter-id="${chapId}"] .line-check`)
                .forEach(ac => {
                    ac.checked = included;
                    ac.closest('tr').classList.toggle('excluded', !included);
                });

            updateAllTotals();
            scheduleChapterStateSave();
            isDirty = true;
        });
    });

    // Checkbox section : coche/décoche les articles de la section uniquement
    document.querySelectorAll('.row-section .section-check').forEach(cb => {
        cb.addEventListener('click', e => e.stopPropagation());
        cb.addEventListener('change', () => {
            const sec  = cb.closest('tr');
            const key  = sec.dataset.sectionKey;
            if (!CHAPTER_STATE[key]) CHAPTER_STATE[key] = {};
            CHAPTER_STATE[key].is_included = cb.checked;

            // Propage aux articles directement suivants (jusqu'à section/chapitre suivante)
            let next = sec.nextElementSibling;
            while (next && !next.classList.contains('row-section') && !next.classList.contains('row-chapter')) {
                if (next.classList.contains('row-article')) {
                    const ac = next.querySelector('.line-check');
                    if (ac) {
                        ac.checked = cb.checked;
                        next.classList.toggle('excluded', !cb.checked);
                    }
                }
                next = next.nextElementSibling;
            }
            updateAllTotals();
            scheduleChapterStateSave();
            isDirty = true;
        });
    });
}

/** Bascule un chapitre entre mode Macro (ratio €/m²) et Détail (somme articles). */
function toggleChapterMode(chapId) {
    const tr  = document.querySelector(`.row-chapter[data-chapter-id="${chapId}"]`);
    if (!tr) return;
    const key = tr.dataset.chapterKey;
    const state = CHAPTER_STATE[key] || {};
    state.use_macro = !state.use_macro;
    CHAPTER_STATE[key] = state;

    const badge = document.getElementById(`chapter-mode-${chapId}`);
    if (badge) {
        badge.textContent = state.use_macro ? 'MACRO' : 'DÉTAIL';
        badge.classList.toggle('mode-macro',  state.use_macro);
        badge.classList.toggle('mode-detail', !state.use_macro);
    }
    applyMacroNeutralization();
    updateAllTotals();
    scheduleChapterStateSave();
    isDirty = true;
}

/** Grise visuellement les articles des chapitres en mode Macro. */
function applyMacroNeutralization() {
    document.querySelectorAll('.row-chapter').forEach(tr => {
        const chapId = tr.dataset.chapterId;
        const key    = tr.dataset.chapterKey;
        const state  = CHAPTER_STATE[key] || { use_macro: false };
        document.querySelectorAll(`.row-article[data-chapter-id="${chapId}"]`).forEach(art => {
            art.classList.toggle('macro-neutralized', !!state.use_macro);
        });
    });
}

function scheduleChapterStateSave() {
    clearTimeout(chapterStateTimer);
    chapterStateTimer = setTimeout(saveChapterState, 600);
}

function saveChapterState() {
    const settings = Object.entries(CHAPTER_STATE).map(([key, s]) => ({
        chapter_key:       key,
        is_included:       !!s.is_included,
        use_macro:         !!s.use_macro,
        qty:               s.qty || 1,
        ratio_m2_override: s.ratio_m2_override || null,
    }));
    fetch(`/api/affaire/${AFFAIRE_ID}/chapter_settings`, {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify({ settings }),
    }).catch(() => {});
}


// ── Calculs ───────────────────────────────────────────────────────────────────

function computeRow(row) {
    if (!row) return;

    const qty    = parseFloat(row.querySelector('.input-qty')?.value || 0) || 0;
    const pu     = parseFloat(row.querySelector('.input-pu')?.value  || 0) || 0;
    const total  = qty * pu;
    const artId  = parseInt(row.dataset.articleId);
    const ratioRef = parseFloat(RATIO_REFS[artId] || pu);

    // Mise à jour du total affiché
    const cellTotal = row.querySelector('.cell-total');
    if (cellTotal) cellTotal.textContent = formatEur(total);

    // Mise à jour de la déviance
    updateDeviation(row, pu, ratioRef);

    updateAllTotals();
}

function updateDeviation(row, pu, ratioRef) {
    const devCell = row.querySelector('.dev-badge');
    if (!devCell || !ratioRef || ratioRef === 0) {
        if (devCell) { devCell.textContent = '—'; devCell.className = 'dev-badge dev-neutral'; }
        return;
    }

    const dev    = (pu - ratioRef) / ratioRef;
    const devPct = (dev * 100).toFixed(1);
    const sign   = dev >= 0 ? '+' : '';

    devCell.textContent = `${sign}${devPct}%`;

    if (Math.abs(dev) <= DEVIATION_WARN) {
        devCell.className = 'dev-badge dev-ok';
    } else if (Math.abs(dev) <= DEVIATION_ALERT) {
        devCell.className = 'dev-badge dev-warn';
    } else {
        devCell.className = 'dev-badge dev-alert';
    }
}

function updateAllTotals() {
    const totals           = { CFO: 0, CFA: 0, PV: 0 };
    let   totalAll         = 0;
    const chapterLots      = {};   // chapId → lot
    const chapterInclusion = {};   // chapId → bool (checkbox chapitre)
    const chapterSumSects  = {};   // chapId → Σ section displaySectSum

    // ── Lecture état chapitres ──────────────────────────────────────────────────
    document.querySelectorAll('.row-chapter').forEach(tr => {
        const chapId    = tr.dataset.chapterId;
        const key       = tr.dataset.chapterKey;
        const state     = CHAPTER_STATE[key] || { is_included: true };
        const chkEl     = tr.querySelector('.chapter-check');
        // Priorité au DOM (checkbox visible) sinon CHAPTER_STATE
        chapterInclusion[chapId] = chkEl ? chkEl.checked : (state.is_included !== false);
        chapterLots[chapId]      = tr.dataset.lot || 'CFO';
        chapterSumSects[chapId]  = 0;
    });

    // ── Calcul par section ──────────────────────────────────────────────────────
    // Règle : chapitre coché + section cochée → sous-total
    //   • lignes cochées avec qty > 0  → sommées
    //   • si Σ = 0 (toutes qty = 0)   → fallback ratio_ref_section × SDO
    //   • chapitre ou section décoché  → sous-total vide, non comptabilisé
    document.querySelectorAll('.row-section').forEach(sec => {
        const chapId      = sec.dataset.chapterId;
        const chapActive  = chapterInclusion[chapId] !== false;
        const sectCheckEl = sec.querySelector('.section-check');
        const sectActive  = sectCheckEl ? sectCheckEl.checked : true;

        const elSRatio = sec.querySelector('.section-ratio');
        const refPerM2 = elSRatio ? parseFloat(elSRatio.dataset.ratioRef || 0) : 0;

        // Ratio €/m² affiché (toujours visible, quelle que soit l'inclusion)
        if (elSRatio) {
            elSRatio.textContent = refPerM2 > 0 ? `${refPerM2.toFixed(1)} €/m²` : '—';
        }

        if (!chapActive || !sectActive) {
            const elSub = sec.querySelector('.section-subtotal');
            if (elSub) elSub.textContent = '';
            return;
        }

        // Somme des articles cochés avec qty > 0
        let sectSum = 0;
        let next = sec.nextElementSibling;
        while (next && !next.classList.contains('row-section') && !next.classList.contains('row-chapter')) {
            if (next.classList.contains('row-article')) {
                const cb = next.querySelector('.line-check');
                if (cb && cb.checked) {
                    const qty = parseFloat(next.querySelector('.input-qty')?.value || 0) || 0;
                    if (qty > 0) {
                        const pu = parseFloat(next.querySelector('.input-pu')?.value || 0) || 0;
                        sectSum += qty * pu;
                    }
                }
            }
            next = next.nextElementSibling;
        }

        // Mise à jour ratio €/m² dynamique si données réelles
        if (sectSum > 0 && currentSDO > 0 && elSRatio) {
            elSRatio.textContent = `${(sectSum / currentSDO).toFixed(1)} €/m²`;
        }

        // Sous-total : somme réelle OU fallback ratio_ref × SDO
        const displaySectSum = sectSum > 0
            ? sectSum
            : (refPerM2 > 0 ? refPerM2 * currentSDO : 0);

        const elSub = sec.querySelector('.section-subtotal');
        if (elSub) elSub.textContent = displaySectSum > 0 ? formatEur(displaySectSum) : '';

        chapterSumSects[chapId] = (chapterSumSects[chapId] || 0) + displaySectSum;
    });

    // ── Totaux chapitre = Σ sections ───────────────────────────────────────────
    document.querySelectorAll('.row-chapter').forEach(tr => {
        const chapId     = tr.dataset.chapterId;
        const chapActive = chapterInclusion[chapId];
        const finalTotal = chapActive ? (chapterSumSects[chapId] || 0) : 0;

        const lot = chapterLots[chapId] || 'CFO';
        totals[lot] = (totals[lot] || 0) + finalTotal;
        totalAll    += finalTotal;

        // Sous-total chapitre
        const elSub = document.getElementById(`chapter-sub-${chapId}`);
        if (elSub) elSub.textContent = finalTotal > 0 ? formatEur(finalTotal) : '—';

        // Ratio €/m² chapitre
        const elRatio  = document.getElementById(`chapter-ratio-${chapId}`);
        if (elRatio) {
            const dynRatio = currentSDO > 0 ? finalTotal / currentSDO : 0;
            const refRatio = parseFloat(elRatio.dataset.ratioRef || 0);
            const shown    = dynRatio > 0 ? dynRatio : refRatio;
            elRatio.textContent = shown > 0 ? `${Math.round(shown)} €/m²` : '—';
        }
    });

    // KPIs
    setKPI('kpi-cfo',   totals['CFO'] || 0);
    setKPI('kpi-cfa',   totals['CFA'] || 0);
    setKPI('kpi-pv',    totals['PV']  || 0);
    setKPI('kpi-total', totalAll);

    const m2 = currentSDO > 0 ? totalAll / currentSDO : 0;
    const m2El = document.getElementById('kpi-m2');
    if (m2El) m2El.textContent = formatEur(m2) + '/m²';

    currentTotal = totalAll;   // mémorisé pour la sauvegarde dashboard
    updateProvisions(totalAll);
}

function setKPI(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = formatEur(value);
}


// ── Paramètres SDO / kVA / Phase / Provisions ─────────────────────────────────

function initParamsListeners() {
    // SDO éditable → recalcul des ratios avec debounce
    document.getElementById('edit-sdo')?.addEventListener('input', e => {
        const val = parseFloat(e.target.value);
        if (val >= 100) {
            currentSDO = val;
            clearTimeout(sdoDebounceTimer);
            sdoDebounceTimer = setTimeout(recalculateRatios, 500);
        }
    });
    document.getElementById('edit-sdo')?.addEventListener('change', scheduleParamsSave);

    // kVA
    document.getElementById('edit-kva')?.addEventListener('change', e => {
        currentKVA = parseFloat(e.target.value) || 800;
        scheduleParamsSave();
    });

    // Phase → preset Taux Phase (taxe indépendante, modifiable ensuite)
    document.getElementById('edit-phase')?.addEventListener('change', e => {
        currentPhase = e.target.value;
        const preset = PHASE_PRESETS[currentPhase] ?? 3;
        tauxPhase = preset;
        const input = document.getElementById('input-taux-phase');
        if (input) input.value = preset.toFixed(1);
        updateAllTotals();
        scheduleParamsSave();
    });

    // Taxe Phase
    document.getElementById('input-taux-phase')?.addEventListener('input', e => {
        tauxPhase = parseFloat(e.target.value) || 0;
        updateAllTotals();
        scheduleParamsSave();
    });

    // Taxe Incertitude
    document.getElementById('input-incertitude')?.addEventListener('input', e => {
        tauxIncertitude = parseFloat(e.target.value) || 0;
        updateAllTotals();
        scheduleParamsSave();
    });

    // Taxe Risque
    document.getElementById('input-risque')?.addEventListener('input', e => {
        tauxRisque = parseFloat(e.target.value) || 0;
        updateAllTotals();
        scheduleParamsSave();
    });

    // Sliders de complexité par lot → auto-save + recalcul instantané des PU
    ['cfo', 'cfa', 'pv'].forEach(lot => {
        const slider = document.getElementById(`cplx-${lot}`);
        const label  = document.getElementById(`label-cplx-${lot}`);
        if (!slider) return;
        slider.addEventListener('input', e => {
            const val = parseFloat(e.target.value) || 1;
            complexity[lot] = val;
            if (label) label.textContent = `×${val.toFixed(2)}`;
            clearTimeout(sdoDebounceTimer);
            sdoDebounceTimer = setTimeout(recalculateRatios, 350);
            scheduleParamsSave();
        });
    });
}

function updateProvisions(totalHT) {
    // Formule : Total × (1+Phase) × (1+Incertitude) × (1+Risque)
    const factor = (1 + tauxPhase       / 100)
                 * (1 + tauxIncertitude / 100)
                 * (1 + tauxRisque      / 100);
    const el = document.getElementById('kpi-provisions');
    if (el) el.textContent = formatEur(totalHT * factor);
}

function recalculateRatios() {
    const url = `/api/ratios?sdo=${currentSDO}`
              + `&ccfo=${complexity.cfo}&ccfa=${complexity.cfa}&cpv=${complexity.pv}`;

    fetch(url)
        .then(r => r.json())
        .then(ratios => {
            document.querySelectorAll('.row-article').forEach(row => {
                const artId = String(row.dataset.articleId);
                const ratio = ratios[artId];
                if (!ratio) return;

                // Sprint 7 §2.4 — Décorrélation SDO : unités fixes (u/ens/ml)
                // ne varient PAS quand la SDO change. Seules les lignes
                // surfaciques (m²) et les ratios_type SURFACIQUE sont
                // recalculées. Les complexités continuent de s'appliquer à
                // toutes les lignes (lot-spécifiques).
                const unit       = (row.dataset.unit || '').toLowerCase();
                const ratioType  = row.dataset.ratioType || '';
                const isSurfaceLinked = (unit === 'm²' || unit === 'm2')
                                        || ratioType === 'SURFACIQUE';

                const qtyInput = row.querySelector('.input-qty');
                const puInput  = row.querySelector('.input-pu');

                if (isSurfaceLinked) {
                    // Ligne liée SDO → recalcul qty + PU depuis le ratio
                    if (qtyInput && qtyInput.dataset.source !== 'manual' && ratio.qty_estimee != null) {
                        qtyInput.value = ratio.qty_estimee.toFixed(2);
                    }
                    if (puInput && puInput.dataset.source !== 'manual' && ratio.avg_pu_cible != null) {
                        puInput.value = ratio.avg_pu_cible.toFixed(2);
                        RATIO_REFS[artId] = ratio.avg_pu_actualise || 0;
                    }
                } else {
                    // Unité fixe (u/ens/ml/…) → quantité NE BOUGE PAS, seul le PU
                    // suit la complexité (pas la SDO). On met à jour le PU si pas manuel.
                    if (puInput && puInput.dataset.source !== 'manual' && ratio.avg_pu_cible != null) {
                        puInput.value = ratio.avg_pu_cible.toFixed(2);
                        RATIO_REFS[artId] = ratio.avg_pu_actualise || 0;
                    }
                    // qty : on NE TOUCHE PAS (préserve la saisie utilisateur)
                }
                computeRow(row);
            });
            updateAllTotals();
            scheduleParamsSave();
            showNotification(`Ratios recalculés — SDO ${currentSDO} m² (u/ens/ml inchangés)`, 'info');
        })
        .catch(() => showNotification('Erreur lors du recalcul des ratios', 'error'));
}

function scheduleParamsSave() {
    clearTimeout(paramsTimer);
    paramsTimer = setTimeout(saveParams, 600);
}

function saveParams() {
    fetch(`/api/affaire/${AFFAIRE_ID}/params`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            surface_sdo:         currentSDO,
            kva_cible:           currentKVA,
            phase_etude:         currentPhase,
            taux_phase:          tauxPhase,
            taux_incertitude:    tauxIncertitude,
            coef_risque:         tauxRisque,
            coef_complexity_cfo: complexity.cfo,
            coef_complexity_cfa: complexity.cfa,
            coef_complexity_pv:  complexity.pv,
        }),
    }).catch(() => {});
}

function saveParam(key, value) {
    fetch(`/api/affaire/${AFFAIRE_ID}/params`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value }),
    }).catch(() => {});
}


// ── Correction popup ──────────────────────────────────────────────────────────

let correctionTarget = null;

function showCorrectionPopup(inputEl, articleId, newVal, ratioRef) {
    const popup = document.getElementById('correction-popup');
    if (!popup) return;

    correctionTarget = { articleId, newVal, ratioRef, inputEl };

    const devPct = ((newVal - ratioRef) / ratioRef * 100).toFixed(1);
    popup.querySelector('#popup-ratio-ref').textContent  = formatEur(ratioRef);
    popup.querySelector('#popup-new-val').textContent    = formatEur(newVal);
    popup.querySelector('#popup-deviation').textContent  = `${devPct}%`;

    // Positionne le popup près de l'input
    const rect = inputEl.getBoundingClientRect();
    popup.style.top  = `${rect.bottom + window.scrollY + 6}px`;
    popup.style.left = `${Math.min(rect.left, window.innerWidth - 280)}px`;
    popup.classList.add('visible');
}

function closeCorrectionPopup() {
    const popup = document.getElementById('correction-popup');
    if (popup) popup.classList.remove('visible');
    correctionTarget = null;
}

function applySpecifique() {
    // Rien à faire : valeur déjà dans l'input
    closeCorrectionPopup();
    showNotification('Valeur appliquée à cette affaire uniquement', 'info');
}

function applyBase() {
    if (!correctionTarget) return;
    const { articleId, newVal } = correctionTarget;
    const raison = document.getElementById('popup-raison')?.value || '';

    fetch('/api/ratio/correct', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            dpgf_article_id: articleId,
            pu_override:     newVal,
            raison:          raison,
            scope:           'base',
        })
    })
    .then(r => r.json())
    .then(data => {
        showNotification(data.message || 'Base globale mise à jour', 'success');
        closeCorrectionPopup();
        // Mise à jour du ratio_ref local
        RATIO_REFS[articleId] = newVal;
        const row = document.querySelector(`[data-article-id="${articleId}"]`);
        if (row) updateDeviation(row, newVal, newVal);
    });
}


// ── Sauvegarde ────────────────────────────────────────────────────────────────

function saveAffaire() {
    const lines = [];

    document.querySelectorAll('.row-article').forEach(row => {
        const artId  = parseInt(row.dataset.articleId);
        const qtyEl  = row.querySelector('.input-qty');
        const puEl   = row.querySelector('.input-pu');
        const unitEl = row.querySelector('.input-unit');
        const cbEl   = row.querySelector('.line-check');

        if (!artId) return;

        const qty  = parseFloat(qtyEl?.value || 0) || 0;
        const pu   = parseFloat(puEl?.value  || 0) || 0;
        const unit = (unitEl?.value || '').trim();

        lines.push({
            dpgf_article_id:   artId,
            quantity:          qty,
            quantity_source:   qtyEl?.dataset?.source || 'ratio',
            unit_price_ht:     pu,
            unit_price_source: puEl?.dataset?.source  || 'ratio',
            total_ht:          qty * pu,
            is_included:       cbEl ? cbEl.checked : true,
            ratio_ref:         RATIO_REFS[artId] || pu,
            unit_override:     unit || null,
            unit_source:       unitEl?.dataset?.source || 'ratio',
        });
    });

    fetch(`/api/affaire/${AFFAIRE_ID}/save`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lines, total_estime: currentTotal }),
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'ok') {
            isDirty = false;
            showNotification(`${data.saved} lignes sauvegardées`, 'success');
        } else {
            showNotification('Erreur lors de la sauvegarde', 'error');
        }
    })
    .catch(() => showNotification('Erreur réseau', 'error'));
}


// ── Export Excel ──────────────────────────────────────────────────────────────

function exportExcel() {
    showNotification('Génération du fichier Excel…', 'info');
    window.location.href = `/api/affaire/${AFFAIRE_ID}/export`;
}


// ── Filtres lot ───────────────────────────────────────────────────────────────

function filterLot(lot) {
    document.querySelectorAll('[data-lot-filter]').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.lotFilter === lot);
    });

    document.querySelectorAll('.row-article, .row-section').forEach(row => {
        if (lot === 'ALL') {
            row.style.display = '';
        } else {
            const rowLot = row.dataset.lot || '';
            row.style.display = (rowLot === lot || row.classList.contains('row-section')) ? '' : 'none';
        }
    });

    document.querySelectorAll('.row-chapter').forEach(row => {
        if (lot === 'ALL') {
            row.style.display = '';
        } else {
            const chapterLot = row.dataset.lot || '';
            row.style.display = chapterLot === lot ? '' : 'none';
        }
    });
}


// ── Masquer / afficher lignes à zéro ─────────────────────────────────────────

let hideZeroLines = false;

function toggleZeroLines() {
    hideZeroLines = !hideZeroLines;
    const btn = document.getElementById('btn-zero');
    if (btn) btn.textContent = hideZeroLines ? '👁 Afficher zéros' : '👁 Masquer zéros';

    document.querySelectorAll('.row-article').forEach(row => {
        const qty = parseFloat(row.querySelector('.input-qty')?.value || 0) || 0;
        const pu  = parseFloat(row.querySelector('.input-pu')?.value  || 0) || 0;
        if (qty === 0 && pu === 0) {
            row.classList.toggle('zero-hidden', hideZeroLines);
        }
    });

    // Cache aussi les sections sans aucun article visible
    document.querySelectorAll('.row-section').forEach(section => {
        const chapId = section.dataset.chapterId;
        const visibleInSection = Array.from(
            document.querySelectorAll(`.row-article[data-chapter-id="${chapId}"]`)
        ).some(r => !r.classList.contains('zero-hidden') && !r.classList.contains('excluded'));
        section.classList.toggle('zero-hidden', hideZeroLines && !visibleInSection);
    });
}


// ── Utilitaires ───────────────────────────────────────────────────────────────

function formatEur(val) {
    if (val == null || isNaN(val)) return '—';
    if (val === 0) return '0 €';
    return new Intl.NumberFormat('fr-FR', {
        style: 'currency',
        currency: 'EUR',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0,
    }).format(val);
}

function showNotification(msg, type = 'info') {
    const container = document.getElementById('flash-container');
    if (!container) return;

    const el = document.createElement('div');
    el.className = `flash flash-${type}`;
    el.innerHTML = `<span>${msg}</span>`;
    container.appendChild(el);

    setTimeout(() => {
        el.style.opacity = '0';
        el.style.transition = 'opacity .3s';
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

// Ferme le popup correction si on clique ailleurs
document.addEventListener('click', e => {
    const popup = document.getElementById('correction-popup');
    if (popup && !popup.contains(e.target) && !e.target.classList.contains('input-pu')) {
        closeCorrectionPopup();
    }
});
