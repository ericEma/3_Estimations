# CURRENT_STATE.md — Estimation Élec
**Mise à jour : 2026-04-26 | Sprint 8 LIVRÉ + Session 26/04 : Bibliothèque DPGF + Lanceur corrigé**

---

## Statut global

**Projet** : ESTIMATION ÉLEC (EGIS) — Phase Bêta
**Sprint 8** : ✅ Livré — Corrections QC Eric (5 rounds de retours)
**Prochain** : Sprint 9 — À définir selon retours Eric post-sprint 8

---

## État technique

| Composant | État |
|---|---|
| Serveur Flask | Opérationnel (`Lancer_Estimateur.bat`) |
| Base SQLite | **v8** (colonne `total_estime_ht` ajoutée par migration auto) |
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
    kva_cible, phase_etude, taux_incertitude, taux_phase,
    total_estime_ht          -- Sprint 8 : total effectif (ratio fallback inclus)
)
affaire_lines (
    id, affaire_id, dpgf_article_id, designation, lot, ratio_type,
    unit, quantity, unit_price_ht, total_ht, is_included,
    quantity_source, unit_price_source, created_at, updated_at,
    unit_override, unit_source  -- Sprint 7
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
| `/affaire/new` | GET/POST | Créer affaire + injecter 285 lignes |
| `/affaire/<id>` | GET | Calculateur DPGF |
| `/affaire/<id>/edit` | GET/POST | **Sprint 8** — Édition fiche affaire |
| `/api/affaire/<id>/save` | POST | Sauvegarder lignes + total_estime |
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

## Problèmes connus résiduels

1. **200 articles sans ratio** (`AUCUNE_REF`) → qty=0, pu=0. Bouton "Masquer zéros" disponible.
2. Le `chapter_settings` est auto-sauvé en debounce 600 ms ; pas de feedback visuel.
3. `total_estime_ht` est `NULL` pour les affaires jamais sauvegardées depuis Sprint 8
   → COALESCE se rabat sur `SUM(affaire_lines)` automatiquement.
