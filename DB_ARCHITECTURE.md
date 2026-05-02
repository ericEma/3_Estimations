# DB_ARCHITECTURE — Estimation Élec (SQLite)

**Moteur** : SQLite 3 (`estimation_elec.db`), accès via `sqlite3` natif (pas d’ORM).

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

- Aujourd’hui **`dpgf_article_id` est `NOT NULL`** : toute ligne d’estimation doit pointer vers un article du référentiel (y compris articles **custom** créés dans `dpgf_articles`).
- Si le métier exige des **lignes sur-mesure sans ligne `dpgf_articles`**, il faudrait une **migration** (FK nullable + contrainte `CHECK` + champs snapshot sur la ligne, ou politique « toujours créer un `dpgf_articles` minimal »). Toute évolution doit rester **FK-safe** (`PRAGMA foreign_keys`, tests sur `save_affaire_lines` / UI).

## Fichiers de référence

| Fichier        | Rôle                                              |
|----------------|---------------------------------------------------|
| `models.py`    | Connexion, `get_db()`, suppressions bibliothèque  |
| `schema.sql`   | Schéma initial (import, `devis_lines`, etc.)    |
| `init_db.py`   | Création / vérif, `PRAGMA foreign_keys = ON`    |

---

**Jalon projet** : description alignée sur l’état **2026-05-02** (Bibliothèque DPGF validée côté UX ; suppression de section et transactions décrites ci-dessus opérationnelles). Voir aussi `CURRENT_STATE.md`.
