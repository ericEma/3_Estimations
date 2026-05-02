"""
mapping_knowledge.py - Mémoire d'apprentissage des mappings validés

Mémorise les correspondances (désignation + unité) → article DPGF validées
explicitement par Eric dans validate_mapping.py.

Règles métier :
  - Clé = (source_designation normalisée, source_unit normalisée)
  - Score retourné : 100.0 avec méthode 'knowledge' si correspondance trouvée
  - Garde-fou unité : si l'unité change de famille (ex: u → ml), la connaissance
    est ignorée et on retombe sur le fuzzy match standard.
  - Alimentation : UNIQUEMENT sur choix explicites (l NNN, [1..N]) dans
    validate_mapping.py. Jamais sur les auto-mappings incertains.

Utilisation future possible (non implémentée) :
  - Extraction de marqueurs : si "HQM" est toujours mappé vers TDHQM,
    booster le poids de ce token dans le fuzzy match.
"""
import re
import sqlite3
from loguru import logger

from scripts.scoring import unit_family, UNIT_PENALTY_FACTOR


# ══════════════════════════════════════════════════════════════
# Normalisation
# ══════════════════════════════════════════════════════════════

def normalize_key(text: str | None) -> str:
    """
    Normalise une désignation ou unité pour la clé de recherche.
    strip + lower + espaces multiples → 1 espace.
    Retourne '' si text est None ou vide.
    """
    if not text:
        return ""
    return re.sub(r'\s+', ' ', str(text).strip().lower())


# ══════════════════════════════════════════════════════════════
# Migration — création de la table si absente
# ══════════════════════════════════════════════════════════════

def ensure_table(conn: sqlite3.Connection) -> None:
    """
    Crée mapping_knowledge si elle n'existe pas encore.
    Appelé à chaque ouverture de connexion (idempotent).
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mapping_knowledge (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            source_designation  TEXT NOT NULL,
            source_unit         TEXT NOT NULL,
            dpgf_article_id     INTEGER NOT NULL REFERENCES dpgf_articles(id),
            occurrence_count    INTEGER NOT NULL DEFAULT 1,
            last_used           DATE    NOT NULL DEFAULT CURRENT_DATE,
            created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_designation, source_unit)
        );
        CREATE INDEX IF NOT EXISTS idx_knowledge_desig ON mapping_knowledge(source_designation);
        CREATE INDEX IF NOT EXISTS idx_knowledge_unit  ON mapping_knowledge(source_unit);
    """)
    conn.commit()


# ══════════════════════════════════════════════════════════════
# Lecture — Lookup
# ══════════════════════════════════════════════════════════════

def lookup(
    conn: sqlite3.Connection,
    designation: str,
    unit: str | None,
) -> dict | None:
    """
    Recherche une correspondance apprise pour (designation, unit).

    Garde-fou unité :
      - Si la correspondance mémorisée a une unité de MÊME famille → retourne le hit.
      - Si la famille a changé (ex: u mémorisé, mais la ligne est maintenant ml)
        → retourne None (la connaissance est ignorée, fuzzy reprend la main).
      - Si l'une des unités est 'unknown' → on ne pénalise pas (on retourne le hit).

    Returns None si aucune correspondance apprise.
    """
    norm_desig = normalize_key(designation)
    norm_unit  = normalize_key(unit)

    row = conn.execute(
        """
        SELECT mk.dpgf_article_id, mk.occurrence_count, mk.last_used,
               da.designation, da.unit, da.chapter, da.section,
               da.excel_row_num, da.excel_row_label, da.is_virtual
        FROM mapping_knowledge mk
        JOIN dpgf_articles da ON da.id = mk.dpgf_article_id
        WHERE mk.source_designation = ?
          AND mk.source_unit        = ?
        """,
        (norm_desig, norm_unit),
    ).fetchone()

    if not row:
        return None

    # Garde-fou famille d'unité
    f_devis = unit_family(unit)
    f_dpgf  = unit_family(row["unit"] if hasattr(row, 'keys') else row[4])
    if f_devis != "unknown" and f_dpgf != "unknown" and f_devis != f_dpgf:
        logger.debug(
            f"Knowledge ignorée : famille changée ({unit}/{f_devis} "
            f"vs {row[4]}/{f_dpgf}) pour '{designation[:40]}'"
        )
        return None

    return {
        "id":              row[0] if not hasattr(row, 'keys') else row["dpgf_article_id"],
        "occurrence_count": row[1] if not hasattr(row, 'keys') else row["occurrence_count"],
        "last_used":       row[2] if not hasattr(row, 'keys') else row["last_used"],
        "designation":     row[3] if not hasattr(row, 'keys') else row["designation"],
        "unit":            row[4] if not hasattr(row, 'keys') else row["unit"],
        "chapter":         row[5] if not hasattr(row, 'keys') else row["chapter"],
        "section":         row[6] if not hasattr(row, 'keys') else row["section"],
        "excel_row_num":   row[7] if not hasattr(row, 'keys') else row["excel_row_num"],
        "excel_row_label": (row[8] or str(row[7])) if not hasattr(row, 'keys') else (row["excel_row_label"] or str(row["excel_row_num"])),
        "is_virtual":      bool(row[9] if not hasattr(row, 'keys') else row["is_virtual"]),
        "score":           100.0,
        "source":          "knowledge",
    }


def load_index(conn: sqlite3.Connection) -> dict:
    """
    Charge toute la table mapping_knowledge en mémoire sous la forme :
      {(norm_desig, norm_unit): {dpgf_article_id, designation, unit, ...}}

    Utilisé par import_devis.py pour éviter une requête BDD par ligne.
    """
    index = {}
    rows = conn.execute(
        """
        SELECT mk.source_designation, mk.source_unit,
               mk.dpgf_article_id, mk.occurrence_count,
               da.designation, da.unit, da.chapter, da.section,
               da.excel_row_num, da.excel_row_label, da.is_virtual
        FROM mapping_knowledge mk
        JOIN dpgf_articles da ON da.id = mk.dpgf_article_id
        """
    ).fetchall()

    for r in rows:
        key = (r[0], r[1])   # déjà normalisé en base
        index[key] = {
            "id":           r[2],
            "occurrence":   r[3],
            "designation":  r[4],
            "unit":         r[5],
            "chapter":      r[6],
            "section":      r[7],
            "excel_row_num": r[8],
            "excel_row_label": r[9] or str(r[8]),
            "is_virtual":   bool(r[10]),
        }
    return index


def lookup_in_index(
    index: dict,
    designation: str,
    unit: str | None,
) -> dict | None:
    """
    Lookup dans l'index pré-chargé (sans requête BDD).
    Applique le même garde-fou de famille d'unité que `lookup()`.
    """
    key = (normalize_key(designation), normalize_key(unit))
    art = index.get(key)
    if not art:
        return None

    f_devis = unit_family(unit)
    f_dpgf  = unit_family(art["unit"])
    if f_devis != "unknown" and f_dpgf != "unknown" and f_devis != f_dpgf:
        return None

    return {**art, "score": 100.0, "source": "knowledge"}


# ══════════════════════════════════════════════════════════════
# Écriture — Record
# ══════════════════════════════════════════════════════════════

def record(
    conn: sqlite3.Connection,
    designation: str,
    unit: str | None,
    dpgf_article_id: int,
) -> str:
    """
    Enregistre ou met à jour une correspondance apprise.

    Utilise INSERT OR REPLACE avec UPSERT (SQLite >= 3.24).
    La colonne `dpgf_article_id` est mise à jour si la désignation est
    re-validée sur un article différent (correction explicite d'Eric).

    Returns : 'new' | 'updated' | 'unchanged'
    """
    norm_desig = normalize_key(designation)
    norm_unit  = normalize_key(unit)

    existing = conn.execute(
        "SELECT dpgf_article_id, occurrence_count FROM mapping_knowledge "
        "WHERE source_designation = ? AND source_unit = ?",
        (norm_desig, norm_unit),
    ).fetchone()

    if existing is None:
        conn.execute(
            """
            INSERT INTO mapping_knowledge
                (source_designation, source_unit, dpgf_article_id, occurrence_count, last_used)
            VALUES (?, ?, ?, 1, CURRENT_DATE)
            """,
            (norm_desig, norm_unit, dpgf_article_id),
        )
        conn.commit()
        return "new"

    # Article différent → correction explicite d'Eric → on met à jour
    same_article = existing[0] == dpgf_article_id
    conn.execute(
        """
        UPDATE mapping_knowledge
        SET dpgf_article_id  = ?,
            occurrence_count = occurrence_count + 1,
            last_used        = CURRENT_DATE
        WHERE source_designation = ? AND source_unit = ?
        """,
        (dpgf_article_id, norm_desig, norm_unit),
    )
    conn.commit()
    return "unchanged" if same_article else "updated"


# ══════════════════════════════════════════════════════════════
# Statistiques — Rapport de session
# ══════════════════════════════════════════════════════════════

def session_report(conn: sqlite3.Connection, session_counts: dict) -> None:
    """
    Affiche le rapport d'intelligence acquise à la fin d'une session
    de validation.

    Args:
        session_counts : {'new': int, 'updated': int, 'unchanged': int}
    """
    total_in_db = conn.execute(
        "SELECT COUNT(*) FROM mapping_knowledge"
    ).fetchone()[0]

    logger.success("=" * 62)
    logger.success("INTELLIGENCE ACQUISE — SESSION")
    logger.success("=" * 62)
    logger.success(f"  Nouvelles règles mémorisées  : {session_counts.get('new', 0):>4}")
    logger.success(f"  Règles mises à jour          : {session_counts.get('updated', 0):>4}")
    logger.success(f"  Règles confirmées (inchangées): {session_counts.get('unchanged', 0):>4}")
    logger.success(f"  ─────────────────────────────────")
    logger.success(f"  Total règles en base          : {total_in_db:>4}")
    logger.success("=" * 62)

    if total_in_db > 0:
        # Top 5 désignations les plus fréquentes
        top = conn.execute(
            """
            SELECT mk.source_designation, mk.source_unit, mk.occurrence_count,
                   da.designation AS dpgf_desig
            FROM mapping_knowledge mk
            JOIN dpgf_articles da ON da.id = mk.dpgf_article_id
            ORDER BY mk.occurrence_count DESC
            LIMIT 5
            """
        ).fetchall()
        if top:
            logger.info("  Top correspondances mémorisées :")
            for r in top:
                logger.info(
                    f"    [{r[2]:>2}x] '{r[0][:35]}' [{r[1] or '?'}]"
                    f" → '{r[3][:35]}'"
                )
