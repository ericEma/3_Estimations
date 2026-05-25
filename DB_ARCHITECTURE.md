# DB_ARCHITECTURE — Estimation Élec (SQLite)

**Moteur** : SQLite 3 (`estimation_elec.db`), accès via `sqlite3` natif (pas d’ORM).

**État documenté** : 2026-05-25 — fiche affaire, page estimation snapshot/layout, bibliothèque DPGF et matching.

## Foreign keys

- SQLite **n’active pas** les contraintes de clés étrangères par défaut.
- **À chaque nouvelle connexion**, le code applicatif exécute `PRAGMA foreign_keys = ON` (voir `models.get_db()`, `init_db.py`).
- Les migrations ponctuelles qui modifient des tables avec FK peuvent utiliser `PRAGMA foreign_keys = OFF` puis `ON` (voir `ensure_app_tables()` dans `models.py`) — ce n’est pas l’état nominal en production.

## Cascade et suppressions

- Il n’y a **pas** de `ON DELETE CASCADE` systématique sur toutes les tables liées à `dpgf_articles` (ex. `devis_lines`, `mapping_knowledge`, `mapping_synonyms`, `affaire_lines`, `ratio_overrides`).
- Les suppressions « sensibles » sont donc gérées **manuellement** dans `models.py`, dans un ordre compatible avec les FK :
  - suppression / mise à jour des lignes **dépendantes** ;
  - puis suppression ou mise à jour des lignes `dpgf_articles`.

### Bibliothèque DPGF — suppression d’une section (sous-chapitre)

- Il n’existe **pas** de table `section` : la section est identifiée par le couple `(chapter, section)` sur `dpgf_articles.row_type = 'article'`.
- La fonction `delete_bibliotheque_section(conn, chapter, section)` :
  - nettoie `affaire_lines` et `ratio_overrides` pour **tous** les articles de la section ;
  - pour les articles **custom** supprimés physiquement : `devis_lines.dpgf_article_id` mis à `NULL` si la table existe, puis suppression des entrées `mapping_synonyms` / `mapping_knowledge` concernées ;
  - supprime les lignes custom dans `dpgf_articles` ;
  - masque les articles PSA (`is_hidden = 1`) ;
  - supprime la ligne correspondante dans `bibliotheque_section_ratios`.
- Les sauvegardes batch `save_bibliotheque_save` utilisent **`conn.commit()` en fin de lot** et **`conn.rollback()`** en cas d’exception pour éviter une base à moitié mise à jour.

### Article custom unitaire

- `delete_custom_article` applique la même logique de nettoyage des FK que pour les articles custom dans une section.

---

## Module Affaires & Articles (calculateur d’estimation)

> Synthèse utile pour les évolutions schéma / règles métier. **Source de vérité** : `CREATE TABLE` dans `models.py` (`ensure_app_tables`) + migrations `ALTER` listées dans le même fichier.

### Tables liées aux **affaires**

| Table | Rôle |
|--------|------|
| `affaires` | Fiche projet (SDO, coefficients, phase, taxes, `total_estime_ht`, lien `category_id`, etc.). |
| `affaire_lines` | **Pivot** affaire ↔ référentiel : quantités et PU **par affaire** sans modifier `dpgf_articles`. |
| `affaire_chapter_settings` | Inclusion chapitre, mode macro, qty tête, override ratio pour une affaire. |
| `building_categories` | Types de bâtiments ; référencée par `affaires.category_id`. |

### Tables liées aux **articles** (référentiel & global)

| Table | Rôle |
|--------|------|
| `dpgf_articles` | Référentiel DPGF (chapitre, section, unité, ratio, `qty_ref`, `is_custom`, `is_hidden`, …). |
| `ratio_overrides` | Correction de **prix de base** au niveau **article** (impact toutes les affaires utilisant cet article). |
| `bibliotheque_section_ratios` | Ratios €/m² ou €/kWc par **section de bibliothèque** (pas par affaire). |

### `affaire_lines` = table pivot « type détail affaire »

- Elle joue déjà le rôle d’un **`affaire_details` / items de devis** : `affaire_id`, `dpgf_article_id`, `quantity`, `unit_price_ht`, `quantity_source` / `unit_price_source`, `total_ht`, `is_included`, `unit_override`, `unit_source`, etc.
- Les champs **désignation / lot / type ratio** affichés au calculateur proviennent en pratique d’un **JOIN sur `dpgf_articles`** : ils ne sont en général **pas** dupliqués comme colonnes sur `affaire_lines` dans le schéma actuel.

### Limite actuelle & évolution possible (non implémentée)

- Aujourd’hui **`dpgf_article_id` peut être NULL** pour les lignes affaire-only de la page Estimation (`line_chapter`, `line_section`, `line_designation`). Les lignes catalogue restent liées à `dpgf_articles`.
- Les lignes sur-mesure hors arbre ont été masquées côté UI estimation; les lignes affaire-only de l'arbre peuvent ensuite être promues vers `dpgf_articles`. Toute évolution doit rester **FK-safe** (`PRAGMA foreign_keys`, tests sur `save_affaire_lines` / UI).

## Fichiers de référence

| Fichier        | Rôle                                              |
|----------------|---------------------------------------------------|
| `models.py`    | Connexion, `get_db()`, suppressions bibliothèque  |
| `schema.sql`   | Schéma initial (import, `devis_lines`, etc.)    |
| `init_db.py`   | Création / vérif, `PRAGMA foreign_keys = ON`    |

---

**Jalon projet** : description alignée sur l’état **2026-05-02** (Bibliothèque DPGF validée côté UX ; suppression de section et transactions décrites ci-dessus opérationnelles). Voir aussi `CURRENT_STATE.md`.
---

## État applicatif — 2026-05-25

### Flux principaux

| Flux | Frontend | Backend/API | Tables principales | Rôle |
|---|---|---|---|---|
| Dashboard affaires | `templates/index.html` | `/`, `/api/affaire/<id>/delete` | `affaires`, `affaire_lines` | Liste des affaires, bouton `Fiche`, bouton `Estimer`, suppression affaire. |
| Fiche affaire | `templates/affaire_new.html`, `complexity_scale.js`, `pv_system_scale.js` | `/affaire/new`, `/affaire/<id>/edit`, `/api/affaire/preview_estimation` | `affaires`, `building_categories` | Paramétrage projet, ratios globaux CFO/CFA/PV, preview budgétaire. |
| Page Estimation | `templates/affaire_estimation.html`, `static/js/affaire_estimation.js` | `/affaire/<id>/estimation`, `/api/affaire/<id>/estimation/save`, `/params`, `/chapter_settings`, `/estimation/layout`, `/estimation/promote` | `affaires`, `affaire_lines`, `affaire_chapter_settings`, `affaire_estimation_section_sort`, `dpgf_articles` | Saisie snapshot par affaire, macro sections, layout local, promotion base de prix. |
| Bibliothèque DPGF | `templates/bibliotheque.html`, `static/js/bibliotheque.js` | `/bibliotheque`, `/bibliotheque/<id>`, `/api/bibliotheque/save`, `/api/bibliotheque/article/delete` | `dpgf_articles`, `ratio_overrides`, `bibliotheque_section_ratios`, `affaire_lines` | Référentiel de prix, ratios section, ajouts custom, suppression/masquage, déplacement section. |
| Matching import devis | `templates/matching_view.html`, `static/js/matching.js` | `/matching`, `/api/matching/*` | `projects`, `devis_lines`, `dpgf_articles`, `mapping_*` | Revue post-import, affectation DPGF, PU pondéré manuel. |

### Colonnes clés ajoutées sur `affaires`

- `kva_cible` : puissance TGBT indicative.
- `puissance_pv_kwc` : diviseur PV en kWc.
- `pv_system_type` : `toiture`, `ib`, `ombriere`.
- `phase_etude`, `taux_phase`, `taux_incertitude`, `coef_risque` : provisions cumulatives.
- `ratio_global_cfo_m2`, `ratio_global_cfa_m2`, `ratio_global_pv_kwc` : ratios fiche affaire pour la preview uniquement.
- `estimation_initialized_at` : marque le snapshot page estimation.
- `total_estime_ht` : total dashboard sauvegardé, avec fallback COALESCE.

### `affaire_lines` — snapshot et layout local

`affaire_lines` accepte désormais deux familles de lignes :

- **Lignes catalogue snapshot** : `dpgf_article_id` renseigné, `ratio_ref` figé, `quantity`, `unit_price_ht`, `line_designation` override optionnel.
- **Lignes arbre affaire-only** : `dpgf_article_id` NULL, `line_chapter`, `line_section`, `line_designation`, `unit_override`, `line_lot`, `sort_order`. Elles servent aux sections/articles créés dans une affaire avant promotion.

Règle importante : une affaire initialisée ne resynchronise pas ses prix depuis la bibliothèque; `ratio_ref` est la référence de l'affaire.

### `affaire_chapter_settings`

- Clés `chap:<chapter>` : inclusion chapitre.
- Clés `sect:<chapter>|<section>` : inclusion section, `use_macro`, `qty`, `ratio_m2_override`, `is_local`.
- Macro section : si aucun article de la section n'a un total positif, total = `ratio_m2_override × qty`; sinon total = somme articles.

### `affaire_estimation_section_sort`

- Table d'ordre local par affaire : `(affaire_id, chapter, section, sort_order)`.
- Source pour afficher la page Estimation et pour promouvoir une section au bon endroit dans `dpgf_articles.row_order`.

### `dpgf_articles.row_order`

- La bibliothèque trie par `chapter, row_order`.
- Les déplacements de section dans `/bibliotheque` réécrivent les `row_order` visibles du chapitre.
- La promotion d'une section locale décale les `row_order` suivants pour insérer la nouvelle section à la position validée côté estimation.

### Garde-fous avant évolution

1. Ne pas mélanger la fiche affaire (preview vivante) et la page estimation (snapshot figé).
2. Ne pas remplacer une logique section-first par une somme chapitre directe.
3. Ne pas supprimer physiquement les articles PSA : utiliser `is_hidden=1`.
4. Toute migration touchant une table avec FK doit rester FK-safe et testée.
5. Lancer au minimum :
   - `python -m unittest tests.test_affaire_preview_ratios tests.test_estimation_snapshot tests.test_estimation_promote tests.test_bibliotheque_section_move -v`
   - `node --check static/js/affaire_estimation.js`
   - `node --check static/js/bibliotheque.js`

### Versionnement Git des bases

Décision Eric du 2026-05-25 : les fichiers SQLite (`estimation_elec.db`, et `estimation.db` si présent) sont versionnés avec le code afin de sauvegarder l'état réel de l'application et des données de référence.
