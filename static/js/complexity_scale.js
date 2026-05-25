/**
 * Échelle de complexité Egis (5 paliers) — partagée fiche affaire / calculateur.
 */
'use strict';

const COMPLEXITY_COEFS = [0.75, 0.9, 1.0, 1.25, 1.55];

const COMPLEXITY_BY_COEF = {
  0.75: {
    term: 'Coque / État brut',
    hint: 'Structure + enveloppe + attentes réglementaires minimales. Second œuvre absent ou en réserve.',
  },
  0.9: {
    term: 'Livraison blanche',
    hint: 'Conformité réglementaire assurée, équipements de base, sans confort ajouté.',
  },
  1.0: {
    term: 'Standard occupant',
    hint: 'Livraison complète et fonctionnelle pour le type de programme. Base de ratio.',
  },
  1.25: {
    term: 'Standing',
    hint: 'Prestations étoffées, matériel haut de gamme, densité accrue.',
  },
  1.55: {
    term: 'Prestige / Technique dense',
    hint: 'Très haute qualité ou programme à forte exigence technique (hôpital, datacenter, musée).',
  },
};

function snapComplexityCoef(val) {
  const v = parseFloat(val);
  if (!Number.isFinite(v)) return 1.0;
  let best = COMPLEXITY_COEFS[2];
  let bestD = Infinity;
  for (const c of COMPLEXITY_COEFS) {
    const d = Math.abs(c - v);
    if (d < bestD) {
      bestD = d;
      best = c;
    }
  }
  return best;
}

function complexityCoefToIndex(coef) {
  const c = snapComplexityCoef(coef);
  const i = COMPLEXITY_COEFS.indexOf(c);
  return i >= 0 ? i : 2;
}

function complexityIndexToCoef(index) {
  const i = Math.max(0, Math.min(4, parseInt(index, 10) || 0));
  return COMPLEXITY_COEFS[i];
}

function formatComplexityCoef(coef) {
  const c = snapComplexityCoef(coef);
  return c % 1 === 0 ? c.toFixed(2).replace(/\.00$/, '.0') : c.toFixed(2);
}

function updateComplexityDisplay(lot, coef, opts) {
  opts = opts || {};
  const c = snapComplexityCoef(coef);
  const meta = COMPLEXITY_BY_COEF[c] || COMPLEXITY_BY_COEF[1.0];
  const label = document.getElementById(opts.labelId || `label-${lot}`);
  const hint = document.getElementById(opts.hintId || `hint-${lot}`);
  const term = document.getElementById(opts.termId || `term-${lot}`);
  if (label) label.textContent = `×${formatComplexityCoef(c)}`;
  if (term) term.textContent = meta.term;
  if (hint) hint.textContent = meta.hint;
  return c;
}

function bindComplexitySlider(slider, opts) {
  if (!slider) return snapComplexityCoef(opts.initialCoef || 1);
  const hidden = opts.hiddenInput || null;
  const lot = opts.lot || '';
  const onChange = opts.onChange || null;

  const initial = snapComplexityCoef(
    hidden ? parseFloat(hidden.value) : (opts.initialCoef || 1)
  );
  slider.min = 0;
  slider.max = 4;
  slider.step = 1;
  slider.value = complexityCoefToIndex(initial);

  function sync() {
    const coef = complexityIndexToCoef(slider.value);
    if (hidden) hidden.value = coef;
    updateComplexityDisplay(lot, coef, opts);
    if (onChange) onChange(coef);
    return coef;
  }

  slider.addEventListener('input', sync);
  return sync();
}

function initAffaireNewComplexitySliders() {
  ['cfo', 'cfa'].forEach(lot => {
    const slider = document.querySelector(`[data-cplx-slider="${lot}"]`);
    const hidden = document.querySelector(`[name="coef_complexity_${lot}"]`);
    bindComplexitySlider(slider, { lot, hiddenInput: hidden });
  });
}

function initCalculateurComplexitySliders(onCoefChange) {
  ['cfo', 'cfa', 'pv'].forEach(lot => {
    const slider = document.getElementById(`cplx-${lot}`);
    if (!slider) return;
    const initial = parseFloat(slider.getAttribute('data-initial-coef') || slider.value) || 1;
    bindComplexitySlider(slider, {
      lot,
      labelId: `label-cplx-${lot}`,
      initialCoef: initial,
      onChange(coef) {
        if (onCoefChange) onCoefChange(lot, coef);
      },
    });
  });
}

window.COMPLEXITY_COEFS = COMPLEXITY_COEFS;
window.snapComplexityCoef = snapComplexityCoef;
window.complexityCoefToIndex = complexityCoefToIndex;
window.complexityIndexToCoef = complexityIndexToCoef;
window.updateComplexityDisplay = updateComplexityDisplay;
window.bindComplexitySlider = bindComplexitySlider;
window.initAffaireNewComplexitySliders = initAffaireNewComplexitySliders;
window.initCalculateurComplexitySliders = initCalculateurComplexitySliders;
