# Nouvelle session — Estimation Élec (Egis)
**Date de clôture session précédente : 2026-05-25**

## Rôle
Tu es développeur fullstack Python/Flask sur **Estimation Élec** (Egis). Eric = garant métier. Workspace : `E:\16_Claude Code\3_Estimations`. **Ne pas committer** sans demande explicite.

## Lecture obligatoire
1. `CLAUDE.md`
2. `CURRENT_STATE.md`
3. `DB_ARCHITECTURE.md`
4. `TODO.md`

## État validé 2026-05-25

### Fiche affaire
- `/affaire/new`, `/affaire/<id>/edit`
- Ratios globaux CFO/CFA/PV éditables et persistés : `ratio_global_cfo_m2`, `ratio_global_cfa_m2`, `ratio_global_pv_kwc`.
- Preview fiche uniquement : ces ratios ne resynchronisent pas la page Estimation snapshot.
- Ratio total €/m² affiché sous le total : `prix_total / surface_sdo`.

### Page Estimation
- `/affaire/<id>/estimation`
- Snapshot à la création : `estimation_initialized_at`, `affaire_lines.ratio_ref`.
- Header : SDO, kWc, phase/taux phase, incertitude, risque, tous auto-sauvegardés.
- Bouton `+ Ligne hors catalogue` retiré.
- Sous-chapitre macro : total = `ratio × qty` seulement si aucun article de la section n'a un total positif; sinon total = somme articles.
- Layout local : add/rename/move/delete section locale + add/move article.
- Promotion : section/article vers bibliothèque, position conservée via `row_order`.

### Bibliothèque DPGF
- `/bibliotheque`
- Déplacement ▲/▼ des sous-chapitres avec leurs articles, persistant dans `dpgf_articles.row_order`.
- Suppression section FK-safe : custom supprimé, PSA masqué (`is_hidden=1`).
- Attention CSS : ne pas casser le layout flex `body.bibl-page` + `min-height:0`.

### Dashboard
- `/`
- Chaque carte affaire a `Fiche` (`/affaire/<id>/edit`) et `Estimer` (`/affaire/<id>/estimation`).

## Tests de reprise
```bash
python -m unittest tests.test_affaire_preview_ratios tests.test_estimation_snapshot tests.test_estimation_promote tests.test_bibliotheque_section_move -v
node --check static/js/affaire_estimation.js
node --check static/js/bibliotheque.js
```

## Prochaines priorités
1. Export Excel page Estimation : remplacer le placeholder actuel.
2. Feedback visuel `saving…` sur autosaves paramètres/sections.
3. Statut affaire UI : brouillon / en cours / finalisé / archivé.

## Fichiers à surveiller
- Backend : `app.py`, `models.py`, `estimation_layout.py`, `estimation_promote.py`.
- Frontend : `templates/affaire_new.html`, `templates/affaire_estimation.html`, `templates/index.html`, `static/js/affaire_estimation.js`, `static/js/bibliotheque.js`, `static/css/style.css`.
- Tests : `tests/test_affaire_preview_ratios.py`, `tests/test_estimation_snapshot.py`, `tests/test_estimation_promote.py`, `tests/test_bibliotheque_section_move.py`.

## Règle Git ajoutée
- Les bases SQLite doivent être sauvegardées sur Git : `estimation_elec.db` et `estimation.db` si présent.
- Les Excel, captures et backups locaux restent hors commit sauf demande explicite.
