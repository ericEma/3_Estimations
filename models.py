"""
models.py — Couche d'accès aux données (sqlite3)
Sprint 4 : Application Estimation Élec

Note : SQLite3 natif (pas SQLAlchemy) pour rester cohérent avec les scripts
existants et éviter une migration de schéma. Les fonctions retournent des dicts.
"""

import sqlite3
import os
from datetime import date, datetime

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(PROJECT_DIR, "estimation_elec.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _verify_foreign_keys_enabled(conn: sqlite3.Connection) -> None:
    """SQLite n'active pas les FK par défaut : exiger ON sur la connexion courante."""
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute("PRAGMA foreign_keys").fetchone()
    if row is None or int(row[0]) != 1:
        raise RuntimeError(
            "PRAGMA foreign_keys != ON — suppression de section refusée (intégrité)"
        )


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return r is not None


def _affaire_lines_hors_catalog_migrated(conn: sqlite3.Connection) -> bool:
    """True si ``dpgf_article_id`` accepte NULL et colonnes hors catalogue présentes."""
    cols = conn.execute("PRAGMA table_info(affaire_lines)").fetchall()
    names = {c[1] for c in cols}
    if "line_designation" not in names:
        return False
    for c in cols:
        if c[1] == "dpgf_article_id" and int(c[3] or 0) == 0:
            return True
    return False


def _migrate_affaire_lines_hors_catalogue(conn: sqlite3.Connection) -> None:
    """Rend ``dpgf_article_id`` nullable + ``line_designation`` / ``line_lot`` (lignes sur-mesure)."""
    if _affaire_lines_hors_catalog_migrated(conn):
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        DROP TABLE IF EXISTS affaire_lines__hc_mig;
        CREATE TABLE affaire_lines__hc_mig (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            affaire_id          INTEGER NOT NULL REFERENCES affaires(id) ON DELETE CASCADE,
            dpgf_article_id     INTEGER REFERENCES dpgf_articles(id),
            quantity            REAL,
            quantity_source     TEXT DEFAULT 'ratio'
                CHECK (quantity_source IN ('ratio', 'manual')),
            unit_price_ht       REAL,
            unit_price_source   TEXT DEFAULT 'ratio'
                CHECK (unit_price_source IN ('ratio', 'manual')),
            total_ht            REAL,
            is_included         INTEGER NOT NULL DEFAULT 1,
            ratio_ref           REAL,
            deviation_pct       REAL,
            notes               TEXT,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            unit_override       TEXT,
            unit_source         TEXT DEFAULT 'ratio',
            line_designation    TEXT,
            line_lot            TEXT
        );
        INSERT INTO affaire_lines__hc_mig (
            id, affaire_id, dpgf_article_id, quantity, quantity_source,
            unit_price_ht, unit_price_source, total_ht, is_included,
            ratio_ref, deviation_pct, notes, created_at, unit_override, unit_source,
            line_designation, line_lot
        )
        SELECT
            id, affaire_id, dpgf_article_id, quantity, quantity_source,
            unit_price_ht, unit_price_source, total_ht, is_included,
            ratio_ref, deviation_pct, notes, created_at, unit_override, unit_source,
            NULL, NULL
        FROM affaire_lines;
        DROP TABLE affaire_lines;
        ALTER TABLE affaire_lines__hc_mig RENAME TO affaire_lines;
        CREATE INDEX IF NOT EXISTS idx_affaire_lines_affaire ON affaire_lines(affaire_id);
        CREATE INDEX IF NOT EXISTS idx_affaire_lines_article ON affaire_lines(dpgf_article_id);
        """
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def derive_lot_from_chapter(chapter: str | None) -> str:
    """CFO / CFA / PV depuis le libellé de chapitre (insensible à la casse)."""
    d = (chapter or "").lower()
    if "faible" in d or "cfa" in d:
        return "CFA"
    if "photovolta" in d:
        return "PV"
    return "CFO"


def delete_bibliotheque_section(
    conn: sqlite3.Connection, chapter: str, section: str
) -> dict:
    """Supprime proprement une section (sous-chapitre) dans la bibliothèque DPGF.

    Il n'existe pas de table « section » : clé métier (chapter, section) sur
    ``dpgf_articles``. Ordre respectant les FK (pas de CASCADE SQLite sur toutes
    les tables) :

    1. ``affaire_lines`` + ``ratio_overrides`` : nettoyage pour tous les articles
       de la section (estimations + overrides).
    2. ``devis_lines`` (NULL) + ``mapping_*`` : uniquement pour les articles
       **custom** supprimés physiquement.
    3. DELETE ``dpgf_articles`` custom ; UPDATE ``is_hidden`` pour PSA.
    4. DELETE ``bibliotheque_section_ratios`` pour la clé (chapter, section).

    Returns:
        Statistiques pour logs (counts).
    """
    chap = (chapter or "").strip()
    sec = (section or "").strip()
    if not chap or not sec:
        raise ValueError("section_delete : chapter et section non vides requis")

    _verify_foreign_keys_enabled(conn)

    rows = conn.execute(
        """
        SELECT id, COALESCE(is_custom, 0) AS is_custom
        FROM dpgf_articles
        WHERE chapter = ? AND section = ? AND row_type = 'article'
        """,
        (chap, sec),
    ).fetchall()

    ids_all = [int(r["id"]) for r in rows]
    ids_custom = [int(r["id"]) for r in rows if int(r["is_custom"]) == 1]

    ph_all = ",".join("?" * len(ids_all)) if ids_all else ""
    ph_cust = ",".join("?" * len(ids_custom)) if ids_custom else ""

    if ids_all:
        conn.execute(
            f"DELETE FROM affaire_lines WHERE dpgf_article_id IN ({ph_all})",
            ids_all,
        )
        conn.execute(
            f"DELETE FROM ratio_overrides WHERE dpgf_article_id IN ({ph_all})",
            ids_all,
        )

    if ids_custom:
        if _table_exists(conn, "devis_lines"):
            try:
                conn.execute(
                    f"UPDATE devis_lines SET dpgf_article_id = NULL "
                    f"WHERE dpgf_article_id IN ({ph_cust})",
                    ids_custom,
                )
            except sqlite3.OperationalError:
                pass
        for tbl in ("mapping_synonyms", "mapping_knowledge"):
            if _table_exists(conn, tbl):
                try:
                    conn.execute(
                        f"DELETE FROM {tbl} WHERE dpgf_article_id IN ({ph_cust})",
                        ids_custom,
                    )
                except sqlite3.OperationalError:
                    pass

        conn.execute(
            f"""
            DELETE FROM dpgf_articles
            WHERE chapter = ? AND section = ? AND row_type = 'article'
              AND COALESCE(is_custom, 0) = 1
            """,
            (chap, sec),
        )

    conn.execute(
        """
        UPDATE dpgf_articles SET is_hidden = 1
        WHERE chapter = ? AND section = ? AND row_type = 'article'
          AND COALESCE(is_custom, 0) = 0
        """,
        (chap, sec),
    )

    conn.execute(
        "DELETE FROM bibliotheque_section_ratios WHERE chapter = ? AND section = ?",
        (chap, sec),
    )

    return {
        "chapter": chap,
        "section": sec,
        "articles_in_section": len(ids_all),
        "custom_deleted": len(ids_custom),
    }


def ensure_app_tables():
    """Crée les tables spécifiques à l'application web si absentes."""
    conn = get_db()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS affaires (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                name                TEXT NOT NULL,
                client              TEXT,
                adresse             TEXT,
                date_creation       DATE DEFAULT CURRENT_DATE,
                surface_sdo         REAL NOT NULL DEFAULT 1000.0,
                category_id         INTEGER REFERENCES building_categories(id),
                coef_complexity_cfo REAL NOT NULL DEFAULT 1.0,
                coef_complexity_cfa REAL NOT NULL DEFAULT 1.0,
                coef_complexity_pv  REAL NOT NULL DEFAULT 1.0,
                coef_risque         REAL NOT NULL DEFAULT 0.0,
                taux_marge          REAL NOT NULL DEFAULT 0.0,
                statut              TEXT NOT NULL DEFAULT 'brouillon'
                    CHECK (statut IN ('brouillon', 'en_cours', 'finalise', 'archive')),
                notes               TEXT,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS affaire_lines (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                affaire_id          INTEGER NOT NULL REFERENCES affaires(id) ON DELETE CASCADE,
                dpgf_article_id     INTEGER REFERENCES dpgf_articles(id),
                quantity            REAL,
                quantity_source     TEXT DEFAULT 'ratio'
                    CHECK (quantity_source IN ('ratio', 'manual')),
                unit_price_ht       REAL,
                unit_price_source   TEXT DEFAULT 'ratio'
                    CHECK (unit_price_source IN ('ratio', 'manual')),
                total_ht            REAL,
                is_included         INTEGER NOT NULL DEFAULT 1,
                ratio_ref           REAL,
                deviation_pct       REAL,
                notes               TEXT,
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
                unit_override       TEXT,
                unit_source         TEXT DEFAULT 'ratio',
                line_designation    TEXT,
                line_lot            TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_affaire_lines_affaire ON affaire_lines(affaire_id);
            CREATE INDEX IF NOT EXISTS idx_affaire_lines_article ON affaire_lines(dpgf_article_id);

            CREATE TABLE IF NOT EXISTS ratio_overrides (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                dpgf_article_id INTEGER NOT NULL REFERENCES dpgf_articles(id),
                pu_override     REAL NOT NULL,
                raison          TEXT,
                created_by      TEXT DEFAULT 'Eric',
                created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(dpgf_article_id)
            );

            -- Sprint 9.2 : ratios €/m² SDO par section (bibliothèque)
            CREATE TABLE IF NOT EXISTS bibliotheque_section_ratios (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                chapter    TEXT NOT NULL,
                section    TEXT NOT NULL,
                ratio_m2   REAL NOT NULL,
                ratio_unit TEXT NOT NULL DEFAULT 'm2',
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chapter, section)
            );

            -- Sprint 7 : état des checkbox chapitre/section + mode Macro (ratio €/m²)
            CREATE TABLE IF NOT EXISTS affaire_chapter_settings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                affaire_id    INTEGER NOT NULL REFERENCES affaires(id) ON DELETE CASCADE,
                chapter_key   TEXT NOT NULL,
                is_included   INTEGER NOT NULL DEFAULT 1,
                use_macro     INTEGER NOT NULL DEFAULT 0,
                qty           REAL NOT NULL DEFAULT 1.0,
                ratio_m2_override REAL,
                updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(affaire_id, chapter_key)
            );
            CREATE INDEX IF NOT EXISTS idx_affaire_chapsettings_affaire
                ON affaire_chapter_settings(affaire_id);
        """)
        conn.commit()

        # ── Migrations Sprint 5 / Sprint 6 ───────────────────────────────────
        _migrations = [
            "ALTER TABLE affaires ADD COLUMN kva_cible        REAL DEFAULT 800.0",
            "ALTER TABLE affaires ADD COLUMN phase_etude       TEXT DEFAULT 'APD'",
            "ALTER TABLE affaires ADD COLUMN taux_incertitude  REAL DEFAULT 3.0",
            # Sprint 6 — 3ᵉ taxe indépendante liée à la phase (paliers Egis)
            "ALTER TABLE affaires ADD COLUMN taux_phase        REAL DEFAULT 3.0",
            # Sprint 7 — unité éditable par affaire (override du référentiel)
            "ALTER TABLE affaire_lines ADD COLUMN unit_override  TEXT",
            "ALTER TABLE affaire_lines ADD COLUMN unit_source    TEXT DEFAULT 'ratio'",
            # Sprint 8 — total effectif (ratio fallback inclus) pour le dashboard
            "ALTER TABLE affaires ADD COLUMN total_estime_ht REAL",
            # Sprint 9 — quantité de référence bibliothèque (hors affaire)
            "ALTER TABLE dpgf_articles ADD COLUMN qty_ref REAL DEFAULT 0",
            # Sprint 9.2 — masquage articles maître (non supprimé)
            "ALTER TABLE dpgf_articles ADD COLUMN is_hidden INTEGER DEFAULT 0",
            # Sprint 9.3 — unité du ratio de section (m2 ou kwc pour PV)
            "ALTER TABLE bibliotheque_section_ratios ADD COLUMN ratio_unit TEXT DEFAULT 'm2'",
        ]
        for sql in _migrations:
            try:
                conn.execute(sql)
                conn.commit()
            except sqlite3.OperationalError:
                pass  # colonne déjà présente

        # ── Mise à jour de la liste building_categories (Sprint 8) ───────────
        _NEW_CATEGORIES = [
            'Aéroport', 'Bureaux', 'Château', 'Groupe scolaire', 'Collège',
            'EHPAD', 'Gymnase', 'Hôpital', 'Hôtel', 'Industrie',
            'Laboratoire', 'Logements', 'Lycée', 'Parking', 'Stade',
        ]
        existing_names = {r[0] for r in conn.execute(
            "SELECT name FROM building_categories"
        ).fetchall()}
        if existing_names != set(_NEW_CATEGORIES):
            # Nullifie les FK avant de reconstruire la liste
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.execute("DELETE FROM building_categories")
            conn.executemany(
                "INSERT INTO building_categories (name) VALUES (?)",
                [(n,) for n in _NEW_CATEGORIES]
            )
            conn.execute("UPDATE affaires SET category_id = NULL "
                         "WHERE category_id NOT IN "
                         "(SELECT id FROM building_categories)")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

        # Sprint 9.4 — lignes hors catalogue (dpgf_article_id NULL + métadonnées)
        _migrate_affaire_lines_hors_catalogue(conn)
    finally:
        conn.close()


# ─── AFFAIRES ─────────────────────────────────────────────────────────────────

def save_total_estime(affaire_id: int, total: float):
    """Sauvegarde le total HT effectif (ratio fallback inclus) pour affichage dashboard."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE affaires SET total_estime_ht = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (total, affaire_id)
        )
        conn.commit()
    finally:
        conn.close()


def get_affaires() -> list:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT a.*,
                   bc.name as category_name,
                   COALESCE(
                     a.total_estime_ht,
                     (SELECT SUM(al.total_ht) FROM affaire_lines al
                      WHERE al.affaire_id = a.id AND al.is_included = 1),
                     0
                   ) as total_ht
            FROM affaires a
            LEFT JOIN building_categories bc ON a.category_id = bc.id
            ORDER BY a.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_affaire(affaire_id: int) -> dict | None:
    conn = get_db()
    try:
        r = conn.execute("""
            SELECT a.*, bc.name as category_name
            FROM affaires a
            LEFT JOIN building_categories bc ON a.category_id = bc.id
            WHERE a.id = ?
        """, (affaire_id,)).fetchone()
        return dict(r) if r else None
    finally:
        conn.close()


def create_affaire(data: dict) -> int:
    conn = get_db()
    try:
        cur = conn.execute("""
            INSERT INTO affaires
              (name, client, adresse, surface_sdo, category_id,
               coef_complexity_cfo, coef_complexity_cfa, coef_complexity_pv,
               coef_risque, taux_marge,
               kva_cible, phase_etude, taux_incertitude, taux_phase,
               notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('name', 'Nouvelle Affaire'),
            data.get('client'),
            data.get('adresse'),
            float(data.get('surface_sdo', 1000)),
            data.get('category_id'),
            float(data.get('coef_complexity_cfo', 1.0)),
            float(data.get('coef_complexity_cfa', 1.0)),
            float(data.get('coef_complexity_pv',  1.0)),
            float(data.get('coef_risque', 1.0)),
            0.0,  # taux_marge conservé pour compat
            float(data.get('kva_cible', 800.0)),
            data.get('phase_etude', 'APD'),
            float(data.get('taux_incertitude', 3.0)),
            float(data.get('taux_phase',        3.0)),
            data.get('notes'),
        ))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def update_affaire(affaire_id: int, data: dict):
    conn = get_db()
    try:
        conn.execute("""
            UPDATE affaires SET
                name                = ?,
                client              = ?,
                adresse             = ?,
                surface_sdo         = ?,
                category_id         = ?,
                coef_complexity_cfo = ?,
                coef_complexity_cfa = ?,
                coef_complexity_pv  = ?,
                coef_risque         = ?,
                kva_cible           = ?,
                phase_etude         = ?,
                taux_incertitude    = ?,
                taux_phase          = ?,
                notes               = ?,
                statut              = ?,
                updated_at          = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            data.get('name'),
            data.get('client'),
            data.get('adresse'),
            float(data.get('surface_sdo', 1000)),
            data.get('category_id'),
            float(data.get('coef_complexity_cfo', 1.0)),
            float(data.get('coef_complexity_cfa', 1.0)),
            float(data.get('coef_complexity_pv',  1.0)),
            float(data.get('coef_risque', 1.0)),
            float(data.get('kva_cible', 800.0)),
            data.get('phase_etude', 'APD'),
            float(data.get('taux_incertitude', 3.0)),
            float(data.get('taux_phase',        3.0)),
            data.get('notes'),
            data.get('statut', 'brouillon'),
            affaire_id,
        ))
        conn.commit()
    finally:
        conn.close()


def update_affaire_params(affaire_id: int, data: dict):
    """Auto-save temps réel des paramètres de cadrage.

    Met à jour uniquement les colonnes présentes dans `data` : SDO, kVA, phase,
    taux_phase, taux_incertitude, coef_risque, coef_complexity_{cfo,cfa,pv}.
    """
    ALLOWED_FLOAT = {
        'surface_sdo', 'kva_cible',
        'taux_phase', 'taux_incertitude', 'coef_risque',
        'coef_complexity_cfo', 'coef_complexity_cfa', 'coef_complexity_pv',
    }
    ALLOWED_STR   = {'phase_etude', 'category_id'}
    ALLOWED       = ALLOWED_FLOAT | ALLOWED_STR
    fields = [k for k in data.keys() if k in ALLOWED]
    if not fields:
        return

    set_clause = ', '.join(f"{f} = ?" for f in fields) + ", updated_at = CURRENT_TIMESTAMP"
    values = []
    for f in fields:
        v = data[f]
        if f in ALLOWED_STR:
            values.append(v if v not in ('', None) else None)
        else:
            values.append(float(v))
    values.append(affaire_id)

    conn = get_db()
    try:
        conn.execute(f"UPDATE affaires SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def delete_affaire(affaire_id: int):
    conn = get_db()
    try:
        conn.execute("DELETE FROM affaires WHERE id = ?", (affaire_id,))
        conn.commit()
    finally:
        conn.close()


# ─── LIGNES D'AFFAIRE ─────────────────────────────────────────────────────────

def get_affaire_lines(affaire_id: int) -> dict:
    """Retourne dict[dpgf_article_id] = line_data."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT al.*, da.designation, da.unit, da.ratio_type, da.chapter, da.section
            FROM affaire_lines al
            JOIN dpgf_articles da ON al.dpgf_article_id = da.id
            WHERE al.affaire_id = ?
        """, (affaire_id,)).fetchall()
        return {r['dpgf_article_id']: dict(r) for r in rows}
    finally:
        conn.close()


def save_affaire_lines(affaire_id: int, lines: list):
    """
    Sauvegarde les lignes du calculateur.
    lines = [{dpgf_article_id, quantity, quantity_source, unit_price_ht,
               unit_price_source, total_ht, is_included, ratio_ref,
               unit_override, unit_source}, ...]

    Les lignes hors catalogue (``dpgf_article_id`` NULL) ne sont pas effacées.
    """
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM affaire_lines WHERE affaire_id = ? AND dpgf_article_id IS NOT NULL",
            (affaire_id,),
        )
        for line in lines:
            qty       = line.get('quantity') or 0
            pu        = line.get('unit_price_ht') or 0
            total     = qty * pu
            ratio_ref = line.get('ratio_ref') or pu
            dev       = ((pu - ratio_ref) / ratio_ref * 100) if ratio_ref else 0

            conn.execute("""
                INSERT INTO affaire_lines
                  (affaire_id, dpgf_article_id, quantity, quantity_source,
                   unit_price_ht, unit_price_source, total_ht,
                   is_included, ratio_ref, deviation_pct, notes,
                   unit_override, unit_source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                affaire_id,
                line['dpgf_article_id'],
                qty,
                line.get('quantity_source', 'ratio'),
                pu,
                line.get('unit_price_source', 'ratio'),
                total,
                1 if line.get('is_included', True) else 0,
                ratio_ref,
                round(dev, 1),
                line.get('notes'),
                line.get('unit_override'),
                line.get('unit_source', 'ratio'),
            ))
        conn.execute(
            "UPDATE affaires SET updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (affaire_id,)
        )
        conn.commit()
    finally:
        conn.close()


def _ref_pu_for_article_conn(conn: sqlite3.Connection, dpgf_id: int) -> float:
    r = conn.execute(
        """
        SELECT COALESCE(ro.pu_override, da.pu_ht_ref, 0)
        FROM dpgf_articles da
        LEFT JOIN ratio_overrides ro ON ro.dpgf_article_id = da.id
        WHERE da.id = ?
        """,
        (dpgf_id,),
    ).fetchone()
    return float(r[0] or 0) if r else 0.0


def _default_estimation_quantity(row: dict, sdo: float, kva: float) -> float:
    """Qté par défaut : SDO pour m² / surfacique ; kWc cible pour unités kWc (lot PV).

    Communications internes — En charge du lot électricité.
    """
    chap_l = (row.get("chapter") or "").lower()
    unit_raw = row.get("unit") or ""
    u = unit_raw.lower().replace("²", "2").replace(" ", "")
    rt = row.get("ratio_type") or ""
    if "kwc" in u:
        return _round_money2(float(kva or 0))
    if u in ("m2", "m²") or rt == "SURFACIQUE":
        return _round_money2(float(sdo or 0))
    return 0.0


def get_estimation_catalog_rows(affaire_id: int) -> list:
    """Snapshot catalogue : chaque article DPGF + ligne d'affaire éventuelle.

    PU référence : COALESCE(override, pu_ht_ref) puis repli ``compute_ratios`` si nul.
    Sans ligne ``affaire_lines``, qté par défaut = SDO (surfacique / m²) ou kWc (PV).

    Communications internes — En charge du lot électricité.
    """
    affaire = get_affaire(affaire_id)
    if not affaire:
        return []
    sdo = float(affaire.get("surface_sdo") or 0)
    kva = float(affaire.get("kva_cible") or 0)
    ccfo = float(affaire.get("coef_complexity_cfo") or 1.0)
    ccfa = float(affaire.get("coef_complexity_cfa") or 1.0)
    cpv = float(affaire.get("coef_complexity_pv") or 1.0)

    try:
        from scripts.engine_ratios import compute_ratios

        ratios_map = compute_ratios(sdo, ccfo, ccfa, cpv)
    except Exception:
        ratios_map = {}

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT da.id AS dpgf_id, da.chapter, da.section, da.designation, da.unit,
                   da.ratio_type, da.row_order,
                   COALESCE(ro.pu_override, da.pu_ht_ref) AS ref_pu_ht,
                   al.id AS line_id, al.quantity, al.unit_price_ht, al.total_ht
            FROM dpgf_articles da
            LEFT JOIN ratio_overrides ro ON ro.dpgf_article_id = da.id
            LEFT JOIN affaire_lines al
                ON al.dpgf_article_id = da.id AND al.affaire_id = ?
            WHERE da.row_type = 'article'
              AND (da.is_hidden IS NULL OR da.is_hidden = 0)
            ORDER BY da.row_order
            """,
            (affaire_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            pid = int(d["dpgf_id"])
            ref = d.get("ref_pu_ht")
            ref_f = float(ref) if ref is not None else 0.0
            if ref_f <= 0 and pid in ratios_map:
                ent = ratios_map[pid]
                up = float(ent.get("unit_price") or 0)
                ref_f = up if up > 0 else float(ent.get("avg_pu_actualise") or 0)
            d["ref_pu_ht"] = _round_money2(ref_f)

            lid = d.get("line_id")
            q = d.get("quantity")
            if lid is None:
                d["quantity"] = _default_estimation_quantity(d, sdo, kva)
            elif q is None:
                d["quantity"] = 0.0
            else:
                d["quantity"] = _round_money2(float(q))

            d["lot"] = derive_lot_from_chapter(d.get("chapter"))
            out.append(d)
        return out
    finally:
        conn.close()


def get_estimation_custom_rows(affaire_id: int) -> list:
    """Lignes sur-mesure (hors catalogue) pour une affaire."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT id AS line_id, line_designation, unit_override, line_lot,
                   quantity, unit_price_ht, total_ht
            FROM affaire_lines
            WHERE affaire_id = ? AND dpgf_article_id IS NULL
            ORDER BY id
            """,
            (affaire_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# Chapitres catalogue estimation (casse identique BDD — En charge du lot électricité)
ESTIMATION_CHAPTER_DESIGNATIONS = (
    "Courants Forts",
    "Courants faibles",
    "Photovoltaïque",
)


def get_estimation_chapter_state(affaire_id: int) -> list:
    """Inclusion / macro / qty tête pour les 3 chapitres DPGF (clé ``chap:…``).

    Réutilise ``affaire_chapter_settings`` sans écraser ``use_macro`` / ``qty`` à l'enregistrement estimation.
    """
    settings = get_chapter_settings(affaire_id)
    out: list[dict] = []
    for name in ESTIMATION_CHAPTER_DESIGNATIONS:
        key = f"chap:{name}"
        row = settings.get(key, {})
        inc = row.get("is_included")
        if inc is None:
            is_included = True
        else:
            is_included = bool(inc)
        out.append(
            {
                "chapter": name,
                "chapter_key": key,
                "is_included": is_included,
                "use_macro": bool(row.get("use_macro", 0)),
                "qty": float(row.get("qty", 1.0) or 1.0),
            }
        )
    return out


def get_estimation_section_state(affaire_id: int) -> list:
    """Lignes ``sect:chapitre|section`` depuis ``affaire_chapter_settings`` (sous-chapitres catalogue)."""
    settings = get_chapter_settings(affaire_id)
    out: list[dict] = []
    for key, row in settings.items():
        if not isinstance(key, str) or not key.startswith("sect:"):
            continue
        rest = key[5:]
        if "|" not in rest:
            continue
        chap, sec = rest.split("|", 1)
        inc = row.get("is_included")
        is_included = True if inc is None else bool(inc)
        out.append(
            {
                "chapter": chap,
                "section": sec,
                "chapter_key": key,
                "is_included": is_included,
                "use_macro": bool(row.get("use_macro", 0)),
                "qty": float(row.get("qty", 1.0) or 1.0),
            }
        )
    return out


def batch_sync_estimation_m2_quantities(affaire_id: int, surface_sdo: float) -> int:
    """Mise à jour groupée des quantités catalogue en m² = SDO projet (unité m² uniquement).

    ``quantity_source`` = ``ratio`` (hors énumération manuelle). Communications internes — En charge du lot électricité.
    """
    qty = _round_money2(float(surface_sdo or 0))
    conn = get_db()
    try:
        cur = conn.execute(
            """
            UPDATE affaire_lines
            SET quantity = ?, quantity_source = 'ratio'
            WHERE affaire_id = ?
              AND dpgf_article_id IS NOT NULL
              AND dpgf_article_id IN (
                SELECT id FROM dpgf_articles
                WHERE row_type = 'article'
                  AND (is_hidden IS NULL OR is_hidden = 0)
                  AND LOWER(REPLACE(REPLACE(TRIM(COALESCE(unit, '')), '²', '2'), ' ', '')) = 'm2'
              )
            """,
            (qty, affaire_id),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def _round_money2(value) -> float:
    """Arrondi monétaire strict à 2 décimales (totaux estimation)."""
    return round(float(value or 0), 2)


def _estimation_totals_rounded(totals: dict) -> dict:
    return {
        "CFO": _round_money2(totals.get("CFO")),
        "CFA": _round_money2(totals.get("CFA")),
        "PV": _round_money2(totals.get("PV")),
        "ALL": _round_money2(totals.get("ALL")),
    }


def _compute_estimation_kpis_conn(conn: sqlite3.Connection, affaire_id: int) -> dict:
    """Totaux HT par lot sur la connexion courante (données non encore commitées visibles)."""
    totals = {"CFO": 0.0, "CFA": 0.0, "PV": 0.0}
    chap_inc: dict[str, bool] = {}
    for r in conn.execute(
        """
        SELECT chapter_key, is_included
        FROM affaire_chapter_settings
        WHERE affaire_id = ? AND chapter_key LIKE 'chap:%'
        """,
        (affaire_id,),
    ).fetchall():
        chap_inc[r["chapter_key"]] = bool(r["is_included"])

    sect_inc: dict[str, bool] = {}
    for r in conn.execute(
        """
        SELECT chapter_key, is_included
        FROM affaire_chapter_settings
        WHERE affaire_id = ? AND chapter_key LIKE 'sect:%'
        """,
        (affaire_id,),
    ).fetchall():
        sect_inc[r["chapter_key"]] = bool(r["is_included"])

    def _chapter_included(chapter: str | None) -> bool:
        if not chapter:
            return True
        k = f"chap:{chapter}"
        if k not in chap_inc:
            return True
        return chap_inc[k]

    def _section_included(chapter: str | None, section: str | None) -> bool:
        if not chapter or not section:
            return True
        k = f"sect:{chapter}|{section}"
        if k not in sect_inc:
            return True
        return sect_inc[k]

    rows = conn.execute(
        """
        SELECT da.chapter, da.section,
               COALESCE(ro.pu_override, da.pu_ht_ref, 0) AS ref_pu_ht,
               al.quantity, al.unit_price_ht
        FROM dpgf_articles da
        LEFT JOIN ratio_overrides ro ON ro.dpgf_article_id = da.id
        LEFT JOIN affaire_lines al
            ON al.dpgf_article_id = da.id AND al.affaire_id = ?
        WHERE da.row_type = 'article'
          AND (da.is_hidden IS NULL OR da.is_hidden = 0)
        """,
        (affaire_id,),
    ).fetchall()
    for r in rows:
        if not _chapter_included(r["chapter"]):
            continue
        if not _section_included(r["chapter"], r["section"]):
            continue
        lot = derive_lot_from_chapter(r["chapter"] if r["chapter"] is not None else "")
        qty = float(r["quantity"] or 0)
        ref = float(r["ref_pu_ht"] or 0)
        pu = r["unit_price_ht"]
        pu_eff = float(pu) if pu is not None else ref
        line_tot = _round_money2(qty * pu_eff)
        totals[lot] = _round_money2(totals[lot] + line_tot)
    customs = conn.execute(
        """
        SELECT line_lot, quantity, unit_price_ht
        FROM affaire_lines
        WHERE affaire_id = ? AND dpgf_article_id IS NULL
        """,
        (affaire_id,),
    ).fetchall()
    for r in customs:
        lot = (r["line_lot"] or "CFO").upper()
        if lot not in totals:
            lot = "CFO"
        qty = float(r["quantity"] or 0)
        pu = float(r["unit_price_ht"] or 0)
        line_tot = _round_money2(qty * pu)
        totals[lot] = _round_money2(totals[lot] + line_tot)
    totals["ALL"] = _round_money2(totals["CFO"] + totals["CFA"] + totals["PV"])
    return _estimation_totals_rounded(totals)


def compute_estimation_kpis(affaire_id: int) -> dict:
    """Totaux HT par lot (CFO / CFA / PV) et global — même logique d'affichage que la page."""
    conn = get_db()
    try:
        return _compute_estimation_kpis_conn(conn, affaire_id)
    finally:
        conn.close()


def save_estimation_changes(affaire_id: int, changes: list) -> dict:
    """Persiste une ou plusieurs modifications depuis la page Estimation (debounce JS).

    Chaque élément peut contenir :
      • Catalogue : ``dpgf_article_id``, ``quantity``, ``unit_price_ht`` (null = PU référentiel),
        optionnel ``line_id`` ignoré si upsert par ``dpgf_article_id``.
      • Nouvelle ligne custom : ``is_new_custom``, ``temp_line_id``, ``line_designation``,
        ``unit_override``, ``line_lot`` (CFO/CFA/PV), ``quantity``, ``unit_price_ht``.
      • Mise à jour custom : ``line_id``, ``dpgf_article_id`` null/absent, mêmes champs.
      • Suppression custom : ``delete_custom``: true, ``line_id``.
    """
    conn = get_db()
    new_ids = []
    try:
        changes = changes if isinstance(changes, list) else []
        if not changes:
            kpis = _compute_estimation_kpis_conn(conn, affaire_id)
            return {"status": "ok", "saved": 0, "totals": kpis, "new_ids": []}

        for ch in changes:
            if not isinstance(ch, dict):
                continue
            if ch.get("delete_custom") and ch.get("line_id"):
                conn.execute(
                    """
                    DELETE FROM affaire_lines
                    WHERE id = ? AND affaire_id = ? AND dpgf_article_id IS NULL
                    """,
                    (int(ch["line_id"]), affaire_id),
                )
                continue

            if ch.get("is_new_custom"):
                qty = float(ch.get("quantity") or 0)
                pu = ch.get("unit_price_ht")
                pu_f = float(pu) if pu is not None and pu != "" else 0.0
                total_ht = _round_money2(qty * pu_f)
                lot = (ch.get("line_lot") or "CFO").upper()
                if lot not in ("CFO", "CFA", "PV"):
                    lot = "CFO"
                cur = conn.execute(
                    """
                    INSERT INTO affaire_lines (
                        affaire_id, dpgf_article_id, quantity, quantity_source,
                        unit_price_ht, unit_price_source, total_ht, is_included,
                        ratio_ref, deviation_pct, line_designation, unit_override, line_lot
                    ) VALUES (?, NULL, ?, 'manual', ?, 'manual', ?, 1, ?, 0, ?, ?, ?)
                    """,
                    (
                        affaire_id,
                        qty,
                        pu_f,
                        total_ht,
                        pu_f,
                        (ch.get("line_designation") or "").strip() or "Sans désignation",
                        (ch.get("unit_override") or "").strip() or "u",
                        lot,
                    ),
                )
                tid = ch.get("temp_line_id")
                new_id = cur.lastrowid
                if tid is not None and new_id is not None:
                    new_ids.append({"temp_line_id": tid, "line_id": int(new_id)})
                continue

            dpgf = ch.get("dpgf_article_id")
            if dpgf not in (None, "", False):
                dpgf = int(dpgf)
                ref = _ref_pu_for_article_conn(conn, dpgf)
                qty = float(ch.get("quantity") or 0)
                pu_raw = ch.get("unit_price_ht")
                if pu_raw is None or pu_raw == "":
                    pu_db = None
                    pu_eff = ref
                else:
                    pu_db = float(pu_raw)
                    pu_eff = pu_db
                total_ht = _round_money2(qty * pu_eff)
                dev = ((pu_eff - ref) / ref * 100) if ref else 0.0

                row = conn.execute(
                    """
                    SELECT id FROM affaire_lines
                    WHERE affaire_id = ? AND dpgf_article_id = ?
                    """,
                    (affaire_id, dpgf),
                ).fetchone()
                if row:
                    conn.execute(
                        """
                        UPDATE affaire_lines SET
                            quantity = ?, quantity_source = 'manual',
                            unit_price_ht = ?, unit_price_source = 'manual',
                            total_ht = ?, ratio_ref = ?, deviation_pct = ?
                        WHERE id = ?
                        """,
                        (qty, pu_db, total_ht, ref, round(dev, 1), row["id"]),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO affaire_lines (
                            affaire_id, dpgf_article_id, quantity, quantity_source,
                            unit_price_ht, unit_price_source, total_ht, is_included,
                            ratio_ref, deviation_pct, unit_override, unit_source
                        ) VALUES (?, ?, ?, 'manual', ?, 'manual', ?, 1, ?, ?, NULL, 'ratio')
                        """,
                        (affaire_id, dpgf, qty, pu_db, total_ht, ref, round(dev, 1)),
                    )
                continue

            lid = ch.get("line_id")
            if lid is not None and lid != "" and ch.get("dpgf_article_id") in (None, "", False):
                qty = float(ch.get("quantity") or 0)
                pu = ch.get("unit_price_ht")
                pu_f = float(pu) if pu is not None and pu != "" else 0.0
                total_ht = _round_money2(qty * pu_f)
                lot = (ch.get("line_lot") or "CFO").upper()
                if lot not in ("CFO", "CFA", "PV"):
                    lot = "CFO"
                conn.execute(
                    """
                    UPDATE affaire_lines SET
                        quantity = ?, quantity_source = 'manual',
                        unit_price_ht = ?, unit_price_source = 'manual',
                        total_ht = ?,
                        line_designation = ?, unit_override = ?, line_lot = ?
                    WHERE id = ? AND affaire_id = ? AND dpgf_article_id IS NULL
                    """,
                    (
                        qty,
                        pu_f,
                        total_ht,
                        (ch.get("line_designation") or "").strip() or "Sans désignation",
                        (ch.get("unit_override") or "").strip() or "u",
                        lot,
                        int(lid),
                        affaire_id,
                    ),
                )

        kpis = _compute_estimation_kpis_conn(conn, affaire_id)
        conn.execute(
            """
            UPDATE affaires
            SET total_estime_ht = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (kpis["ALL"], affaire_id),
        )
        conn.commit()
        return {"status": "ok", "saved": len(changes), "totals": kpis, "new_ids": new_ids}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─── RÉFÉRENTIEL ──────────────────────────────────────────────────────────────

def get_categories() -> list:
    conn = get_db()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT id, name FROM building_categories ORDER BY name"
        ).fetchall()]
    finally:
        conn.close()


def delete_project(project_id: int) -> bool:
    """Supprime un projet et toutes ses devis_lines.

    `devis_lines` a une FK `project_id REFERENCES projects(id) ON DELETE CASCADE`
    si définie, sinon on supprime manuellement. On force PRAGMA foreign_keys=ON
    puis on supprime explicitement les deux pour rester robuste, peu importe
    l'état de la contrainte.
    """
    conn = get_db()
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM devis_lines WHERE project_id = ?", (project_id,))
        cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_projects_list() -> list:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT p.*, bc.name as category_name,
                   (SELECT COUNT(*) FROM devis_lines dl
                    WHERE dl.project_id=p.id AND dl.row_type='article'
                      AND dl.mapping_status IN ('auto','manual')) as nb_mapped,
                   (SELECT COUNT(*) FROM devis_lines dl
                    WHERE dl.project_id=p.id AND dl.row_type='article'
                      AND dl.mapping_status='pending') as nb_pending,
                   (SELECT COUNT(*) FROM devis_lines dl
                    WHERE dl.project_id=p.id AND dl.row_type='article'
                      AND dl.mapping_status='unmapped') as nb_unmapped
            FROM projects p
            LEFT JOIN building_categories bc ON p.category_id = bc.id
            ORDER BY p.devis_date DESC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_pending_lines(project_id: int) -> list:
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT dl.id, dl.original_designation, dl.unit,
                   dl.quantity, dl.unit_price_ht, dl.total_ht,
                   dl.mapping_status, dl.mapping_score, dl.mapping_candidate,
                   dl.context_path, dl.excel_row_num, dl.lot
            FROM devis_lines dl
            WHERE dl.project_id = ? AND dl.row_type = 'article'
              AND dl.mapping_status IN ('pending', 'unmapped')
            ORDER BY dl.excel_row_num
        """, (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_all_mappable_lines(project_id: int) -> list:
    """Retourne TOUTES les lignes extraites du devis source (articles + so),
    avec jointure sur l'article DPGF cible actuel pour pré-remplir le select
    et permettre à l'utilisateur de corriger un mapping automatique.

    Les champs du devis source sont exposés sous les alias `source_*` pour
    bien marquer dans le template qu'il s'agit des données d'origine et non
    de celles de l'article DPGF cible.
    """
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT dl.id,
                   dl.original_designation AS source_designation,
                   dl.unit                 AS source_unit,
                   dl.quantity             AS source_quantity,
                   dl.unit_price_ht        AS source_unit_price,
                   dl.total_ht             AS source_total_ht,
                   dl.mapping_status, dl.mapping_score, dl.mapping_candidate,
                   dl.context_path, dl.excel_row_num, dl.lot, dl.row_type,
                   dl.dpgf_article_id,
                   da.designation AS dpgf_designation,
                   da.chapter     AS dpgf_chapter,
                   da.unit        AS dpgf_unit
            FROM devis_lines dl
            LEFT JOIN dpgf_articles da ON dl.dpgf_article_id = da.id
            WHERE dl.project_id = ?
            ORDER BY dl.excel_row_num
        """, (project_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_bibliotheque_data(affaire_id=None) -> dict:
    """Données pour la page Bibliothèque DPGF.
    Si affaire_id fourni : prix et quantités depuis affaire_lines.
    Sinon : uniquement les articles du référentiel.
    """
    conn = get_db()
    try:
        affaires = conn.execute(
            "SELECT id, name, surface_sdo, kva_cible, phase_etude, statut FROM affaires ORDER BY name"
        ).fetchall()

        if affaire_id:
            rows = conn.execute("""
                SELECT da.id, da.chapter, da.section, da.designation, da.unit,
                       da.ratio_type, da.row_order,
                       da.is_custom, da.qty_ref,
                       COALESCE(ro.pu_override, al.unit_price_ht) AS pu_ht,
                       al.quantity, al.total_ht, al.is_included,
                       ro.pu_override IS NOT NULL AS has_override,
                       ro.raison AS override_raison
                FROM dpgf_articles da
                LEFT JOIN affaire_lines al
                    ON al.dpgf_article_id = da.id AND al.affaire_id = ?
                LEFT JOIN ratio_overrides ro ON ro.dpgf_article_id = da.id
                WHERE da.row_type = 'article'
                  AND (da.is_hidden IS NULL OR da.is_hidden = 0)
                ORDER BY da.row_order
            """, (affaire_id,)).fetchall()
        else:
            rows = conn.execute("""
                SELECT da.id, da.chapter, da.section, da.designation, da.unit,
                       da.ratio_type, da.row_order,
                       da.is_custom, da.qty_ref,
                       COALESCE(ro.pu_override, da.pu_ht_ref) AS pu_ht,
                       da.qty_ref AS quantity, NULL AS total_ht, 1 AS is_included,
                       ro.pu_override IS NOT NULL AS has_override,
                       ro.raison AS override_raison
                FROM dpgf_articles da
                LEFT JOIN ratio_overrides ro ON ro.dpgf_article_id = da.id
                WHERE da.row_type = 'article'
                  AND (da.is_hidden IS NULL OR da.is_hidden = 0)
                ORDER BY da.row_order
            """).fetchall()

        # Ratios par section (overrides manuels bibliothèque) — include ratio_unit (Sprint 9.3)
        sec_ratios_rows = conn.execute(
            "SELECT chapter, section, ratio_m2, ratio_unit FROM bibliotheque_section_ratios"
        ).fetchall()
        sec_ratios = {
            f"{r['chapter']}|||{r['section']}": {
                'ratio': r['ratio_m2'],
                'unit':  r['ratio_unit'] or 'm2',
            }
            for r in sec_ratios_rows
        }

        return {
            'affaires':     [dict(r) for r in affaires],
            'articles':     [dict(r) for r in rows],
            'sec_ratios':   sec_ratios,
        }
    finally:
        conn.close()


def hide_article(art_id: int):
    """Marque un article PSA comme masqué (is_hidden=1) sans supprimer le référentiel."""
    conn = get_db()
    try:
        conn.execute("UPDATE dpgf_articles SET is_hidden=1 WHERE id=? AND is_custom=0", (art_id,))
        conn.commit()
    finally:
        conn.close()


def delete_custom_article(art_id: int):
    """Supprime définitivement un article custom (is_custom=1).

    Même logique de FK que ``delete_bibliotheque_section`` : imports / mapping
    peuvent référencer ``dpgf_articles`` sans ON DELETE CASCADE.
    """
    conn = get_db()
    try:
        _verify_foreign_keys_enabled(conn)
        aid = int(art_id)
        conn.execute("DELETE FROM affaire_lines WHERE dpgf_article_id=?", (aid,))
        conn.execute("DELETE FROM ratio_overrides WHERE dpgf_article_id=?", (aid,))
        if _table_exists(conn, "devis_lines"):
            try:
                conn.execute(
                    "UPDATE devis_lines SET dpgf_article_id=NULL WHERE dpgf_article_id=?",
                    (aid,),
                )
            except sqlite3.OperationalError:
                pass
        for tbl in ("mapping_synonyms", "mapping_knowledge"):
            if _table_exists(conn, tbl):
                try:
                    conn.execute(
                        f"DELETE FROM {tbl} WHERE dpgf_article_id=?",
                        (aid,),
                    )
                except sqlite3.OperationalError:
                    pass
        conn.execute(
            "DELETE FROM dpgf_articles WHERE id=? AND COALESCE(is_custom,0)=1",
            (aid,),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def save_bibliotheque_save(changes: list):
    """Persiste les modifications inline de la bibliothèque (debounce 800 ms côté JS).

    Chaque élément de `changes` peut être :
      • { id, field:'pu_ht',      value }  → ratio_overrides (scope base)
      • { id, field:'designation', value } → dpgf_articles.designation
      • { id, field:'unit',        value } → dpgf_articles.unit
      • { id, field:'qty_ref',     value } → dpgf_articles.qty_ref
      • { is_new:True, chapter, section, designation, unit, qty_ref, pu_ht }
        → INSERT dpgf_articles is_custom=1 + ratio_overrides
    """
    if not changes:
        return []
    conn = get_db()
    new_ids = []
    _FIELDS_NO_ARTICLE_ID = frozenset(
        {"section_ratio", "section_ratio_rename", "section_delete"}
    )
    try:
        _verify_foreign_keys_enabled(conn)
        for c in changes:
            if c.get('is_new'):
                # Calcul row_order : max existant dans la section + 1
                max_order = conn.execute(
                    "SELECT COALESCE(MAX(row_order), 0) FROM dpgf_articles "
                    "WHERE chapter=? AND section=?",
                    (c.get('chapter', ''), c.get('section', ''))
                ).fetchone()[0]
                cur = conn.execute("""
                    INSERT INTO dpgf_articles
                      (designation, unit, chapter, section, row_order,
                       row_type, ratio_type, is_custom, qty_ref)
                    VALUES (?, ?, ?, ?, ?, 'article', 'UNITAIRE', 1, ?)
                """, (
                    c.get('designation', 'Nouvel article'),
                    c.get('unit', 'u'),
                    c.get('chapter', ''),
                    c.get('section', ''),
                    max_order + 1,
                    float(c.get('qty_ref') or 0),
                ))
                new_art_id = cur.lastrowid
                new_ids.append(new_art_id)
                pu = float(c.get('pu_ht') or 0)
                if pu > 0:
                    conn.execute("""
                        INSERT INTO ratio_overrides (dpgf_article_id, pu_override, raison)
                        VALUES (?, ?, 'Saisie bibliothèque')
                        ON CONFLICT(dpgf_article_id) DO UPDATE SET
                            pu_override = excluded.pu_override,
                            created_at  = CURRENT_TIMESTAMP
                    """, (new_art_id, pu))
            else:
                art_id = c.get('id')
                field  = c.get('field')
                value  = c.get('value')
                if field is None:
                    continue
                # Plusieurs actions bibliothèque n'ont pas d'article id (clé chapitre/section)
                if field not in _FIELDS_NO_ARTICLE_ID and not art_id:
                    continue
                if field == 'pu_ht':
                    pu = float(value or 0)
                    conn.execute("""
                        INSERT INTO ratio_overrides (dpgf_article_id, pu_override, raison)
                        VALUES (?, ?, '')
                        ON CONFLICT(dpgf_article_id) DO UPDATE SET
                            pu_override = excluded.pu_override,
                            created_at  = CURRENT_TIMESTAMP
                    """, (art_id, pu))
                elif field == 'designation':
                    conn.execute(
                        "UPDATE dpgf_articles SET designation=? WHERE id=?",
                        (str(value), art_id)
                    )
                elif field == 'unit':
                    conn.execute(
                        "UPDATE dpgf_articles SET unit=? WHERE id=?",
                        (str(value), art_id)
                    )
                elif field == 'qty_ref':
                    conn.execute(
                        "UPDATE dpgf_articles SET qty_ref=? WHERE id=?",
                        (float(value or 0), art_id)
                    )
                elif field == 'section':
                    conn.execute(
                        "UPDATE dpgf_articles SET section=? WHERE id=?",
                        (str(value), art_id)
                    )
                elif field == 'section_delete':
                    delete_bibliotheque_section(
                        conn, c.get('chapter', ''), c.get('section', '')
                    )
                elif field == 'section_ratio_rename':
                    # Renommer la clé section dans bibliotheque_section_ratios
                    old_sec = c.get('old_section', '')
                    new_sec = str(value)
                    chap    = c.get('chapter', '')
                    conn.execute(
                        "UPDATE bibliotheque_section_ratios SET section=? "
                        "WHERE chapter=? AND section=?",
                        (new_sec, chap, old_sec)
                    )
                elif field == 'section_ratio':
                    # value est un nombre ; ratio_unit optionnel dans extra
                    ratio_unit = c.get('ratio_unit', 'm2') or 'm2'
                    conn.execute("""
                        INSERT INTO bibliotheque_section_ratios (chapter, section, ratio_m2, ratio_unit)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(chapter, section) DO UPDATE SET
                            ratio_m2   = excluded.ratio_m2,
                            ratio_unit = excluded.ratio_unit,
                            updated_at = CURRENT_TIMESTAMP
                    """, (c.get('chapter', ''), c.get('section', ''), float(value or 0), ratio_unit))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return new_ids


def get_dpgf_articles_flat() -> list:
    """Retourne tous les articles du référentiel (pour le select de mapping)."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT id, code, designation, unit, chapter, section, ratio_type
            FROM dpgf_articles WHERE row_type='article'
            ORDER BY chapter, section, designation
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def assign_mapping(line_id: int, dpgf_article_id: int):
    """Assigne manuellement une ligne à un article DPGF."""
    conn = get_db()
    try:
        # Récupère lot et prix pour recalculer prix_normalise
        line = conn.execute(
            "SELECT unit_price_ht, project_id FROM devis_lines WHERE id=?", (line_id,)
        ).fetchone()
        article = conn.execute(
            "SELECT chapter FROM dpgf_articles WHERE id=?", (dpgf_article_id,)
        ).fetchone()

        if not line or not article:
            return

        project = conn.execute(
            "SELECT coef_cfo, coef_cfa, coef_pv FROM projects WHERE id=?",
            (line['project_id'],)
        ).fetchone()

        chapter = article['chapter'] or ''
        if 'CFA' in chapter.upper() or 'Faible' in chapter or 'SSI' in chapter:
            lot   = 'CFA'
            coef  = project['coef_cfa']
        elif 'PV' in chapter.upper() or 'Photovolta' in chapter:
            lot   = 'PV'
            coef  = project['coef_pv']
        else:
            lot   = 'CFO'
            coef  = project['coef_cfo']

        pu_ht       = line['unit_price_ht'] or 0
        prix_norm   = pu_ht / coef if coef else pu_ht

        conn.execute("""
            UPDATE devis_lines SET
                dpgf_article_id = ?,
                mapping_status  = 'manual',
                mapping_score   = 100.0,
                lot             = ?,
                prix_normalise  = ?
            WHERE id = ?
        """, (dpgf_article_id, lot, prix_norm, line_id))
        conn.commit()
    finally:
        conn.close()


def mark_unmapped(line_id: int):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE devis_lines SET mapping_status='unmapped' WHERE id=?",
            (line_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ─── RATIO OVERRIDES ──────────────────────────────────────────────────────────

def save_ratio_override(dpgf_article_id: int, pu_override: float, raison: str = ''):
    conn = get_db()
    try:
        conn.execute("""
            INSERT INTO ratio_overrides (dpgf_article_id, pu_override, raison)
            VALUES (?, ?, ?)
            ON CONFLICT(dpgf_article_id) DO UPDATE SET
                pu_override = excluded.pu_override,
                raison      = excluded.raison,
                created_at  = CURRENT_TIMESTAMP
        """, (dpgf_article_id, pu_override, raison))
        conn.commit()
    finally:
        conn.close()


def get_ratio_overrides() -> dict:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT dpgf_article_id, pu_override, raison, created_at FROM ratio_overrides"
        ).fetchall()
        return {r['dpgf_article_id']: dict(r) for r in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


# ─── Sprint 7 — Chapter settings (checkbox bloc + mode Macro) ────────────────

def get_chapter_settings(affaire_id: int) -> dict:
    """Retourne dict[chapter_key] = {is_included, use_macro, qty, ratio_m2_override}."""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT chapter_key, is_included, use_macro, qty, ratio_m2_override
            FROM affaire_chapter_settings
            WHERE affaire_id = ?
        """, (affaire_id,)).fetchall()
        return {r['chapter_key']: dict(r) for r in rows}
    finally:
        conn.close()


def save_chapter_settings(affaire_id: int, settings: list):
    """Persiste les checkboxes / modes des chapitres et sections.

    settings = [
        {chapter_key, is_included, use_macro, qty, ratio_m2_override?}, ...
    ]
    chapter_key : "chap:<designation>" ou "sect:<chap_designation>|<sect_designation>"
    """
    if not settings:
        return
    conn = get_db()
    try:
        for s in settings:
            conn.execute("""
                INSERT INTO affaire_chapter_settings
                    (affaire_id, chapter_key, is_included, use_macro, qty,
                     ratio_m2_override, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(affaire_id, chapter_key) DO UPDATE SET
                    is_included       = excluded.is_included,
                    use_macro         = excluded.use_macro,
                    qty               = excluded.qty,
                    ratio_m2_override = excluded.ratio_m2_override,
                    updated_at        = CURRENT_TIMESTAMP
            """, (
                affaire_id,
                s['chapter_key'],
                1 if s.get('is_included', True) else 0,
                1 if s.get('use_macro',   False) else 0,
                float(s.get('qty', 1.0)),
                s.get('ratio_m2_override'),
            ))
        conn.commit()
    finally:
        conn.close()


def init_chapter_settings(affaire_id: int, tree: list):
    """À la création d'une affaire : insère les settings par défaut
    (chapitres et sections : is_included=1, qty=1, use_macro=1 sur les
    chapitres, 0 sur les sections → ratio m² actif par défaut au niveau
    chapitre)."""
    settings = []
    for chapter in tree:
        settings.append({
            'chapter_key': f"chap:{chapter['designation']}",
            'is_included': True,
            'use_macro':   True,   # Sprint 7 §init : ratio m² actif d'emblée
            'qty':         1.0,
        })
        for section in chapter.get('sections', []):
            settings.append({
                'chapter_key': f"sect:{chapter['designation']}|{section['designation']}",
                'is_included': True,
                'use_macro':   False,
                'qty':         1.0,
            })
    save_chapter_settings(affaire_id, settings)
