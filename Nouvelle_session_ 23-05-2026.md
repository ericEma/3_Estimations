# Nouvelle session — Estimation Élec (Egis)
## Rôle
Tu es développeur fullstack Python/Flask sur le projet **Estimation Élec** (Egis). Eric est ingénieur électricien, garant métier. Workspace : `E:\16_Claude Code\3_Estimations`. Ne pas committer sans demande explicite d’Eric.
## Lecture obligatoire (dans cet ordre)
1. `CLAUDE.md` — règles métier, schéma, routes, §12 Matching, **§13 Fiche affaire**
2. `CURRENT_STATE.md` — jalons (dernier : **2026-05-23**)
3. `TODO.md` — backlog
4. `DB_ARCHITECTURE.md` si migration BDD / suppressions
## Stack (immuable)
Python 3.10+, Flask 3.x, SQLite3 natif (pas SQLAlchemy), rapidfuzz, openpyxl, loguru.  
Lancement local : `Lancer_Estimateur.bat` (perso) / `Lancer_Estimateur.vbs` (entreprise).
## Dernier jalon validé — 2026-05-23 (fiche affaire)
**Routes** : `/affaire/new`, `/affaire/<id>/edit`, `GET /api/affaire/preview_estimation`
**Fonctionnel** :
- Carte estimation **CFO / CFA / PV + Total** sur `/affaire/new`
- Ratios = **Bibliothèque DPGF** (`pu_ht_ref`) via `scripts/engine_bibliotheque_ratios.py` — **PAS** `compute_ratios()` (devis importés)
- **PV** : slider 3 crans → `affaires.pv_system_type` (`toiture` ×1,0 / `ib` ×1,3 / `ombriere` ×1,55) — `static/js/pv_system_scale.js`
- **CFO/CFA** : slider complexité 5 paliers — `static/js/complexity_scale.js`
- Sur la fiche : `coef_complexity_pv` = **1,0** ; type PV = `pv_system_type` uniquement
- `puissance_pv_kwc` (diviseur €/kWc) ≠ `kva_cible` (TGBT kVA, référence future)
**Formule preview** :
- Base biblio (CFO/CFA : €/m² × SDO ; PV : €/kWc × kWc)
- × complexité (CFO/CFA seulement) × `(1+phase%)×(1+incertitude%)×(1+risque%)`
- PV : × coef système (`models.pv_system_coef`)
**Ratios de référence typiques** (SDO 1000, 100 kWc) : CFO ~572 €/m², CFA ~211 €/m², PV ~0,95 €/kWc.  
Les montants affichés > ratio×surface si provisions > 0 % (cumul multiplicatif) — **comportement validé Eric**.
**Fichiers clés** : `app.py` (`compute_affaire_preview_estimation`), `models.py` (`PV_SYSTEM_TYPES`), `templates/affaire_new.html`, `scripts/engine_bibliotheque_ratios.py`
## Autres modules stables (ne pas casser)
- **Bibliothèque** `/bibliotheque` — validée 2026-05-02 : scroll flex `body.bibl-page`, `localData` en `let`, debounce 800 ms
- **Calculateur** `/affaire/<id>` — section-first, lot detection `| lower`, cellules orange `data-source="manual"`
- **Estimation** `/affaire/<id>/estimation` — double calque, Sprint 9.4
- **Matching** `/matching` — `weighted_price_override`, modal DPGF, `engine_matching.py`
## Règles métier critiques
- Total majoré = HT × `(1+phase/100) × (1+incertitude/100) × (1+risque/100)`
- Chapitre = Σ sections (section-first)
- Lot : `faible`/`cfa` → CFA, `photovolta` → PV, sinon CFO (case-insensitive)
- Migrations : `try/except OperationalError` dans `ensure_app_tables()` ; FK : `PRAGMA foreign_keys OFF/ON`
- Pas de LaTeX dans l’UI/docs
## Backlog prioritaire (`TODO.md`)
1. **Export Excel** page Estimation (bouton placeholder)
2. Feedback visuel **"saving…"** sur `chapter_settings`
3. **Optionnel** : propager `pv_system_type` sur le calculateur DPGF (lot PV)
4. Statut affaire (workflow UI), tests E2E, etc.
## Travail demandé cette session
Nous allons travailler sur la modification de certaines fonctionnalités suite à ma vérification du fonctionnement.
## Contraintes
- Diff minimal, pas de sur-ingénierie
- Ne pas modifier le CSS bibliothèque sans accord
- Ne pas supprimer de fichiers/worktrees sans validation
- Vérifier équilibre financier (±0,5 %) si tu touches aux totaux