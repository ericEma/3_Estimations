"""
init_db.py - Initialisation de la base de données Estimation Elec
Usage : python init_db.py [--reset]
  --reset : supprime et recrée la BDD (ATTENTION : perte de données)
"""
import sqlite3
import sys
import os
from pathlib import Path
from loguru import logger
from scripts.mapping_knowledge import ensure_table as _ensure_knowledge_table

# ── Chemins ────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).parent
DB_PATH     = PROJECT_DIR / "estimation_elec.db"
SCHEMA_PATH = PROJECT_DIR / "schema.sql"
LOG_DIR     = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

# ── Logging ────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")
logger.add(
    LOG_DIR / "init_db.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}",
    rotation="1 MB",
    retention="30 days"
)


def check_sqlite_version(conn: sqlite3.Connection) -> bool:
    """Vérifie que pow() est disponible (SQLite >= 3.35)."""
    version = sqlite3.sqlite_version_info
    logger.info(f"SQLite version : {sqlite3.sqlite_version}")
    if version < (3, 35, 0):
        logger.warning(
            f"SQLite {sqlite3.sqlite_version} < 3.35 : pow() non disponible. "
            "La vue v_ratios pourrait ne pas fonctionner."
        )
        return False
    # Test effectif
    try:
        conn.execute("SELECT pow(1.03, 2)")
        logger.info("pow() SQLite : OK")
        return True
    except sqlite3.OperationalError as e:
        logger.warning(f"pow() non disponible : {e}. Tentative avec math Python...")
        return False


def register_python_pow(conn: sqlite3.Connection):
    """Enregistre une fonction pow() Python si SQLite ne la supporte pas."""
    import math
    conn.create_function("pow", 2, lambda x, y: math.pow(x, y))
    logger.info("Fonction pow() enregistrée via Python (fallback).")


def init_database(reset: bool = False) -> sqlite3.Connection:
    """Crée (ou recrée) la base de données à partir de schema.sql."""

    profile = os.environ.get("ESTIMATION_PROFILE")
    if profile:
        from db_profiles import get_db_path, normalize_profile

        db_file = get_db_path(normalize_profile(profile))
    else:
        db_file = DB_PATH

    # ── Reset ──────────────────────────────────────────────────
    if reset and db_file.exists():
        logger.warning(f"MODE RESET : suppression de {db_file.name}")
        db_file.unlink()

    db_exists = db_file.exists()

    # ── Connexion ──────────────────────────────────────────────
    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    # Foreign keys doivent être activées à CHAQUE connexion (non persistées)
    conn.execute("PRAGMA foreign_keys = ON")

    # ── Vérification pow() ────────────────────────────────────
    pow_native = check_sqlite_version(conn)
    if not pow_native:
        register_python_pow(conn)

    if db_exists and not reset:
        logger.info(f"Base existante chargée : {db_file.name}")
        _ensure_knowledge_table(conn)
        _verify_schema(conn)
        return conn

    # ── Création du schéma ────────────────────────────────────
    logger.info(f"Création de la base : {DB_PATH.name}")
    if not SCHEMA_PATH.exists():
        logger.error(f"schema.sql introuvable : {SCHEMA_PATH}")
        sys.exit(1)

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    try:
        conn.executescript(schema_sql)
        conn.commit()
        logger.success("Schéma appliqué avec succès.")
    except sqlite3.Error as e:
        logger.error(f"Erreur lors de l'application du schéma : {e}")
        conn.close()
        if DB_PATH.exists():
            DB_PATH.unlink()
        sys.exit(1)

    _verify_schema(conn)
    return conn


def _verify_schema(conn: sqlite3.Connection):
    """Vérifie la présence des tables et de la vue attendues."""
    expected_tables = {
        "config", "building_categories", "dpgf_articles", "projects", "devis_lines",
        "mapping_knowledge",
    }
    expected_views = {"v_ratios"}

    cur = conn.cursor()

    # Tables
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = {row[0] for row in cur.fetchall()}
    missing_tables = expected_tables - tables
    if missing_tables:
        logger.error(f"Tables manquantes : {', '.join(sorted(missing_tables))}")
        sys.exit(1)
    logger.info(f"Tables OK : {', '.join(sorted(tables))}")

    # Vues
    cur.execute("SELECT name FROM sqlite_master WHERE type='view' ORDER BY name")
    views = {row[0] for row in cur.fetchall()}
    missing_views = expected_views - views
    if missing_views:
        logger.error(f"Vues manquantes : {', '.join(sorted(missing_views))}")
        sys.exit(1)
    logger.info(f"Vues OK : {', '.join(sorted(views))}")

    # Config
    cur.execute("SELECT key, value FROM config ORDER BY key")
    config = {row["key"]: row["value"] for row in cur.fetchall()}
    logger.info(f"Config : {config}")

    # Building categories
    cur.execute("SELECT COUNT(*) AS n FROM building_categories")
    n = cur.fetchone()["n"]
    logger.info(f"Catégories de bâtiment : {n}")

    # Test de la vue v_ratios (doit retourner 0 ligne sans données, sans erreur)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM v_ratios")
        n_ratios = cur.fetchone()["n"]
        logger.info(f"Vue v_ratios : accessible ({n_ratios} ligne(s))")
    except sqlite3.OperationalError as e:
        logger.error(f"Erreur vue v_ratios : {e}")
        sys.exit(1)

    logger.success("Vérification du schéma : PASS")


if __name__ == "__main__":
    reset_flag = "--reset" in sys.argv
    if reset_flag:
        confirm = input("⚠️  RESET : toutes les données seront perdues. Confirmer ? (oui/non) : ")
        if confirm.strip().lower() != "oui":
            logger.info("Reset annulé.")
            sys.exit(0)

    conn = init_database(reset=reset_flag)
    conn.close()
    logger.success(f"Base prête : {DB_PATH}")
