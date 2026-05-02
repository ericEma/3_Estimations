"""
migrate_v3.py - Migration schéma v3 : Coefficients par Lot + Validité Statistique
Sprint 3 — Intelligence Métier

Colonnes ajoutées :
  projects :
    - coef_cfo         REAL NOT NULL DEFAULT 1.0  (coef lot Courants Forts)
    - coef_cfa         REAL NOT NULL DEFAULT 1.0  (coef lot Courants Faibles)
    - coef_pv          REAL NOT NULL DEFAULT 1.0  (coef lot Photovoltaïque)
    - puissance_pv_kwp REAL NOT NULL DEFAULT 0.0  (puissance crête kWp)
  devis_lines :
    - is_stat_valid    INTEGER NOT NULL DEFAULT 1  (0 = exclu des ratios)
    - lot              TEXT                         (CFO | CFA | PV — dérivé du chapitre)

Nouvelle table :
  mapping_synonyms : association désignation entreprise ↔ article DPGF

Migration de données :
  - Pour chaque projet existant : coef_cfo = coef_cfa = coef_pv = coef_complexite
  - Pour chaque ligne devis_lines existante : lot déduit du context_path
  - coef_complexite est supprimée (ou gardée en fallback si SQLite trop ancien)

Usage :
  python scripts/migrate_v3.py
"""
import sys
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from init_db import init_database
from loguru import logger

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / "migrate_v3.log", rotation="1 MB", level="DEBUG")

# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_lot_from_context(context_path: str | None) -> str:
    """Déduit le lot (CFO/CFA/PV) depuis le context_path ou chapter.

    Règles :
      'FORT'  dans le path → CFO  (Courants Forts)
      'FAIBLE' ou 'SSI'   → CFA  (Courants Faibles / SSI)
      'PHOTO' ou 'SOLAI'  → PV   (Photovoltaïque)
      Défaut              → CFO
    """
    c = (context_path or "").upper()
    if "FAIBLE" in c or " SSI" in c or c.endswith("SSI"):
        return "CFA"
    if "PHOTO" in c or "SOLAI" in c:
        return "PV"
    return "CFO"


# ── DDL migrations ─────────────────────────────────────────────────────────────

DDL_PROJECTS = [
    (
        "ALTER TABLE projects ADD COLUMN coef_cfo REAL NOT NULL DEFAULT 1.0",
        "projects.coef_cfo",
    ),
    (
        "ALTER TABLE projects ADD COLUMN coef_cfa REAL NOT NULL DEFAULT 1.0",
        "projects.coef_cfa",
    ),
    (
        "ALTER TABLE projects ADD COLUMN coef_pv REAL NOT NULL DEFAULT 1.0",
        "projects.coef_pv",
    ),
    (
        "ALTER TABLE projects ADD COLUMN puissance_pv_kwp REAL NOT NULL DEFAULT 0.0",
        "projects.puissance_pv_kwp",
    ),
]

DDL_DEVIS_LINES = [
    (
        "ALTER TABLE devis_lines ADD COLUMN is_stat_valid INTEGER NOT NULL DEFAULT 1",
        "devis_lines.is_stat_valid",
    ),
    (
        "ALTER TABLE devis_lines ADD COLUMN lot TEXT",
        "devis_lines.lot",
    ),
]

DDL_MAPPING_SYNONYMS = """
CREATE TABLE IF NOT EXISTS mapping_synonyms (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    designation_entreprise TEXT NOT NULL,
    dpgf_article_id      INTEGER NOT NULL REFERENCES dpgf_articles(id),
    source               TEXT DEFAULT 'manual',  -- 'manual' | 'auto'
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(designation_entreprise, dpgf_article_id)
);
CREATE INDEX IF NOT EXISTS idx_synonyms_desig ON mapping_synonyms(designation_entreprise);
"""


def _apply_ddl(conn: sqlite3.Connection, migrations: list[tuple[str, str]], section: str) -> None:
    """Applique une liste d'ALTER TABLE ADD COLUMN, ignore les doublons."""
    print(f"\n  [DDL] {section} :")
    for sql, label in migrations:
        try:
            conn.execute(sql)
            conn.commit()
            print(f"    [OK]  {label}")
            logger.info(f"DDL OK : {label}")
        except sqlite3.OperationalError as e:
            if "duplicate column" in str(e).lower():
                print(f"    [--]  {label}  (existe déjà — ignoré)")
                logger.debug(f"DDL skip (exists) : {label}")
            else:
                print(f"    [ERR] {label} : {e}")
                logger.error(f"DDL erreur : {label} — {e}")
                conn.close()
                sys.exit(1)


def _migrate_coefs(conn: sqlite3.Connection) -> None:
    """Transfère coef_complexite → coef_cfo / coef_cfa / coef_pv pour les projets existants."""
    print("\n  [DML] Transfert coef_complexite -> coef_cfo / coef_cfa / coef_pv :")
    rows = conn.execute(
        "SELECT id, name, coef_complexite FROM projects WHERE coef_cfo = 1.0"
    ).fetchall()
    if not rows:
        print("    [--]  Aucun projet a migrer (tables vides ou deja migrees)")
        logger.info("Transfert coefs : aucun projet concerne")
        return
    for pid, name, coef in rows:
        conn.execute(
            "UPDATE projects SET coef_cfo=?, coef_cfa=?, coef_pv=? WHERE id=?",
            (coef, coef, coef, pid),
        )
        print(f"    [OK]  Projet '{name}' (id={pid}) : coef={coef} -> CFO/CFA/PV")
        logger.info(f"Projet id={pid} '{name}' : coef_complexite={coef} -> coef_cfo/cfa/pv")
    conn.commit()


def _migrate_lots(conn: sqlite3.Connection) -> None:
    """Déduit le lot de chaque ligne de devis_lines depuis context_path."""
    print("\n  [DML] Déduction du lot (CFO/CFA/PV) pour devis_lines :")
    rows = conn.execute(
        "SELECT id, context_path FROM devis_lines WHERE lot IS NULL"
    ).fetchall()
    if not rows:
        print("    [--]  Aucune ligne à migrer")
        logger.info("Migration lots : aucune ligne concernée")
        return
    updates = [((_detect_lot_from_context(ctx)), rid) for rid, ctx in rows]
    conn.executemany("UPDATE devis_lines SET lot = ? WHERE id = ?", updates)
    conn.commit()
    counts = {}
    for lot, _ in updates:
        counts[lot] = counts.get(lot, 0) + 1
    for lot, n in sorted(counts.items()):
        print(f"    [OK]  {lot} : {n} ligne(s)")
    logger.info(f"Migration lots : {len(updates)} lignes mises à jour — {counts}")


def _drop_coef_complexite(conn: sqlite3.Connection) -> None:
    """Tente de supprimer coef_complexite. Ignore si SQLite < 3.35 ou colonne absente."""
    print("\n  [DDL] Suppression de projects.coef_complexite (ancienne colonne) :")
    # Vérifier que la colonne existe encore
    cols = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    if "coef_complexite" not in cols:
        print("    [--]  Déjà supprimée — ignoré")
        logger.debug("coef_complexite : absente, rien à faire")
        return
    try:
        conn.execute("ALTER TABLE projects DROP COLUMN coef_complexite")
        conn.commit()
        print("    [OK]  coef_complexite supprimée")
        logger.info("projects.coef_complexite supprimée avec succès")
    except sqlite3.OperationalError as e:
        print(f"    [!!]  Impossible de supprimer : {e}")
        print("          La colonne est conservée (inoffensive, plus utilisée)")
        logger.warning(f"Impossible de supprimer coef_complexite : {e}")


def _create_mapping_synonyms(conn: sqlite3.Connection) -> None:
    """Crée la table mapping_synonyms si elle n'existe pas."""
    print("\n  [DDL] Table mapping_synonyms :")
    for stmt in DDL_MAPPING_SYNONYMS.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    print("    [OK]  mapping_synonyms créée (ou déjà présente)")
    logger.info("Table mapping_synonyms : OK")


def _rapport_final(conn: sqlite3.Connection) -> None:
    """Vérifie et affiche le résultat de la migration."""
    print("\n  [RAPPORT]")

    cols_projects = [r[1] for r in conn.execute("PRAGMA table_info(projects)").fetchall()]
    for col in ("coef_cfo", "coef_cfa", "coef_pv", "puissance_pv_kwp"):
        status = "OK" if col in cols_projects else "ABSENT !"
        print(f"    projects.{col:<25} {status}")

    cols_lines = [r[1] for r in conn.execute("PRAGMA table_info(devis_lines)").fetchall()]
    for col in ("is_stat_valid", "lot"):
        status = "OK" if col in cols_lines else "ABSENT !"
        print(f"    devis_lines.{col:<22} {status}")

    old_col = "coef_complexite" in cols_projects
    print(f"    projects.coef_complexite       {'présente (inoffensive)' if old_col else 'supprimée'}")

    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    ms_ok = "mapping_synonyms" in tables
    print(f"    table mapping_synonyms         {'OK' if ms_ok else 'ABSENT !'}")

    n_proj = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    n_lines = conn.execute("SELECT COUNT(*) FROM devis_lines").fetchone()[0]
    print(f"\n    Projets existants  : {n_proj}")
    print(f"    Lignes devis       : {n_lines}")


def run() -> None:
    conn = init_database()

    print()
    print("=" * 60)
    print("  MIGRATION v3 — Coefficients par Lot + Validité Statistique")
    print("=" * 60)

    _apply_ddl(conn, DDL_PROJECTS, "Nouvelles colonnes — projects")
    _apply_ddl(conn, DDL_DEVIS_LINES, "Nouvelles colonnes — devis_lines")
    _create_mapping_synonyms(conn)
    _migrate_coefs(conn)
    _migrate_lots(conn)
    _drop_coef_complexite(conn)
    _rapport_final(conn)

    conn.close()
    print()
    print("  Migration v3 terminée.")
    print("  Prochaine étape : re-importer le devis PSA avec import_devis.py")
    print("=" * 60)
    print()


if __name__ == "__main__":
    run()
