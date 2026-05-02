# CLAUDE.md — Estimation Élec (Egis)
> Fichier unique de démarrage de session. Remplace la lecture de instructions.md + CURRENT_STATE.md.
> Mise à jour : 2026-05-02 | Sprint 9.2 — **Bibliothèque DPGF validée (jalon Eric, 2026-05-02)**

---

## 1. Rôles
- **Claude Code** : Développeur Fullstack Python/SQL. Vérifie la cohérence des calculs avant de valider.
- **Eric (utilisateur)** : Ingénieur Électricien, garant des règles métier et de la validation finale.

---

## 2. Stack Technique (immuable)
- **Python 3.10+**, **Flask 3.x**, **SQLite3 natif** (pas SQLAlchemy)
- **rapidfuzz** (fuzzy matching), **openpyxl** (Excel), **loguru** (logs)
- Palette CSS Egis : `--bg:#09212C` | `--accent:#D5F311` | `--accent2:#24A9B5` | `--lot:#804A3D`

---

## 3. Règles Métier Immuables

### 3.1 Paliers de Phase (Egis)
| Phase | Taux |
|---|---|
| DIAG | 6 % |
| APS  | 4 % |
| APD  | 3 % |
| PRO  | 1 % |

### 3.2 Provisions par défaut
- Taux d'Incertitude : 3 % | Taux Risque/Aléa : 1 % (plage 0–30 %)

### 3.3 Formule cumulative (3 taxes indépendantes)
```
Total Majoré = Total HT Sec × (1 + Phase/100) × (1 + Incertitude/100) × (1 + Risque/100)
```

### 3.4 Cellule manuelle (Orange)
- Fond `#f5a623` + attribut DOM `data-source="manual"`
- Ignore tout recalcul automatique (ratios, SDO, complexité) jusqu'à reset explicite

### 3.5 Lot Detection (case-insensitive — CRITIQUE)
```jinja2
{% set desig_lower = chapter.designation | lower %}
{% if 'faible' in desig_lower or 'cfa' in desig_lower %} → CFA
{% elif 'photovolta' in desig_lower %}                   → PV
{% else %}                                               → CFO
```
**Ne jamais comparer les désignations de chapitre de façon sensible à la casse.**

### 3.6 Architecture Section-First
- Total chapitre = **Σ sections** — jamais calculé indépendamment
- Section cochée + qty>0 → Σ(qty × PU)
- Section cochée + toutes qty=0 → `refPerM2 × SDO` (fallback référentiel)
- Section ou chapitre décoché → 0

### 3.7 Logique SDO
- Unités fixes (indépendantes SDO) : `u`, `ens`, `ml`
- Unités surfaciques (liées SDO) : `m²` + têtes de chapitre/section

### 3.8 Neutralisation Complexité
- `prix_stocké = prix_devis / coef_lot` (base Standard = 1.0)
- 3 coefficients indépendants : `coef_cfo`, `coef_cfa`, `coef_pv`

### 3.9 Actualisation des Prix
- `Prix_2026 = Prix_Devis × (1 + 0.03) ^ (2026 - Année_Devis)`

### 3.10 Pondération Temporelle des Ratios
| Âge devis | Poids |
|---|---|
| < 18 mois | 1.0 |
| 18–36 mois | 0.5 |
| > 36 mois | 0.1 |

### 3.11 Zéro LaTeX
Toutes les formules et textes UI/docs sont en texte brut ou Markdown. Pas de `$...$`, `\frac`.

---

## 4. Schéma BDD (v8 + Sprint 9.2)

```sql
affaires (id, name, client, adresse, date_creation, surface_sdo,
    category_id, coef_complexity_cfo, coef_complexity_cfa, coef_complexity_pv,
    coef_risque, taux_marge, statut, notes, created_at, updated_at,
    kva_cible, phase_etude, taux_incertitude, taux_phase, total_estime_ht)

affaire_lines (id, affaire_id, dpgf_article_id, designation, lot, ratio_type,
    unit, quantity, unit_price_ht, total_ht, is_included,
    quantity_source, unit_price_source, created_at, updated_at,
    unit_override, unit_source)

affaire_chapter_settings (id, affaire_id, chapter_key, is_included, use_macro,
    qty, ratio_m2_override, updated_at)

dpgf_articles (id, designation, lot, ratio_type, unit,
    is_custom,   -- 0=PSA master, 1=ajouté par utilisateur
    is_hidden,   -- soft-delete pour articles PSA
    qty_ref)     -- quantité de référence bibliothèque

ratio_overrides (id, dpgf_article_id, pu_override, raison, created_at)
building_categories (id, name)   -- 15 types de bâtiments

bibliotheque_section_ratios (id, chapter, section, ratio_m2, updated_at,
    UNIQUE(chapter, section))    -- ratios manuels par section bibliothèque
```

---

## 5. Routes Flask

| Route | Méthode | Description |
|---|---|---|
| `/` | GET | Dashboard — liste affaires |
| `/affaire/new` | GET/POST | Créer affaire + injecter 285 lignes |
| `/affaire/<id>` | GET | Calculateur DPGF |
| `/affaire/<id>/edit` | GET/POST | Édition fiche affaire |
| `/api/affaire/<id>/save` | POST | Sauvegarder lignes + total_estime |
| `/api/affaire/<id>/params` | POST | Auto-save paramètres |
| `/api/affaire/<id>/chapter_settings` | POST | Auto-save checkbox/mode Macro |
| `/api/affaire/<id>/export` | GET | Export Excel |
| `/api/affaire/<id>/delete` | POST | Supprimer affaire |
| `/api/ratios` | GET | Recalcul ratios (sdo, ccfo, ccfa, cpv) |
| `/bibliotheque` | GET | Bibliothèque DPGF (sans contexte affaire) |
| `/bibliotheque/<id>` | GET | Bibliothèque DPGF contextuelle affaire |
| `/api/bibliotheque/save` | POST | Sauvegarder éditions bibliothèque (debounce 800ms) |
| `/api/bibliotheque/article/delete` | POST | Supprimer (custom) ou masquer (PSA) article |

---

## 6. Architecture Fichiers Clés

```
app.py              — Routes Flask, filtre date_fr, affaire_view (ratio_ref_m2 avant écrasement)
models.py           — Couche SQLite : migrations auto, get_bibliotheque_data, save_bibliotheque_save,
                      delete_bibliotheque_section, _verify_foreign_keys_enabled,
                      hide_article, delete_custom_article, save_total_estime, get_affaires (COALESCE)
                      — voir DB_ARCHITECTURE.md (FK, suppressions manuelles)
engine_ratios.py    — compute_ratios(sdo, ccfo, ccfa, cpv) → ratios in-memory pondérés
templates/
  base.html         — Navbar avec lien 📖 Bibliothèque DPGF
  affaire.html      — Calculateur : lot detection, data-ratio-ref, section-subtotals
  affaire_new.html  — Fiche affaire (create/edit mode)
  bibliotheque.html — Tree-grid Chapitre→Section→Article, inline edit, KPI strip
static/js/
  affaire.js        — updateAllTotals (section-first), currentTotal global, saveAffaire
  bibliotheque.js   — render, inline edit, orange rule, delete, section ratio, validation
static/css/style.css
```

---

## 7. Comportements Auto-Save

- SDO, kVA, Phase, Incertitude, Risque, Complexité, category_id → `POST /api/affaire/<id>/params` (debounce 600 ms)
- Changement SDO → `GET /api/ratios` (debounce 500 ms) — recalcule uniquement `data-source != 'manual'`
- chapter_settings → `POST /api/affaire/<id>/chapter_settings` (debounce 600 ms, **sans feedback visuel**)
- Bibliothèque → `POST /api/bibliotheque/save` (debounce 800 ms)

---

## 8. Bibliothèque DPGF — Spécificités Sprint 9.2

### Chapitres (casse exacte DB)
```js
const CHAP_ORDER = ['Courants Forts', 'Courants faibles', 'Photovoltaïque'];
//                                              ^ lowercase f — valeur exacte en BDD
```

### Orange Rule Bibliothèque
- Cellule éditée → `data-source="manual"` + fond `#f5a623`
- Section ratio edité → classe `.manual` sur `.sec-ratio-cell`

### Logique Ratio €/m² Section
- Défaut : `Σ(Articles de la section) / SDO`
- Editable inline, persisté dans `bibliotheque_section_ratios`
- Sert de fallback macro pour estimation sans détail

### is_custom vs is_hidden
- `is_custom=1` → article créé par utilisateur → suppression physique (DELETE)
- `is_custom=0` → article PSA master → soft-delete (`is_hidden=1`)
- Filtre BDD : `AND (is_hidden IS NULL OR is_hidden = 0)`

### Validation Inline
- Désignation vide → erreur
- PU négatif ou NaN → erreur

### Jalon UX 2026-05-02 (validé Eric)
- **Scroll table** : dépend du layout flex `.bibl-page` + `min-height: 0` — ne pas casser sans accord.
- **`bibliotheque.js`** : état `localData` en **`let`** (réassignation dans `deleteSection`).
- **Suppression section** : payload `section_delete` ; backend `delete_bibliotheque_section` + transaction / rollback dans `save_bibliotheque_save`.
- Qty négative ou NaN → erreur
- Comportement : `.cell-error` (bordure rouge) + `scrollIntoView({behavior:'smooth', block:'center'})`
- Save bloqué tant qu'erreur présente

---

## 9. Règles Techniques Critiques

1. **Migration BDD FK-safe** :
   ```python
   conn.execute("PRAGMA foreign_keys = OFF")
   # ... modifications ...
   conn.execute("PRAGMA foreign_keys = ON")
   conn.commit()
   ```
2. **qty_ref** dans `dpgf_articles` = quantité de référence (bibliothèque, sans affaire)
3. **total_estime_ht** : NULL pour affaires pré-Sprint 8 → COALESCE vers `SUM(affaire_lines)`
4. **ratio_overrides** : global (impacte toutes les affaires utilisant le même article)
5. **`compute_ratios()`** retourne des ratios in-memory — pas de table `referentiel_ratios`

---

## 10. Problèmes Connus Résiduels

1. 200 articles `AUCUNE_REF` : qty=0, pu=0. Bouton "Masquer zéros" disponible.
2. chapter_settings auto-save sans feedback visuel "saving…" (TODO Sprint 9)
3. `total_estime_ht` NULL pour affaires jamais sauvegardées depuis Sprint 8 (COALESCE actif)
4. popup "Corriger la base" écrit dans `ratio_overrides` globalement — pas de rollback

---

## 11. Checklist Fin de Tâche

1. Typage SQL correct ?
2. Équilibre financier vérifié (tolérance ±0.5 %) ?
3. Lot detection case-insensitive (`| lower`) ?
4. Cellules manuelles protégées (`data-source != 'manual'` avant recalcul) ?
5. `currentTotal` envoyé dans `saveAffaire()` ?
6. Mode Macro : articles enfants neutralisés si tête cochée + détail décoché ?
7. Prix stockés neutralisés (`/ coef_lot`) ?
8. `sdo_m2` renseigné pour imports ?
9. Migration FK-safe si modification de table avec FK ?
