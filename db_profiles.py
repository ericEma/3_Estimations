"""
Profils métier → fichiers SQLite distincts (base de prix + devis + affaires).

  hopitaux   : Hôpital
  industriel : Industrie
  autres     : tous les autres types de bâtiment
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

PROFILES = ("hopitaux", "industriel", "autres")
DEFAULT_PROFILE = "autres"

PROFILE_LABELS = {
    "hopitaux": "Hôpitaux",
    "industriel": "Industriel",
    "autres": "Autres",
}

PROFILE_DB_FILES = {
    "hopitaux": "estimation_hopitaux.db",
    "industriel": "estimation_industriel.db",
    "autres": "estimation_autres.db",
}

LEGACY_DB_FILE = "estimation_elec.db"

HOPITAL_CATEGORY = "Hôpital"
INDUSTRIE_CATEGORY = "Industrie"


def legacy_db_path() -> Path:
    return PROJECT_DIR / LEGACY_DB_FILE


def get_db_path(profile: str | None = None) -> Path:
    p = normalize_profile(profile)
    dedicated = PROJECT_DIR / PROFILE_DB_FILES[p]
    if dedicated.is_file():
        return dedicated
    leg = legacy_db_path()
    if leg.is_file():
        return leg
    return dedicated


def normalize_profile(value) -> str:
    v = (str(value or "").strip().lower())
    if v in ("hopital", "hopitaux", "hôpital", "hôpitaux"):
        return "hopitaux"
    if v in ("industrie", "industriel"):
        return "industriel"
    if v in PROFILES:
        return v
    return DEFAULT_PROFILE


def normalize_profile_filter(value) -> str:
    """Filtre dashboard : 'tous' ou profil normalisé."""
    v = (str(value or "tous").strip().lower())
    if v in ("", "tous", "all", "*"):
        return "tous"
    return normalize_profile(v)


def profile_for_category_name(name: str | None) -> str:
    n = (name or "").strip()
    if n == HOPITAL_CATEGORY:
        return "hopitaux"
    if n == INDUSTRIE_CATEGORY:
        return "industriel"
    return DEFAULT_PROFILE


def connect(profile: str | None = None) -> sqlite3.Connection:
    path = get_db_path(profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _row_exists(conn: sqlite3.Connection, table: str, id_col: str, row_id: int) -> bool:
    try:
        r = conn.execute(
            f"SELECT 1 FROM {table} WHERE {id_col} = ? LIMIT 1", (row_id,)
        ).fetchone()
        return r is not None
    except sqlite3.OperationalError:
        return False


def _dedicated_files_exist() -> bool:
    return any((PROJECT_DIR / PROFILE_DB_FILES[p]).is_file() for p in PROFILES)


def _profiles_to_scan():
    if _dedicated_files_exist():
        return list(PROFILES)
    if legacy_db_path().is_file():
        return [None]
    return [DEFAULT_PROFILE]


def _infer_profile_from_row(conn: sqlite3.Connection, table: str, id_col: str, row_id: int) -> str:
    cols = {c[1] for c in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if "price_profile" in cols:
        row = conn.execute(
            f"SELECT category_id, price_profile FROM {table} WHERE {id_col} = ?",
            (row_id,),
        ).fetchone()
        if row and row["price_profile"]:
            return normalize_profile(row["price_profile"])
        if row and row["category_id"]:
            return resolve_profile_from_category_id(row["category_id"], conn=conn)
    else:
        row = conn.execute(
            f"SELECT category_id FROM {table} WHERE {id_col} = ?",
            (row_id,),
        ).fetchone()
        if row and row["category_id"]:
            return resolve_profile_from_category_id(row["category_id"], conn=conn)
    return DEFAULT_PROFILE


def _scan_profiles(
    table: str,
    id_col: str,
    row_id: int,
) -> str | None:
    for p in _profiles_to_scan():
        conn = connect(p)
        try:
            if _row_exists(conn, table, id_col, row_id):
                if p is None:
                    return _infer_profile_from_row(conn, table, id_col, row_id)
                return p
        finally:
            conn.close()
    return None


def find_affaire_profile(affaire_id: int, prefer: str | None = None) -> str | None:
    """Localise l'affaire ; ``prefer`` obligatoire si le même id existe dans plusieurs BDD."""
    if prefer:
        p = normalize_profile(prefer)
        conn = connect(p)
        try:
            if _row_exists(conn, "affaires", "id", affaire_id):
                return p
        finally:
            conn.close()
        return None

    hits: list[str | None] = []
    for p in _profiles_to_scan():
        conn = connect(p)
        try:
            if _row_exists(conn, "affaires", "id", affaire_id):
                hits.append(p)
        finally:
            conn.close()

    if not hits:
        return None
    if len(hits) == 1:
        return hits[0]

    for p in hits:
        if p is None:
            continue
        conn = connect(p)
        try:
            cols = {c[1] for c in conn.execute("PRAGMA table_info(affaires)").fetchall()}
            if "price_profile" in cols:
                row = conn.execute(
                    "SELECT price_profile FROM affaires WHERE id = ?", (affaire_id,)
                ).fetchone()
                if row and row[0]:
                    return normalize_profile(row[0])
        finally:
            conn.close()
    return hits[0]


def find_project_profile(project_id: int) -> str | None:
    return _scan_profiles("projects", "id", project_id)


def find_devis_line_profile(line_id: int) -> str | None:
    for p in _profiles_to_scan():
        conn = connect(p)
        try:
            r = conn.execute(
                "SELECT 1 FROM devis_lines WHERE id = ? LIMIT 1", (line_id,)
            ).fetchone()
            if r:
                if p is None:
                    row = conn.execute(
                        "SELECT project_id FROM devis_lines WHERE id = ?",
                        (line_id,),
                    ).fetchone()
                    if row:
                        return find_project_profile(int(row["project_id"]))
                    return DEFAULT_PROFILE
                return p
        finally:
            conn.close()
    return None


def find_dpgf_article_profile(article_id: int) -> str | None:
    return _scan_profiles("dpgf_articles", "id", article_id)


def resolve_profile_from_category_id(
    category_id,
    *,
    conn: sqlite3.Connection | None = None,
) -> str:
    if not category_id:
        return DEFAULT_PROFILE
    own = conn
    if own is None:
        own = connect(DEFAULT_PROFILE)
        close = True
    else:
        close = False
    try:
        row = own.execute(
            "SELECT name FROM building_categories WHERE id = ?",
            (int(category_id),),
        ).fetchone()
        return profile_for_category_name(row["name"] if row else None)
    except (TypeError, ValueError, sqlite3.Error):
        return DEFAULT_PROFILE
    finally:
        if close:
            own.close()


def profiles_to_migrate() -> list[str | None]:
    """Chaque BDD profil dédiée, ou legacy une seule fois en transition."""
    if _dedicated_files_exist():
        return list(PROFILES)
    if legacy_db_path().is_file():
        return [None]
    return [DEFAULT_PROFILE]


def any_profile_db_exists() -> bool:
    if legacy_db_path().is_file():
        return True
    return any((PROJECT_DIR / PROFILE_DB_FILES[p]).is_file() for p in PROFILES)
