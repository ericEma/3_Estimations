/**
 * Navigation latérale — liste Affaires repliable (état mémorisé en sessionStorage).
 */
(function () {
  'use strict';

  const toggle = document.getElementById('nav-affaires-toggle');
  const list = document.getElementById('nav-affaires-list');
  const chevron = document.getElementById('nav-affaires-chevron');
  if (!toggle || !list) return;

  const KEY = 'ee_nav_affaires_open';

  function setOpen(open) {
    list.hidden = !open;
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (chevron) chevron.textContent = open ? '▼' : '▶';
    try {
      sessionStorage.setItem(KEY, open ? '1' : '0');
    } catch (_) { /* ignore */ }
  }

  let open = true;
  try {
    const stored = sessionStorage.getItem(KEY);
    if (stored === '0') open = false;
  } catch (_) { /* ignore */ }

  const hasActiveAffaire = list.querySelector('.aff-item.on');
  if (hasActiveAffaire) open = true;

  setOpen(open);

  toggle.addEventListener('click', function () {
    setOpen(list.hidden);
  });
})();
