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

## Fichiers de référence

| Fichier        | Rôle                                              |
|----------------|---------------------------------------------------|
| `models.py`    | Connexion, `get_db()`, suppressions bibliothèque  |
| `schema.sql`   | Schéma initial (import, `devis_lines`, etc.)    |
| `init_db.py`   | Création / vérif, `PRAGMA foreign_keys = ON`    |

---

**Jalon projet** : description alignée sur l’état **2026-05-02** (Bibliothèque DPGF validée côté UX ; suppression de section et transactions décrites ci-dessus opérationnelles). Voir aussi `CURRENT_STATE.md`.
