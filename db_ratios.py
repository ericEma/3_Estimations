"""
Base SQLite dédiée — ratios par typologie de bâtiment (devis réels).

Indépendante des 3 BDD profils (bibliothèque / matching).
Alimentée uniquement par imports /import (+ bootstrap Excel optionnel).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
RATIOS_DB_FILE = "estimation_ratios.db"

DEFAULT_TEMPORAL_WEIGHTS = (
    (0, 17, 1.0),
    (18, 35, 0.5),
    (36, None, 0.1),
)

TAUX_ACTUALISATION_ANNUEL = 0.03


def get_ratios_db_path() -> Path:
    return PROJECT_DIR / RATIOS_DB_FILE


def connect() -> sqlite3.Connection:
    path = get_ratios_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    if own:
        conn = connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS ratio_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ratio_temporal_weights (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                age_months_min  INTEGER NOT NULL,
                age_months_max  INTEGER,
                weight          REAL NOT NULL,
                UNIQUE(age_months_min, age_months_max)
            );

            CREATE TABLE IF NOT EXISTS ratio_devis_sources (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,
                category_name       TEXT NOT NULL,
                devis_date          TEXT NOT NULL,
                surface_sdo         REAL NOT NULL CHECK (surface_sdo > 0),
                kva_cible           REAL,
                puissance_pv_kwc    REAL,
                total_ht            REAL,
                total_ht_cfo        REAL NOT NULL DEFAULT 0,
                total_ht_cfa        REAL NOT NULL DEFAULT 0,
                total_ht_pv         REAL NOT NULL DEFAULT 0,
                coef_cfo            REAL NOT NULL DEFAULT 1.0,
                coef_cfa            REAL NOT NULL DEFAULT 1.0,
                coef_pv             REAL NOT NULL DEFAULT 1.0,
                detail_level        TEXT NOT NULL DEFAULT 'full'
                    CHECK (detail_level IN ('full', 'partial', 'total_only')),
                imputed             INTEGER NOT NULL DEFAULT 0,
                source              TEXT NOT NULL DEFAULT 'devis_import'
                    CHECK (source IN ('devis_import', 'excel_archive', 'manual')),
                source_file         TEXT,
                price_profile       TEXT,
                project_id          INTEGER,
                import_batch_id     TEXT,
                is_active           INTEGER NOT NULL DEFAULT 1,
                notes               TEXT,
                created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_ratio_sources_category
                ON ratio_devis_sources(category_name);
            CREATE INDEX IF NOT EXISTS idx_ratio_sources_date
                ON ratio_devis_sources(devis_date);

            CREATE TABLE IF NOT EXISTS ratio_source_computed (
                source_id               INTEGER PRIMARY KEY
                    REFERENCES ratio_devis_sources(id) ON DELETE CASCADE,
                annee_reference         INTEGER NOT NULL,
                ratio_cfo_m2_actualise  REAL,
                ratio_cfa_m2_actualise  REAL,
                ratio_pv_kwc_actualise  REAL,
                ratio_total_m2_actualise REAL,
                computed_at             TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ratio_typology_shares (
                category_name   TEXT PRIMARY KEY,
                share_cfo       REAL NOT NULL DEFAULT 0,
                share_cfa       REAL NOT NULL DEFAULT 0,
                share_pv        REAL NOT NULL DEFAULT 0,
                nb_full_sources INTEGER NOT NULL DEFAULT 0,
                computed_at     TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS ratio_building_type_aggregates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                category_name   TEXT NOT NULL,
                lot             TEXT NOT NULL CHECK (lot IN ('CFO', 'CFA', 'PV')),
                unit            TEXT NOT NULL CHECK (unit IN ('EUR_M2', 'EUR_KWC')),
                ratio_actualise REAL NOT NULL,
                nb_sources      INTEGER NOT NULL DEFAULT 0,
                fiabilite       TEXT NOT NULL DEFAULT 'AUCUNE_REF'
                    CHECK (fiabilite IN ('OK', 'PRUDENCE', 'SOURCE_UNIQUE', 'AUCUNE_REF', 'IMPUTE')),
                annee_reference INTEGER NOT NULL,
                computed_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(category_name, lot, unit, annee_reference)
            );
            """
        )
        _seed_defaults(conn)
        conn.commit()
    finally:
        if own:
            conn.close()


def _seed_defaults(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT 1 FROM ratio_config WHERE key = 'annee_reference'"
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO ratio_config (key, value) VALUES ('annee_reference', ?)",
            (str(datetime.now().year),),
        )
    n = conn.execute("SELECT COUNT(*) FROM ratio_temporal_weights").fetchone()[0]
    if n == 0:
        for age_min, age_max, w in DEFAULT_TEMPORAL_WEIGHTS:
            conn.execute(
                """
                INSERT INTO ratio_temporal_weights (age_months_min, age_months_max, weight)
                VALUES (?, ?, ?)
                """,
                (age_min, age_max, w),
            )


def get_annee_reference(conn: sqlite3.Connection | None = None) -> int:
    own = conn is None
    if own:
        conn = connect()
    try:
        row = conn.execute(
            "SELECT value FROM ratio_config WHERE key = 'annee_reference'"
        ).fetchone()
        if row:
            return int(row["value"])
        return datetime.now().year
    finally:
        if own:
            conn.close()
