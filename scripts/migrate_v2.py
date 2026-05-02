"""
migrate_v2.py - Migration schéma v2 : Modèle Maître Évolutif
Sprint 3 — Full Unitaire

Colonnes ajoutées :
  dpgf_articles :
    - is_custom       INTEGER  (1 = créé par l'utilisateur, non présent dans l'Excel original)
    - version_model   TEXT     (version du Modèle Maître au moment de la création)
    - pu_ht_ref       REAL     (PU HT de référence consolidé, calculé après mapping)
    - densite_qte_sdo REAL     (densité typique : Qte / SDO, calculée après mapping)
  devis_lines :
    - sub_chapter_context TEXT (clé du sous-chapitre pour le bloc de correction reliquat)

Migration de données :
  - Tous les articles is_virtual=1 existants deviennent aussi is_custom=1
    (le pivot Full Unitaire unifie les deux concepts)

Usage :
  python scripts/migrate_v2.py
"""
import sys
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from init_db import init_database

# ── DDL : nouvelles colonnes (ALTER TABLE — non-destructif) ───────────────────

SCHEMA_MIGRATIONS = [
    # dpgf_articles — Modèle Maître Évolutif
    (
        "ALTER TABLE dpgf_articles ADD COLUMN is_custom INTEGER NOT NULL DEFAULT 0",
        "dpgf_articles.is_custom",
    ),
    (
        "ALTER TABLE dpgf_articles ADD COLUMN version_model TEXT DEFAULT '1.0'",
        "dpgf_articles.version_model",
    ),
    (
        "ALTER TABLE dpgf_articles ADD COLUMN pu_ht_ref REAL",
        "dpgf_articles.pu_ht_ref",
    ),
    (
        "ALTER TABLE dpgf_articles ADD COLUMN densite_qte_sdo REAL",
        "dpgf_articles.densite_qte_sdo",
    ),
    # devis_lines — bloc de correction sous-chapitre
    (
        "ALTER TABLE devis_lines ADD COLUMN sub_chapter_context TEXT",
        "devis_lines.sub_chapter_context",
    ),
]

# ── DML : migration des données existantes ────────────────────────────────────

DATA_MIGRATIONS = [
    (
        "UPDATE dpgf_articles SET is_custom = 1 WHERE is_virtual = 1",
        "is_virtual -> is_custom : articles utilisateur existants",
    ),
]


def run():
    conn = init_database()
    print()
    print("=" * 60)
    print("  MIGRATION v2 - Modele Maitre Evolutif")
    print("=" * 60)

    # ── Phase 1 : ajout des colonnes ──────────────────────────
    print("\n  [DDL] Ajout des colonnes :")
    for sql, label in SCHEMA_MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
            print(f"    [OK] {label}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"    [--] {label}  (existe deja - ignore)")
            else:
                print(f"    [ERR] {label} : {e}")
                conn.close()
                sys.exit(1)

    # Phase 2 : migration des donnees
    print("\n  [DML] Migration des donnees :")
    for sql, label in DATA_MIGRATIONS:
        conn.execute(sql)
        conn.commit()
        n = conn.execute("SELECT changes()").fetchone()[0]
        print(f"    [OK] {label}  -> {n} ligne(s) mise(s) a jour")

    # Rapport final
    print()
    n_custom  = conn.execute(
        "SELECT COUNT(*) FROM dpgf_articles WHERE is_custom = 1"
    ).fetchone()[0]
    n_virtual = conn.execute(
        "SELECT COUNT(*) FROM dpgf_articles WHERE is_virtual = 1"
    ).fetchone()[0]
    print(f"  Articles dpgf_articles.is_custom=1  : {n_custom}")
    print(f"  Articles dpgf_articles.is_virtual=1 : {n_virtual}")

    # Verifier que sub_chapter_context existe bien
    cols = [
        r[1] for r in conn.execute("PRAGMA table_info(devis_lines)").fetchall()
    ]
    ctx_ok = "sub_chapter_context" in cols
    print(f"  devis_lines.sub_chapter_context     : {'OK' if ctx_ok else 'ABSENT - erreur'}")

    conn.close()
    print()
    print("  Migration terminee. Prochaine etape : re-import PSA.")
    print("=" * 60)
    print()


if __name__ == "__main__":
    run()
