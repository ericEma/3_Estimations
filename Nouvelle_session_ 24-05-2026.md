# Nouvelle session — Estimation Élec (Egis)
**Date de clôture session précédente : 2026-05-24**

## Rôle
Tu es développeur fullstack Python/Flask sur **Estimation Élec** (Egis). Eric = garant métier. Workspace : `E:\16_Claude Code\3_Estimations`. **Ne pas committer** sans demande explicite.

## Lecture obligatoire (ordre)
1. `CLAUDE.md` — règles métier, routes, **§13 Fiche**, **§14 Page Estimation (snapshot + layout)**
2. `CURRENT_STATE.md` — jalon **2026-05-24**
3. `TODO.md` — backlog
4. `DB_ARCHITECTURE.md` — si migration / suppressions FK

## Stack (immuable)
Python 3.10+, Flask 3.x, SQLite3 natif, rapidfuzz, openpyxl, loguru.  
Lancement : `Lancer_Estimateur.bat` / `Lancer_Estimateur.vbs`

---

## Deux mondes à ne pas confondre

| Zone | Comportement |
|------|----------------|
| **Fiche affaire** (`/affaire/new`, `/affaire/<id>/edit`) | Calculatrice **vivante** : ratios biblio, SDO/kWc, provisions — tout modifiable |
| **Page Estimation** (`/affaire/<id>/estimation`) | **Snapshot à la création** : PU figés (`ratio_ref`), pas de resync biblio ; macros section suivent SDO/kWc |

**Affaires de test** créées avant le snapshot : pas de migration ; **créer une nouvelle affaire** pour tester.

---

## Validé Eric — session 2026-05-24

### Sprint 1 — Snapshot création
- `initialize_estimation_snapshot()` au `POST /affaire/new`
- PU = `pu_ht_ref` ou `compute_ratios()` si NULL (aligné écran bibliothèque)
- `estimation_initialized_at` ; plus de `/api/ratios` sur page estimation initialisée
- Tests : `tests/test_estimation_snapshot.py`

### Sprint 2 — Ligne section macro
- Ratio €/m² / €/kWc + surface en qté sur ligne sous-chapitre (éditable)
- Bascule détail si qty article > 0 dans la section

### Sprint 3 — Layout affaire-only
- `estimation_layout.py` : `add_section`, `add_article`, `delete_section`, `move_section`, `move_article`, `rename_section`
- UI : `+`, `§+`, ▲/▼, poubelle (sections locales), bouton 📖 promotion
- Nouvelle section : sous le sous-chapitre courant, macro ratio, **1 article vide**, pas de hors catalogue (bouton masqué)
- Désignations : classe `estim-desig-edit` (même rendu texte) ; **tous les articles** éditables → `affaire_lines.line_designation` (override affaire, pas la biblio PSA)

### Sprint 4 — Promotion base de prix
- `estimation_promote.py` + `POST /api/affaire/<id>/estimation/promote`
- `promote_section` / `promote_article` → `dpgf_articles` is_custom=1 + liaison lignes
- Tests : `tests/test_estimation_promote.py`

### Sprint 5 (partiel)
- Lien **Calculateur** retiré du header `affaire_estimation.html`

---

## Fichiers clés (session 2026-05-24)

| Fichier | Rôle |
|---------|------|
| `models.py` | Snapshot, `get_estimation_catalog_rows`, KPI, `save_estimation_changes` (+ `line_designation` catalogue) |
| `estimation_layout.py` | Layout + ordre sections (`affaire_estimation_section_sort`) |
| `estimation_promote.py` | Promotion vers biblio |
| `app.py` | Routes `estimation/layout`, `estimation/promote` |
| `static/js/affaire_estimation.js` | Grille, désignations, layout, promotion |
| `static/css/style.css` | `.estim-desig-edit`, boutons section |

### Colonnes BDD (migrations auto Sprint 10–11)
- `affaires.estimation_initialized_at`
- `affaire_lines` : `line_chapter`, `line_section`, `sort_order`
- `affaire_chapter_settings.is_local`
- `affaire_estimation_section_sort` (chapter, section, sort_order)

---

## Prochaine session — priorité Eric

### Sprint 5 — Fiche affaire (à faire)
- **Ratios globaux CFO / CFA / PV éditables** sur `affaire_new.html` (image 5 du cahier des charges)
- Persistance sur `affaires` (colonnes à définir ou réutilisation champs existants)
- Preview `GET /api/affaire/preview_estimation` doit utiliser ces overrides

### Ensuite (backlog)
1. Export Excel page Estimation (placeholder actuel)
2. Feedback visuel « saving… » sur `chapter_settings`
3. Optionnel : `pv_system_type` sur calculateur legacy `/affaire/<id>`
4. Renommer sections PSA depuis estimation ? (non demandé — seulement locales aujourd’hui)

---

## Rappels métier critiques
- Total majoré = HT × `(1+phase/100) × (1+incertitude/100) × (1+risque/100)`
- Chapitre = Σ sections (section-first)
- Lot : `faible`/`cfa` → CFA, `photovolta` → PV, sinon CFO (`| lower`)
- Migrations : `try/except OperationalError` ; FK `PRAGMA foreign_keys OFF/ON`
- Pas de LaTeX dans l’UI

## Tests à lancer en début de session
```bash
python -m unittest tests.test_estimation_snapshot tests.test_estimation_promote -v
```

## Contraintes dev
- Diff minimal ; ne pas casser CSS bibliothèque (`body.bibl-page`)
- Vérifier équilibre financier ±0,5 % si totaux modifiés
- Nouvelle affaire obligatoire après changement schéma / snapshot
