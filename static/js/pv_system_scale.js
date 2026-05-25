/**
 * Type de système PV — 3 paliers (fiche affaire).
 */
'use strict';

const PV_SYSTEM_OPTIONS = [
  { id: 'toiture', coef: 1.0, label: '×1,0', term: 'Toiture surimposée', hint: 'Ratio de référence' },
  { id: 'ib', coef: 1.3, label: '×1,3', term: 'Intégration au bâti (IB)', hint: '+ coût structure / étanchéité' },
  { id: 'ombriere', coef: 1.55, label: '×1,55', term: 'Ombrière', hint: 'Structure métallique incluse' },
];

function pvSystemIdFromValue(val) {
  const v = String(val || 'toiture').toLowerCase();
  return PV_SYSTEM_OPTIONS.some(o => o.id === v) ? v : 'toiture';
}

function pvSystemIndexFromId(id) {
  const i = PV_SYSTEM_OPTIONS.findIndex(o => o.id === pvSystemIdFromValue(id));
  return i >= 0 ? i : 0;
}

function updatePvSystemDisplay(opts) {
  const idx = Math.max(0, Math.min(2, parseInt(opts.index, 10) || 0));
  const meta = PV_SYSTEM_OPTIONS[idx];
  const label = document.getElementById(opts.labelId || 'label-pv-system');
  const term = document.getElementById(opts.termId || 'term-pv');
  const hint = document.getElementById(opts.hintId || 'hint-pv');
  if (label) label.textContent = meta.label;
  if (term) term.textContent = meta.term;
  if (hint) hint.textContent = meta.hint;
  return meta;
}

function bindPvSystemSlider(slider, opts) {
  if (!slider) return pvSystemIdFromValue(opts.initialId);
  const hidden = opts.hiddenInput || null;
  const onChange = opts.onChange || null;
  const initialIdx = pvSystemIndexFromId(opts.initialId);

  slider.min = 0;
  slider.max = 2;
  slider.step = 1;
  slider.value = initialIdx;

  function sync() {
    const idx = parseInt(slider.value, 10) || 0;
    const meta = updatePvSystemDisplay({ index: idx, labelId: opts.labelId, termId: opts.termId, hintId: opts.hintId });
    if (hidden) hidden.value = meta.id;
    if (onChange) onChange(meta.id, meta.coef);
    return meta.id;
  }

  slider.addEventListener('input', sync);
  return sync();
}

function initAffaireNewPvSystemSlider() {
  const slider = document.querySelector('[data-pv-system-slider]');
  const hidden = document.querySelector('[name=pv_system_type]');
  const initial = hidden ? hidden.value : 'toiture';
  bindPvSystemSlider(slider, {
    hiddenInput: hidden,
    initialId: initial,
    labelId: 'label-pv-system',
    termId: 'term-pv',
    hintId: 'hint-pv',
    onChange() {
      if (typeof updatePreview === 'function') updatePreview();
    },
  });
}

window.PV_SYSTEM_OPTIONS = PV_SYSTEM_OPTIONS;
window.initAffaireNewPvSystemSlider = initAffaireNewPvSystemSlider;
