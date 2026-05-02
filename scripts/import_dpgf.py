"""
import_dpgf.py - Import / Synchronisation du référentiel DPGF
Sprint 2 — v2 UPSERT par désignation

Clé d'ancrage : designation (normalisée : strip + lower + espaces unifiés)

Logique UPSERT (comportement par défaut) :
  - Désignation existante → UPDATE metadata (position Excel, ratio_type, unité, chapitre…)
    mais CONSERVE l'id original pour préserver les liens devis_lines.
  - Article virtuel (is_virtual=1) absorbé par l'Excel → promu en article réel (is_virtual=0),
    id conservé → les mappings devis existants pointent automatiquement vers le bon article.
  - Désignation nouvelle → INSERT (nouvel article, nouvel id).
  - Articles supprimés du fichier Excel → conservés en base (jamais DELETE).

Usage :
  python scripts/import_dpgf.py          → UPSERT sync (sûr, idempotent)
  python scripts/import_dpgf.py --reset  → vide dpgf_articles puis réimporte
                                           DANGER : brise tous les mappings devis existants !
"""
import re
import sys
import sqlite3
from pathlib import Path
from loguru import logger

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from scripts.read_excel import parse_dpgf, DPGF_FILE
from init_db import init_database

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _normalize_desig(s: str) -> str:
    """Clé de déduplication : strip + lower + espaces multiples → 1 espace."""
    return re.sub(r'\s+', ' ', s.strip().lower())


def _extract_chapter_num(code: str | None) -> str | None:
    if code and "." in code:
        return code.split(".")[0]
    return None


def _clear_dpgf_table(conn: sqlite3.Connection):
    conn.execute("DELETE FROM dpgf_articles")
    conn.execute("DELETE FROM sqlite_sequence WHERE name='dpgf_articles'")
    conn.commit()
    logger.warning("Table dpgf_articles vidée (--reset).")


# ══════════════════════════════════════════════════════════════
# Chargement de l'index existant
# ══════════════════════════════════════════════════════════════

def _load_existing_index(conn: sqlite3.Connection) -> dict:
    """
    Retourne {normalized_desig: {id, excel_row_num, ratio_type, unit, is_virtual}}
    pour tous les articles actuellement en base.
    """
    index = {}
    conn.row_factory = sqlite3.Row
    for row in conn.execute(
        "SELECT id, designation, excel_row_num, ratio_type, unit, is_virtual FROM dpgf_articles"
    ):
        key = _normalize_desig(row["designation"])
        index[key] = {
            "id":           row["id"],
            "excel_row_num": row["excel_row_num"],
            "ratio_type":   row["ratio_type"],
            "unit":         row["unit"],
            "is_virtual":   row["is_virtual"],
        }
    conn.row_factory = None
    return index


# ══════════════════════════════════════════════════════════════
# Import principal — UPSERT
# ══════════════════════════════════════════════════════════════

def import_dpgf(conn: sqlite3.Connection, reset: bool = False) -> dict:
    """
    Synchronise dpgf_articles avec le fichier Excel via UPSERT.

    Returns : dict avec clés inserted, updated, virtual_promoted, unchanged.
    """
    if reset:
        _clear_dpgf_table(conn)

    # ── Parsing Excel ─────────────────────────────────────────
    if not DPGF_FILE.exists():
        logger.error(f"Fichier introuvable : {DPGF_FILE}")
        sys.exit(1)

    rows, stats = parse_dpgf(DPGF_FILE)
    logger.info(f"Parsing DPGF : {stats}")

    articles_ok      = [r for r in rows if r.row_type == "article" and r.unit]
    articles_no_unit = [r for r in rows if r.row_type == "article" and not r.unit]

    logger.info(f"Articles avec unité (à synchroniser) : {len(articles_ok)}")
    logger.info(f"Articles sans unité (ignorés)         : {len(articles_no_unit)}")
    if articles_no_unit:
        for r in articles_no_unit:
            logger.warning(f"  Ignoré (sans unité) Row {r.row_num:>4} | {r.designation[:60]}")

    # ── Chargement de l'index existant ───────────────────────
    existing = _load_existing_index(conn)
    logger.info(f"Articles déjà en base : {len(existing)}")

    # ── UPSERT ────────────────────────────────────────────────
    counts = {"inserted": 0, "updated": 0, "virtual_promoted": 0, "unchanged": 0}

    for i, r in enumerate(articles_ok):
        key         = _normalize_desig(r.designation)
        chapter_num = _extract_chapter_num(r.code)

        if key in existing:
            ex = existing[key]
            was_virtual = bool(ex["is_virtual"])

            # Détecter si quelque chose change (position ou métadonnées)
            changed = (
                ex["excel_row_num"] != r.row_num
                or ex["ratio_type"] != r.ratio_type
                or (ex["unit"] or "").strip().lower() != (r.unit or "").strip().lower()
                or was_virtual
            )

            conn.execute(
                """
                UPDATE dpgf_articles SET
                    code             = ?,
                    unit             = ?,
                    chapter          = ?,
                    chapter_num      = ?,
                    section          = ?,
                    row_order        = ?,
                    excel_row_num    = ?,
                    excel_row_label  = ?,
                    ratio_type       = ?,
                    ratio_type_source = ?,
                    is_virtual       = 0
                WHERE id = ?
                """,
                (
                    r.code, r.unit, r.chapter, chapter_num, r.section,
                    i + 1, r.row_num, str(r.row_num),
                    r.ratio_type, r.ratio_type_source,
                    ex["id"],
                ),
            )

            if was_virtual:
                counts["virtual_promoted"] += 1
                logger.success(
                    f"  ↑ PROMU virtuel→réel  id={ex['id']:>4} | L.{r.row_num:>3} | {r.designation[:55]}"
                )
            elif changed:
                counts["updated"] += 1
                logger.info(
                    f"  ↻ Mis à jour          id={ex['id']:>4} | L.{r.row_num:>3} (était L.{ex['excel_row_num']}) | {r.designation[:50]}"
                )
            else:
                counts["unchanged"] += 1

        else:
            # Nouvel article
            conn.execute(
                """
                INSERT INTO dpgf_articles
                    (code, designation, unit, chapter, chapter_num, section,
                     row_order, excel_row_num, excel_row_label,
                     row_type, ratio_type, ratio_type_source, is_virtual)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    r.code, r.designation, r.unit, r.chapter, chapter_num, r.section,
                    i + 1, r.row_num, str(r.row_num),
                    r.row_type, r.ratio_type, r.ratio_type_source,
                ),
            )
            counts["inserted"] += 1
            logger.success(
                f"  + NOUVEAU              L.{r.row_num:>3} | {r.designation[:55]} | {r.unit} | {r.ratio_type}"
            )

    conn.commit()

    # ── Rapport final ─────────────────────────────────────────
    total_in_db = conn.execute("SELECT COUNT(*) FROM dpgf_articles").fetchone()[0]
    logger.success("=" * 60)
    logger.success("RAPPORT SYNCHRONISATION DPGF")
    logger.success("=" * 60)
    logger.success(f"  Nouveaux articles créés     : {counts['inserted']:>4}")
    logger.success(f"  Articles mis à jour (pos.)  : {counts['updated']:>4}")
    logger.success(f"  Articles virtuels promus    : {counts['virtual_promoted']:>4}")
    logger.success(f"  Articles inchangés          : {counts['unchanged']:>4}")
    logger.success(f"  ─────────────────────────────────")
    logger.success(f"  Total articles en base      : {total_in_db:>4}")
    logger.success("=" * 60)

    # Répartition par chapitre
    cur = conn.execute(
        "SELECT chapter, COUNT(*) n FROM dpgf_articles GROUP BY chapter ORDER BY n DESC"
    )
    logger.info("Répartition par chapitre :")
    for row in cur.fetchall():
        logger.info(f"  {(row[0] or 'N/A'):<45} : {row[1]} articles")

    return counts


# ══════════════════════════════════════════════════════════════
# Entrée principale
# ══════════════════════════════════════════════════════════════

def setup_logger():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )
    logger.add(
        LOG_DIR / "import_dpgf.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="1 MB",
        retention="30 days",
        encoding="utf-8",
    )


if __name__ == "__main__":
    setup_logger()
    reset_flag = "--reset" in sys.argv

    if reset_flag:
        logger.warning("!" * 60)
        logger.warning("AVERTISSEMENT : --reset va vider dpgf_articles et recréer")
        logger.warning("tous les articles avec de nouveaux ids.")
        logger.warning("CELA BRISERA tous les mappings du projet PSA Urgences !")
        logger.warning("!" * 60)
        confirm = input("Confirmer le reset ? (tapez 'RESET' en majuscules) : ")
        if confirm.strip() != "RESET":
            logger.info("Reset annulé.")
            sys.exit(0)

    conn = init_database()
    try:
        counts = import_dpgf(conn, reset=reset_flag)
        total = counts["inserted"] + counts["updated"] + counts["virtual_promoted"]
        logger.success(f"Sync terminée. {total} articles modifiés / {counts['unchanged']} inchangés.")
    finally:
        conn.close()
