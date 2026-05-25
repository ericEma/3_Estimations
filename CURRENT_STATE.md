# CURRENT_STATE.md — Estimation Élec
**Mise à jour : 2026-05-25 | État sauvegardé — fiche, estimation, bibliothèque, architecture**

> Sous Windows (FS insensible à la casse), un fichier `current_state.md` distinct à la racine **n’est pas possible** : utiliser uniquement ce fichier `CURRENT_STATE.md`.

---

## Session 2026-05-25 — État sauvegardé GitHub (validé Eric)

**Objectif** : figer l'état fonctionnel validé pour éviter qu'une évolution future casse la fiche affaire, la page estimation ou la bibliothèque.

### Synthèse fonctionnelle validée

| Zone | Statut | Points validés / corrigés |
|---|---:|---|
| **Fiche affaire** `/affaire/new`, `/affaire/<id>/edit` | ✅ | Ratios globaux CFO/CFA/PV éditables, persistés dans `affaires`, repris à la réouverture, preview recalculée ; ratio global Total €/m² affiché sous le total. |
| **Dashboard** `/` | ✅ | Bouton `Fiche` sur chaque carte affaire vers `/affaire/<id>/edit`, bouton `Estimer` conservé vers `/affaire/<id>/estimation`. |
| **Page Estimation** `/affaire/<id>/estimation` | ✅ | Suppression du bouton `+ Ligne hors catalogue`; header enrichi avec phase, taux phase, incertitude et risque auto-sauvegardés; total sous-chapitre = macro ratio si aucun article chiffré, sinon somme des articles. |
| **Layout Estimation** | ✅ | Ajout/renommage section stabilisés; insertion sous le sous-chapitre cliqué; plus de doublon DOM après renommage; ordre section complet transmis au JS. |
| **Promotion biblio** | ✅ | Section/article locaux promus vers `dpgf_articles`; la section promue conserve sa position dans la bibliothèque via `row_order` (insertion au bon endroit). |
| **Bibliothèque DPGF** `/bibliotheque` | ✅ | Boutons ▲/▼ sur les sous-chapitres pour déplacer une section complète avec ses articles; persistance via `row_order`; suppression section toujours transactionnelle. |

### Règles anti-régression ajoutées

- **Fiche affaire** : les ratios globaux `ratio_global_cfo_m2`, `ratio_global_cfa_m2`, `ratio_global_pv_kwc` ne servent qu'à la preview de fiche, pas à resynchroniser une affaire snapshot.
- **Page Estimation snapshot** : ne pas relancer `/api/ratios` sur une affaire initialisée; les PU restent figés dans `affaire_lines.ratio_ref`.
- **Sous-chapitre macro** : utiliser le total macro `ratio_m2_override × qty` seulement si aucun article de la section n'a un total positif; dès qu'un article est chiffré, le sous-total affiché et les KPI utilisent la somme articles.
- **Promotion section** : lors de `promote_section`, calculer `row_order` depuis `affaire_estimation_section_sort` et décaler les articles suivants du chapitre.
- **Bibliothèque** : déplacer une section revient à réécrire les `row_order` de tous les articles visibles du chapitre; `localData` reste mutable (`let`).
- **Suppression section bibliothèque** : conserver `delete_bibliotheque_section()` et son ordre FK-safe; ne pas supprimer physiquement les articles PSA, utiliser `is_hidden=1`.

### Tests de garde-fou

- `tests/test_affaire_preview_ratios.py` : ratios globaux fiche, persistance création/édition et réouverture.
- `tests/test_estimation_snapshot.py` : snapshot création, PU figés, fallback `compute_ratios`, bascule macro/detail sur total article.
- `tests/test_estimation_promote.py` : promotion section/article, refus doublon, ordre bibliothèque après promotion.
- `tests/test_bibliotheque_section_move.py` : déplacement d'un sous-chapitre bibliothèque et persistance `row_order`.

### Fichiers principaux modifiés / à surveiller

- Backend : `app.py`, `models.py`, `estimation_layout.py`, `estimation_promote.py`.
- Frontend : `templates/affaire_new.html`, `templates/affaire_estimation.html`, `templates/index.html`, `static/js/affaire_estimation.js`, `static/js/bibliotheque.js`, `static/css/style.css`.
- Tests : `tests/test_affaire_preview_ratios.py`, `tests/test_bibliotheque_section_move.py`, `tests/test_estimation_snapshot.py`, `tests/test_estimation_promote.py`.

**À poursuivre** : export Excel de la page Estimation (bouton encore placeholder) puis feedback visuel `saving…` pour les paramètres/sections.

---

## Session 2026-05-24 — Page Estimation `/affaire/<id>/estimation` (validé Eric)

**Contexte** : évolution majeure de la page estimation (distincte de la fiche affaire « calculatrice »).

| Lot | Statut | Détail |
|-----|--------|--------|
| **Snapshot création** | ✅ | `initialize_estimation_snapshot()` au `POST /affaire/new` ; PU figés dans `ratio_ref` ; si `pu_ht_ref` NULL → `compute_ratios()` comme biblio |
| **Macro section** | ✅ | Ligne sous-chapitre : qté = SDO/kWc, PU = ratio €/m² ; total macro ; bascule si qty articles > 0 |
| **Layout affaire** | ✅ | `+`, `§+`, ▲/▼, suppression sections locales ; `estimation_layout.py` + API `estimation/layout` |
| **Nouvelle section** | ✅ | Insérée sous le sous-chapitre courant ; macro ratio éditable ; 1 article vide ; pas de création hors catalogue |
| **Désignations** | ✅ | Tous articles éditables (style `estim-desig-edit`) ; override `affaire_lines.line_designation` |
| **Promotion biblio** | ✅ | 📖 section / article → `estimation_promote.py` ; tests `test_estimation_promote.py` |
| **Header** | ✅ | Lien Calculateur retiré de `affaire_estimation.html` |

**Fichiers** : `models.py`, `estimation_layout.py`, `estimation_promote.py`, `app.py`, `affaire_estimation.js`, `style.css`, `tests/test_estimation_snapshot.py`, `tests/test_estimation_promote.py`.

**Prochain** : Sprint 5 — ratios globaux CFO/CFA/PV éditables sur fiche affaire (`affaire_new.html`).

**Test** : toujours **nouvelle affaire** après changement BDD ; affaires pré-snapshot = comportement legacy.

---

## Session 2026-05-23 — Fiche affaire `/affaire/new` (validé Eric)

**Contexte** : page création / édition affaire ; estimation prévisionnelle CFO/CFA/PV avant ouverture du calculateur.

| Élément | Détail |
|---|---|
| **PV — type de système** | Slider **3 crans** (comme CFO/CFA mais paliers dédiés) : Toiture ×1,00 / IB ×1,30 / Ombrière ×1,55 → `affaires.pv_system_type` |
| **PV — complexité** | `coef_complexity_pv` **fixé à 1,0** à la création/édition fiche (le slider « Coque–Prestige » ne s’applique plus au lot PV sur cette page) |
| **Estimation prévisionnelle** | Carte **4 lignes** : Prix CFO, CFA, PV, **Total** = somme des trois |
| **Source des ratios** | **`scripts/engine_bibliotheque_ratios.py`** — aligné sur la **Bibliothèque DPGF** (`pu_ht_ref`, ratios section manuels), **pas** `compute_ratios()` (devis importés) |
| **Formule prix lot** | `Base biblio × complexité (CFO/CFA) × (1+phase%) × (1+incertitude%) × (1+risque%)` ; PV : `× coef système` en plus |
| **Ratios affichés** | CFO/CFA en **€/m²** (ex. ~572 / ~211 pour SDO 1000) ; PV en **€/kWc** (ex. ~0,95) — diviseur = `puissance_pv_kwc` |
| **API** | `GET /api/affaire/preview_estimation` — recalcul debounce ~280 ms depuis `affaire_new.html` |
| **Champs fiche** | `puissance_pv_kwc` (kWc, diviseur PV) distinct de `kva_cible` (TGBT kVA, référence future) |

**Fichiers** : `app.py`, `models.py` (`PV_SYSTEM_TYPES`, migration `pv_system_type`), `scripts/engine_bibliotheque_ratios.py`, `static/js/pv_system_scale.js`, `static/js/complexity_scale.js` (init sliders CFO/CFA seulement), `templates/affaire_new.html`.

**Note métier validée** : si phase / incertitude / risque sont à **1 %** chacun, le multiplicateur provisions ≈ **1,0303** (+3,03 %) — les montants affichés sont donc supérieurs au simple « ratio × surface » affiché en bas de carte (base hors provisions).

---

## Session 2026-05-12 — Revue Matching (`/matching`)

**Contexte** : page cockpit après import devis ; pas le calculateur affaire.

| Élément | Détail |
|---|---|
| **BDD** | `devis_lines.weighted_price_override` (migration dans `ensure_app_tables`) — PU pondéré saisi manuellement |
| **API** | `POST /api/matching/line/<id>/weighted_price` ; réponses `select` / `create_article` incluent `base_chapter`, `base_section` (DPGF) |
| **`matching_data`** | Jointure `dpgf_articles` : expose `base_chapter`, `base_section` pour chaque ligne |
| **`candidates`** | Jointure si ligne mappée : `base_chapter` / `base_section` référentiel ; fil modal = DPGF, pas `context_path` devis |
| **Modal** | 1er candidat si ligne non mappée (évite `— \| — \| —`) ; ligne « nettoyé » si `cleaned_designation` non vide |
| **Grille** | Désignation devis + base : texte intégral, `pre-wrap` (sauts de ligne) ; `title` sur sélecteur base |
| **`engine_matching.py`** | Tokens avec élisions (`d'un`, `l'armoire`) ; apostrophe U+2019 → `'` dans `_sanitize_text` |
| **Tests** | `tests/test_engine_matching_clean.py`, `tests/test_matching_api.py` (override PU) |

Fichiers : `app.py`, `models.py`, `engine_matching.py`, `static/js/matching.js`, `templates/matching_view.html`, `schema.sql`.

---

## Jalon validé — 2026-05-02 — Page Bibliothèque DPGF

**Validé par Eric** : la page `/bibliotheque` (et variante contextuelle `/bibliotheque/<id>`) est **stable et utilisable en production bêta** : scroll vertical du tree-grid, édition inline (dont cellules manuelles / orange), KPI SDO / kWc, persistance debounce 800 ms, suppression de **section** (sous-chapitre) cohérente avec la base.

**À mémoriser pour les travaux suivants** :

- **Scroll / layout** : la chaîne flex `body.bibl-page` + `min-height: 0` sur `#root` → `.grid-wrap` est ce qui rend le scroll de la table possible — **ne pas modifier le CSS bibliothèque sans accord** (régression fréquente).
- **JS** : le tableau mutable `localData` doit rester déclaré en **`let`**, pas `const` (`deleteSection` réassigne après `filter`) — sinon `TypeError: Assignment to constant variable` et plus aucune save/render.
- **Backend** : suppression de section = `delete_bibliotheque_section` dans `models.py` ; batch save = `_FIELDS_NO_ARTICLE_ID` pour `section_delete` / `section_ratio_rename` ; rollback sur erreur. Référence **`DB_ARCHITECTURE.md`** (SQLite, FK par connexion, cascades manuelles).
- **Schéma** : pas d’`ALTER TABLE` sur `estimation_elec.db` sans validation Eric.

---

## Statut global

**Projet** : ESTIMATION ÉLEC (EGIS) — Phase Bêta
**Sprint 8** : ✅ Livré — Corrections QC Eric (5 rounds de retours)
**Sprint 9 (bibliothèque)** : ✅ **Jalon 2026-05-02** — Page Bibliothèque DPGF validée fonctionnellement
**Sprint 9.4 (estimation affaire)** : ✅ **2026-05-03** — `/affaire/<id>/estimation`, double calque, AJAX, lignes `dpgf_article_id` NULL
**Revue Matching (import)** : ✅ **2026-05-12** — cockpit `/matching`, PU calculé éditable, modal DPGF, désignations multilignes, nettoyage apostrophe
**Fiche affaire — preview bibliothèque** : ✅ **2026-05-23** — slider PV 3 crans, estimation CFO/CFA/PV sur `pu_ht_ref`, API `preview_estimation`
**Page Estimation — snapshot + layout** : ✅ **2026-05-24** — gel PU, sections/articles affaire, promotion biblio, désignations éditables
**Prochain** : **Sprint 5** ratios globaux CFO/CFA/PV sur fiche ; puis Export Excel estimation

---

## État technique

| Composant | État |
|---|---|
| Serveur Flask | Opérationnel (`Lancer_Estimateur.bat`) |
| Base SQLite | **v8** (colonne `total_estime_ht` ajoutée par migration auto) |
| Bibliothèque DPGF | ✅ **Validée 2026-05-02** — scroll, édition, suppression section, `DB_ARCHITECTURE.md` |
| Estimation affaire | ✅ **2026-05-24** — snapshot à création, layout/promote, désignations override, KPI, API `estimation/save` + `layout` + `promote` |
| Revue Matching | ✅ **2026-05-12** — `matching.js` / `matching_view.html`, API `weighted_price`, `engine_matching` |
| Référentiel DPGF | 285 lignes PSA (3 chapitres / 44 sections / 285 articles) |
| Types de bâtiments | 15 types (migration FK-safe : PRAGMA foreign_keys OFF/ON) |
| Lot detection | Case-insensitive via `| lower` Jinja2 (CFO/CFA/PV corrects) |
| Date d'affaire | Format JJ-MM-AAAA (`date_fr` filter Jinja2) |
| Section subtotals | Affichés dans le tableau DPGF (lignes section cochées) |
| Sous-total ratio €/m² | Fallback référentiel si toutes qty=0 dans la section |
| Architecture totaux | **Section-first** : chapitre = Σ sections (cohérence garantie) |
| Total dashboard | `total_estime_ht` stocké à chaque save (ratio fallback inclus) |
| Type bâtiment | Sélecteur dans la barre provisions (même ligne SDO/KVA/Phase) |
| Retour fiche affaire | Lien "✏️ Modifier la fiche" depuis le calculateur |
| Édition affaire | Route `/affaire/<id>/edit` (GET/POST) avec `affaire_new.html` |
| Fiche affaire — preview | ✅ **2026-05-23** — `engine_bibliotheque_ratios`, API `/api/affaire/preview_estimation`, slider PV |
| `affaires.pv_system_type` | Migration auto : `toiture` \| `ib` \| `ombriere` (défaut `toiture`) |
| `affaires.puissance_pv_kwc` | kWc — diviseur ratios chapitre Photovoltaïque (défaut 100) |

---

## Sprint 9.4 — Page Estimation d'affaire (implémenté 2026-05-03)

- **Route** : `GET /affaire/<id>/estimation` — `affaire_estimation.html` (même layout `body.bibl-page` que la bibliothèque pour le scroll).
- **Données** : `get_estimation_catalog_rows`, `get_estimation_custom_rows`, `compute_estimation_kpis` (`models.py`).
- **Persistance** : `POST /api/affaire/<id>/estimation/save` → `save_estimation_changes` ; debounce **600 ms** (`affaire_estimation.js`) ; mise à jour `total_estime_ht`.
- **BDD** : migration `_migrate_affaire_lines_hors_catalogue` — `dpgf_article_id` nullable, `line_designation`, `line_lot` ; `save_affaire_lines` ne supprime que `WHERE dpgf_article_id IS NOT NULL`.
- **UX** : calque rose (unité + PU référentiel, lecture seule), calque vert (qté, PU estimation, total temps réel) ; hors catalogue : rose « — », saisie désignation / unité / lot en vert.
- **Export** : bouton **placeholder** (alerte).
- **Navigation** : bouton vert « Estimation » dans la topbar du calculateur (`affaire.html`) ; bouton « Estimer » sur chaque carte du dashboard (`index.html`).
- **Création affaire** : après `POST /affaire/new`, redirection vers `/affaire/<id>/estimation` sans injection immédiate des 285 lignes (catalogue complet, qté 0 ; injection au premier passage sur le calculateur si `affaire_lines` vide).
- **Totaux** : arrondi strict à 2 décimales côté Python (`_round_money2`, KPI API) et côté JS (`round2`, `moneyTot`, KPI strip).

Fichiers : `app.py`, `models.py`, `templates/affaire_estimation.html`, `static/js/affaire_estimation.js`, `static/css/style.css`, `templates/affaire.html`, `templates/index.html`, `templates/affaire_new.html`, `CLAUDE.md`.

---

## ✅ Points validés — Sprint 8 (2026-04-19) [IMMUABLE]

### Round 1 — Corrections initiales
- **[VAL-8-01]** Totaux CFO/CFA/PV corrigés : lot detection case-insensitive (`| lower`)
- **[VAL-8-02]** Date affichée en format français JJ-MM-AAAA (filtre `date_fr`)
- **[VAL-8-03]** Sous-totaux par section dans le tableau DPGF
- **[VAL-8-04]** Sélecteur type de bâtiment dans la barre de paramètres (topbar)
- **[VAL-8-05]** Lien retour "Modifier la fiche" depuis le calculateur → `/affaire/<id>/edit`
- **[VAL-8-06]** 15 types de bâtiments : Aéroport, Bureaux, Château, Groupe scolaire,
  Collège, EHPAD, Gymnase, Hôpital, Hôtel, Industrie, Laboratoire, Logements, Lycée, Parking, Stade

### Round 2 — Sous-totaux ratio €/m²
- **[VAL-8-07]** Sous-totaux sections : fonctionnent aussi pour les sections €/m² (pas seulement unitaires)
- **[VAL-8-08]** Chapitre coché + qty=0 → ratio €/m² utilisé comme total de fallback

### Round 3 → 4 — Cohérence chapitre/sections
- **[VAL-8-09]** Total chapitre = Σ sous-totaux sections (architecture section-first)
- **[VAL-8-10]** Règle coche section/chapitre clarifiée et implémentée :
  - Section cochée + chapitre coché : Σ lignes cochées qty>0 OU ratio fallback si toutes qty=0
  - Section ou chapitre décoché : aucun sous-total, non comptabilisé

### Round 5 — Dashboard
- **[VAL-8-11]** Total dashboard = total calculateur (colonne `total_estime_ht` + COALESCE)
- **[VAL-8-12]** `currentTotal` JS global envoyé à chaque sauvegarde

---

## ✅ Points validés — Sprint 7 (2026-04-18) [IMMUABLE]

- **[VAL-7-01]** `<thead>` sticky (top: 52px sous la topbar)
- **[VAL-7-02]** Checkboxes chapitre / section → inclusion/exclusion bloc
- **[VAL-7-03]** Initialisation : articles qty=0, têtes chapitre qty=1 + mode Macro actif
- **[VAL-7-04]** Chargement affaire existante STRICT (pas de ré-injection)
- **[VAL-7-05]** Logique hybride Macro : ratio €/m² prime si chapitre coché (§3.2 instructions.md)
- **[VAL-7-06]** Badge cliquable MACRO ↔ DÉTAIL par chapitre
- **[VAL-7-07]** Affichage ratio €/m² par chapitre + par section (avec fallback référentiel)
- **[VAL-7-08]** Décorrélation SDO : unités u/ens/ml inchangées sur changement SDO
- **[VAL-7-09]** Charte graphique Egis (`.app-header`, `.data-table`, `.card-cfo/cfa/pv`)
- **[VAL-7-10]** Table `affaire_chapter_settings` + API `/api/affaire/<id>/chapter_settings`
- **[VAL-7-11]** Unité éditable par affaire (`unit_override` dans `affaire_lines`)
- **[VAL-7-12]** Fiche des écarts de prix (popup `correction-popup` si PU > +20% référence)
- **[VAL-7-13]** Popup "Corriger la base" → écrit dans `ratio_overrides` (global)

---

## Schéma BDD complet (v8)

```sql
-- Tables principales
affaires (
    id, name, client, adresse, date_creation, surface_sdo,
    category_id, coef_complexity_cfo, coef_complexity_cfa, coef_complexity_pv,
    coef_risque, taux_marge, statut, notes, created_at, updated_at,
    kva_cible, puissance_pv_kwc, pv_system_type,  -- PV : kWc + type toiture/IB/ombrière
    phase_etude, taux_incertitude, taux_phase,
    total_estime_ht          -- Sprint 8 : total effectif (ratio fallback inclus)
)
affaire_lines (
    id, affaire_id, dpgf_article_id,  -- nullable Sprint 9.4 (hors catalogue)
    quantity, unit_price_ht, total_ht, is_included,
    quantity_source, unit_price_source, created_at,
    unit_override, unit_source,  -- Sprint 7
    line_designation, line_lot   -- Sprint 9.4 sur-mesure
)
affaire_chapter_settings (   -- Sprint 7
    id, affaire_id, chapter_key, is_included, use_macro,
    qty, ratio_m2_override, updated_at
)
building_categories (id, name)
dpgf_articles (id, designation, lot, ratio_type, unit, ...)
ratio_overrides (id, dpgf_article_id, pu_override, raison, created_at)
```

---

## Architecture fichiers (delta Sprint 8)

```
├── app.py
│   ├── filtre date_fr  (YYYY-MM-DD → JJ-MM-AAAA)
│   ├── route /affaire/<id>/edit  (GET/POST — édition fiche)
│   ├── affaire_view : calcul ratio_ref_m2 AVANT écrasement par valeurs sauvegardées
│   └── affaire_save : appel save_total_estime si 'total_estime' dans payload
│
├── models.py
│   ├── migration Sprint 8 : ALTER TABLE affaires ADD COLUMN total_estime_ht REAL
│   ├── migration building_categories : 15 types (FK-safe PRAGMA foreign_keys OFF/ON)
│   ├── update_affaire_params : ALLOWED_STR inclut 'category_id'
│   ├── save_total_estime(affaire_id, total) : écrit total_estime_ht
│   └── get_affaires() : COALESCE(total_estime_ht, SUM(affaire_lines.total_ht), 0)
│
├── templates/affaire.html
│   ├── Lot detection : {% set desig_lower = chapter.designation | lower %}
│   ├── Date FR : {{ ... | date_fr }}
│   ├── Lien retour : "✏️ Modifier la fiche" → /affaire/<id>/edit
│   ├── Sélecteur bâtiment dans provisions-bar
│   ├── section-subtotal span par section
│   ├── data-ratio-ref sur .section-ratio et .chapter-ratio
│   └── section-sub-{chapId}-{idx} ids pour JS
│
├── templates/affaire_new.html
│   ├── Mode edit_mode (titre conditionnel, action conditionnelle)
│   ├── Pré-remplissage de tous les champs
│   └── Bouton conditionnel Créer / Enregistrer
│
├── static/js/affaire.js
│   ├── let currentTotal = 0  (global, mis à jour dans updateAllTotals)
│   ├── updateAllTotals : architecture section-first complète
│   │   ├── chapterInclusion : DOM-priority (checkbox > CHAPTER_STATE)
│   │   ├── Σ lignes cochées qty>0 par section → displaySectSum
│   │   ├── Fallback : sectSum=0 → refPerM2 × currentSDO
│   │   └── Total chapitre = Σ displaySectSum sections
│   ├── saveParam(key, value) : helper auto-save paramètre via /api/params
│   └── saveAffaire() : envoie { lines, total_estime: currentTotal }
│
└── static/css/style.css
    └── .section-subtotal (float:right, bold, tabular-nums)
```

---

## Routes Flask complètes

| Route | Méthode | Description |
|---|---|---|
| `/` | GET | Dashboard — liste affaires |
| `/affaire/new` | GET/POST | Créer affaire → redirection `/affaire/<id>/estimation` |
| `/affaire/<id>/edit` | GET/POST | Fiche affaire (preview CFO/CFA/PV, slider PV) |
| `/api/affaire/preview_estimation` | GET | **2026-05-23** — Preview bibliothèque (pu_ht_ref) |
| `/affaire/<id>` | GET | Calculateur DPGF |
| `/affaire/<id>/estimation` | GET | **Sprint 9.4** — Saisie estimation (double calque) |
| `/api/affaire/<id>/save` | POST | Sauvegarder lignes + total_estime |
| `/api/affaire/<id>/estimation/save` | POST | **Sprint 9.4** — Sauvegarde incrémentale page Estimation |
| `/api/affaire/<id>/params` | POST | Auto-save paramètres (10 champs dont category_id) |
| `/api/affaire/<id>/chapter_settings` | POST | Auto-save checkbox/mode Macro |
| `/api/affaire/<id>/export` | GET | Export Excel + récap provisions |
| `/api/affaire/<id>/delete` | POST | Supprimer affaire |
| `/api/ratios` | GET | Recalcul ratios (sdo, ccfo, ccfa, cpv) |

---

## Contrôle fonctionnel (2026-04-19)

```
[OK] Migration total_estime_ht → colonne présente en BDD
[OK] save_total_estime(id, 999999) → get_affaires()[0]['total_ht'] == 999999
[OK] COALESCE fallback → SUM(affaire_lines) si total_estime_ht IS NULL
[OK] Route /affaire/<id>/edit → répond 200
[OK] Filtre date_fr : '2026-04-19' → '19-04-2026'
[OK] Lot detection CFA/PV : | lower sur chapitre designation
```

---

## ✅ Session 2026-04-26

### Bibliothèque DPGF (nouveau)
- **[VAL-9-01]** Route `/bibliotheque` et `/bibliotheque/<id>` — explorateur du référentiel
- **[VAL-9-02]** Template `bibliotheque.html` standalone — design Egis Elec-Explorer
- **[VAL-9-03]** Tree-grid Chapitre → Section → Article avec colonnes fixes
- **[VAL-9-04]** Panneau inspecteur latéral : PU éditable → `ratio_overrides`
- **[VAL-9-05]** KPI strip CFO/CFA/PV + SDO modifiable (recalcul temps réel)
- **[VAL-9-06]** Sidebar : liste affaires pour contextualiser les prix
- **[VAL-9-07]** `get_bibliotheque_data(affaire_id)` ajouté dans `models.py`
- **[VAL-9-08]** Lien 📖 Bibliothèque DPGF ajouté dans `base.html`

### Lanceur corrigé
- **[VAL-9-09]** `Lancer_Estimateur.bat` : `cmd /k` → fenêtre reste ouverte si crash
- **[VAL-9-10]** Délai adaptatif : boucle netstat jusqu'à 20 s (au lieu de timeout fixe 3 s)

---

## Session 2026-05-02 — Bibliothèque DPGF : suppression de section (sous-chapitre)

**Contexte** : une tentative précédente de suppression de sous-chapitres avait provoqué une régression (persistance incohérente / état BDD douteux).

**Causes identifiées et mesures** :

1. **Filtrage Python trop strict dans `save_bibliotheque_save`** : la condition `if field != 'section_ratio' and not art_id: continue` **ignorait silencieusement** les payloads `section_delete`, `section_ratio_rename` (et tout champ sans `id`), car `id` est `null` côté JSON. **Correction** : ensemble explicite `_FIELDS_NO_ARTICLE_ID` pour autoriser ces champs sans `art_id`.
2. **Pas de rollback explicite** : en cas d’erreur au milieu du batch bibliothèque, la connexion pouvait laisser une transaction non validée de façon peu claire. **Correction** : `except` → `conn.rollback()` puis relance de l’exception dans `save_bibliotheque_save` ; même principe pour `delete_custom_article`.
3. **Ordre de suppression vs FK** : les articles `dpgf_articles` sont référencés par `affaire_lines`, `ratio_overrides`, et (schéma import) `devis_lines`, `mapping_synonyms`, `mapping_knowledge`. **Correction** : fonction centralisée `delete_bibliotheque_section(conn, chapter, section)` — nettoyage ordonné, puis DELETE custom / `is_hidden` PSA, puis `bibliotheque_section_ratios`.
4. **`PRAGMA foreign_keys`** : vérification obligatoire via `_verify_foreign_keys_enabled(conn)` avant les suppressions bibliothèque (chaque connexion : `get_db()` exécute déjà `PRAGMA foreign_keys = ON`).
5. **Côté JS** : `deleteSection` envoie **toujours** `section_delete` (y compris pour ne garder que la cohérence `bibliotheque_section_ratios`) ; `try/catch` + `console.error` autour de la file de save et de `render()` pour tracer une anomalie sans casser le flux silencieusement.
6. **Documentation** : fichier `DB_ARCHITECTURE.md` — SQLite, FK par connexion, cascades manuelles.
7. **Bug bloquant UI (corrigé)** : `localData` était déclaré en **`const`** alors que `deleteSection` réassigne avec `localData = localData.filter(...)` → erreur console `TypeError: Assignment to constant variable` (~l.659), arrêt du script **avant** `dirtyMap` / `schedSave()` / `render()`. **Correction** : déclaration **`let localData`**. Les arguments `chapter` et `section` passés à `deleteSection(chap, sec)` depuis le template sont inchangés ; le payload `section_delete` inclut bien `chapter` et `section`.

**Fichiers touchés** : `models.py`, `static/js/bibliotheque.js` (logique suppression section + logs + `let localData`), `DB_ARCHITECTURE.md`, `CURRENT_STATE.md`. **Aucun changement CSS** ; scroll table bibliothèque inchangé.

---

## Problèmes connus résiduels

1. **200 articles sans ratio** (`AUCUNE_REF`) → qty=0, pu=0. Bouton "Masquer zéros" disponible.
2. Le `chapter_settings` est auto-sauvé en debounce 600 ms ; pas de feedback visuel.
3. `total_estime_ht` est `NULL` pour les affaires jamais sauvegardées depuis Sprint 8
   → COALESCE se rabat sur `SUM(affaire_lines)` automatiquement.

### Règle de sauvegarde Git validée

À partir du 2026-05-25, les bases SQLite doivent être incluses dans les sauvegardes Git : `estimation_elec.db` et `estimation.db` si présent. Les fichiers Excel, captures d'écran et backups locaux restent exclus sauf demande explicite.
