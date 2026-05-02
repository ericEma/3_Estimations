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
                dpgf_article_id     INTEGER NOT NULL REFERENCES dpgf_articles(id),
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
                created_at          DATETIME DEFAULT CURRENT_TIMESTAMP
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
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM affaire_lines WHERE affaire_id = ?", (affaire_id,))
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
            "SELECT id, name, surface_sdo, phase_etude, statut FROM affaires ORDER BY name"
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
    Cascade manuelle : affaire_lines → ratio_overrides → dpgf_articles.
    """
    conn = get_db()
    try:
        # Cascade manuelle pour respecter la FK affaire_lines.dpgf_article_id
        conn.execute("DELETE FROM affaire_lines  WHERE dpgf_article_id=?", (art_id,))
        conn.execute("DELETE FROM ratio_overrides WHERE dpgf_article_id=?", (art_id,))
        conn.execute("DELETE FROM dpgf_articles   WHERE id=? AND is_custom=1", (art_id,))
        conn.commit()
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
    try:
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
                # section_ratio n'a pas d'art_id — les autres champs l'exigent
                if field != 'section_ratio' and not art_id:
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
                    # Supprime toute une section : masque les PSA, supprime les custom
                    chap = c.get('chapter', '')
                    sec  = c.get('section', '')
                    custom_ids = conn.execute(
                        "SELECT id FROM dpgf_articles "
                        "WHERE chapter=? AND section=? AND is_custom=1 AND row_type='article'",
                        (chap, sec)
                    ).fetchall()
                    for row in custom_ids:
                        conn.execute("DELETE FROM affaire_lines  WHERE dpgf_article_id=?", (row['id'],))
                        conn.execute("DELETE FROM ratio_overrides WHERE dpgf_article_id=?", (row['id'],))
                        conn.execute("DELETE FROM dpgf_articles   WHERE id=?", (row['id'],))
                    conn.execute(
                        "UPDATE dpgf_articles SET is_hidden=1 "
                        "WHERE chapter=? AND section=? AND is_custom=0 AND row_type='article'",
                        (chap, sec)
                    )
                    conn.execute(
                        "DELETE FROM bibliotheque_section_ratios WHERE chapter=? AND section=?",
                        (chap, sec)
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
