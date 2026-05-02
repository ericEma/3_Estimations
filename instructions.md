# Instructions Projet : Estimation Élec (Egis)

## 1. Rôles et Responsabilités
- **Claude Code** : Développeur Fullstack expert Python/SQL. Doit vérifier la cohérence des calculs avant de valider.
- **Utilisateur (Eric)** : Ingénieur Électricien, garant des règles métiers et de la validation finale.

---

## 2. Règles d'Ingénierie Immuables (Sprint 6+)

### 2.1 Paliers de Phase d'étude (Egis)
| Phase | Taux par défaut |
|---|---|
| DIAG | **6 %** |
| APS  | **4 %** |
| APD  | **3 %** |
| PRO  | **1 %** |

### 2.2 Provisions par défaut
- **Taux d'Incertitude** : 3 % (indépendant de la phase, modifiable)
- **Taux de Risque / Aléa** : 1 % (modifiable, plage 0–30 %)

### 2.3 Formule cumulative (3 taxes indépendantes)
```
Total Majoré = Total HT Sec × (1 + Phase/100) × (1 + Incertitude/100) × (1 + Risque/100)
```
Les trois taxes **ne s'écrasent jamais** les unes les autres.

### 2.4 Logique SDO (Surface) — Décorrélation des unités
- Unités **fixes** (indépendantes de la SDO) : `u`, `ens`, `ml`
- Unités **liées dynamiquement à la SDO** : `m²` + toutes les **têtes de chapitre / sous-chapitre**
- Un changement de SDO ne doit recalculer **que** les lignes surfaciques et les têtes de chapitre.

### 2.5 Priorité de Saisie — Cellule manuelle (Orange)
- Une cellule modifiée manuellement passe en **fond orange `#f5a623` + bord lime**.
- Elle **ignore toute recalculation automatique** (ratios, complexité, SDO) jusqu'à réinitialisation explicite.
- Attribut DOM : `data-source="manual"`.

### 2.6 Zéro LaTeX
- Toutes les formules et textes affichés (UI, docs, exports) sont en **texte brut ou Markdown**.
- Pas de `$...$`, `\frac`, ni aucune syntaxe TeX.

---

## 3. Hiérarchie et Structure du Référentiel (Master DPGF)

### 3.1 Mapping des colonnes Excel (1_DPGF_Modele.xlsx)
| Colonne | Contenu          | Valeurs attendues                  |
|---------|------------------|------------------------------------|
| Col B   | Type Ratio       | `SURFACIQUE`, `UNITAIRE`, `KWP`    |
| Col C   | Nature           | `TITRE`, `ARTICLE`                 |
| Col D   | Désignation      | Clé textuelle (base du fuzzy match)|
| Col E   | Unité            | m², u, ml, kWp, ens…               |

### 3.2 Logique de Titre (Roll-Up et Priorité Macro)
- Les lignes `TITRE` servent de **points de regroupement**.
- L'import calcule un **ratio global de chapitre** en sommant les montants des articles enfants.

**Règle de priorité Macro (Sprint 7) :**
> Si la tête de chapitre est **cochée** et que le détail des articles est **décoché**, le montant du chapitre se calcule depuis le **ratio €/m² propre** de la tête de chapitre. Les articles enfants décochés sont alors neutralisés (informatif seulement).

### 3.3 Lot detection — Règle critique (Sprint 8)
La détection CFO/CFA/PV se fait en **comparaison case-insensitive** via `| lower` en Jinja2 :
```jinja2
{% set desig_lower = chapter.designation | lower %}
{% if 'faible' in desig_lower or 'cfa' in desig_lower %}
  {% set chap_lot = 'CFA' %}
{% elif 'photovolta' in desig_lower %}
  {% set chap_lot = 'PV' %}
{% else %}
  {% set chap_lot = 'CFO' %}
{% endif %}
```
**Ne jamais utiliser de comparaison sensible à la casse sur les désignations de chapitre.**

---

## 4. Architecture des Totaux — Section-First (Sprint 8)

### 4.1 Règle fondamentale
Le total d'un chapitre est **toujours la somme de ses sections**.
Il ne se calcule jamais indépendamment des sections.
Cela garantit la cohérence : total chapitre = Σ affichés dans le tableau.

### 4.2 Calcul d'une section
```
Pour chaque section :
  sectSum = Σ (qty × PU) pour les lignes cochées ET qty > 0
  
  Si sectSum > 0 :
    displaySectSum = sectSum
    ratio affiché = sectSum / SDO
  Sinon (toutes qty=0 ou section vide) :
    displaySectSum = refPerM2 × SDO   ← fallback référentiel
    ratio affiché = refPerM2 (depuis data-ratio-ref HTML)
    
  Si section décochée OU chapitre décoché :
    displaySectSum = 0   (non comptabilisé, sous-total effacé)
```

### 4.3 Calcul d'un chapitre
```
totalChapitre = Σ displaySectSum pour toutes ses sections
  (les sections décochées contribuent 0)
```

### 4.4 ratio_ref_m2 — Valeur de référence serveur
Calculé dans `affaire_view` **avant** d'écraser les articles avec les valeurs sauvegardées :
```python
sect_ref_total = sum(art.get('total') or 0 for art in section['articles'])
section['ratio_ref_m2'] = sect_ref_total / sdo_val
chapter['ratio_ref_m2'] = chap_ref_total / sdo_val
```
Transmis au JS via `data-ratio-ref="..."` sur les éléments `.section-ratio` et `.chapter-ratio`.

### 4.5 total_estime_ht — Persistance pour le dashboard
- `currentTotal` (JS global) est mis à jour en fin de chaque `updateAllTotals()`.
- Envoyé dans chaque POST `/api/affaire/<id>/save` : `{ lines, total_estime: currentTotal }`.
- `models.save_total_estime()` écrit en BDD dans `affaires.total_estime_ht`.
- `get_affaires()` lit via : `COALESCE(total_estime_ht, SUM(affaire_lines.total_ht), 0)`.

---

## 5. Règles Métiers Critiques

### 5.1 Neutralisation de la Complexité par Lot
- Tout prix unitaire entrant en base est **divisé par le coefficient de son lot** (base "Standard" 1.0).
- Trois coefficients indépendants : `coef_cfo`, `coef_cfa`, `coef_pv`
- `prix_unitaire_stocke = prix_unitaire_devis / coef_lot`

### 5.2 Logique Photovoltaïque (kWp) et SDO
- CFO / CFA : ratio surfacique sur **SDO** (€/m²).
- PV : ratio sur **puissance installée** en kWp (€/kWc).
- Bloquer l'import si `sdo_m2` est manquant.

### 5.3 Calcul d'Inflation (Actualisation)
- `Prix_2026 = Prix_Devis × (1 + Taux_Annuel) ^ (Annee_Courante - Annee_Devis)`
- Taux par défaut : `0.03`

---

## 6. Moteur de Mémoire (Pondération Temporelle)

| Âge du devis    | Poids |
|-----------------|-------|
| < 18 mois       | 1.0   |
| 18 à 36 mois    | 0.5   |
| > 36 mois       | 0.1   |

`ratio_moyen = Σ(ratio_i × poids_i) / Σ(poids_i)` après neutralisation complexité + actualisation.

---

## 7. Hygiène de Données et Sécurités

### 7.1 Arbitrage des lignes "ens"
Si le Master attend une unité physique mais que le devis indique `ens` :
1. Forcer une quantité manuelle, OU
2. Marquer `is_stat_valid = 0` (exclue des ratios)
La ligne est **toujours importée**, jamais ignorée silencieusement.

### 7.2 Alerte de Rareté
`nb_devis_recents < 3` → warning `⚠ RARETÉ : Ratio basé sur moins de 3 devis récents`

### 7.3 Vérifications Techniques Obligatoires
1. Typage SQL (Float vs String)
2. Équilibre financier : Σ(lignes) vs Total HT source (tolérance ±0.5 %)
3. Détection aberrations : prix > +300 % de la moyenne pondérée

### 7.4 Mapping Fuzzy
- Score de confiance `rapidfuzz` sur désignation
- Validation manuelle forcée si score < 80 %

### 7.5 Migration BDD — Règle FK-safe
Toute modification de table référencée par FK doit être entourée de :
```python
conn.execute("PRAGMA foreign_keys = OFF")
# ... DELETE / INSERT / UPDATE ...
conn.execute("UPDATE affaires SET fk_col = NULL WHERE fk_col NOT IN (SELECT id FROM ref_table)")
conn.execute("PRAGMA foreign_keys = ON")
conn.commit()
```

---

## 8. Stack Technique (immuable)
- **Python 3.10+**, **Flask 3.x**, **SQLite3 natif** (pas SQLAlchemy)
- **rapidfuzz** (fuzzy matching), **openpyxl** (Excel), **loguru** (logs)
- Palette CSS Egis : `--bg:#09212C` | `--accent:#D5F311` | `--accent2:#24A9B5` | `--lot:#804A3D`

---

## 9. Comportements à respecter

### 9.1 Auto-save des paramètres
- SDO, kVA, Phase, Taux Phase, Taux Incertitude, Taux Risque, Complexité CFO/CFA/PV, category_id
  → POST `/api/affaire/<id>/params` (debounce 600 ms)
- Recalcul ratios sur changement SDO → GET `/api/ratios?sdo=X&ccfo=Y&ccfa=Z&cpv=W` (debounce 500 ms)
- Seules les cellules `data-source != 'manual'` sont recalculées.

### 9.2 Injection initiale des 285 lignes
- À la création d'une affaire → `_initialize_affaire_lines()` dans `app.py`.
- Articles à qty=0 par défaut, têtes de chapitre à qty=1 (ratio m² actif).
- Toutes les lignes `is_included=True`. Bouton "Masquer zéros" pour épurer.

### 9.3 Chargement strict (Sprint 7)
- Si `affaire_lines` non vide → chargement STRICT des valeurs sauvegardées.
- Aucune ré-injection depuis les ratios si la ligne existe déjà en DB.

### 9.4 Protocole Checklist fin de tâche
1. Typage SQL correct ?
2. Équilibre financier vérifié ?
3. Aberrations de prix signalées ?
4. Prix stockés neutralisés (/ coef_lot) ?
5. `sdo_m2` renseigné ?
6. Alerte Rareté affichée si besoin ?
7. Mode Macro : articles enfants neutralisés si tête cochée et détail décoché ?
8. **Sprint 8** : Lot detection case-insensitive (`| lower`) ?
9. **Sprint 8** : `currentTotal` envoyé dans saveAffaire() ?

---

## 10. Gestion de la Mémoire Projet
- Tenir à jour `CURRENT_STATE.md` et `TODO.md` à chaque fin de sprint.
- Documenter les fonctions métier complexes (commentaires courts sur le **POURQUOI**).
- `loguru` pour tracer calculs et mappings.
- Générer un prompt de relance `Nouvelle_session.md` en fin de chaque session.
