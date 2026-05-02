"""
check_quality.py - Module de Contrôle Qualité du mapping DPGF

Ce script doit être exécuté après chaque import ou validation pour vérifier :
  1. Intégrité Financière   : Σ(lignes) = Total HT source du projet
  2. Cohérence des Unités   : alerte si devis_line.unit ≠ famille DPGF article.unit
  3. Prix Hors Norme        : PU > N × moyenne v_ratios (défaut N=3.0)
  4. Intégrité Référentiel  : articles DPGF intacts, pas de FK brisées, doublons

Usage :
  python scripts/check_quality.py                      → dernier projet importé
  python scripts/check_quality.py --project-id 6       → projet spécifique
  python scripts/check_quality.py --project-id 6 --outlier-factor 2.0
"""
import sys
import io
import sqlite3
import argparse
from pathlib import Path
from loguru import logger

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from init_db import init_database
from scripts.scoring import unit_family

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
# CHECK 1 — Intégrité Financière
# ══════════════════════════════════════════════════════════════

def check_financial_integrity(conn: sqlite3.Connection, project_id: int) -> dict:
    """
    Vérifie que la somme des lignes importées correspond au Total HT source.
    Seuil de tolérance : 0.02 € (arrondi comptable).
    """
    proj = conn.execute(
        "SELECT name, total_ht_source, total_ht_importe, import_ok FROM projects WHERE id = ?",
        (project_id,)
    ).fetchone()
    if not proj:
        return {"ok": False, "error": f"Projet id={project_id} introuvable"}

    name, total_src, total_imp, import_ok = proj

    # Recalcul dynamique depuis les lignes (hors reliquats unmapped)
    total_calc = conn.execute(
        """
        SELECT COALESCE(SUM(total_ht), 0)
        FROM devis_lines
        WHERE project_id = ? AND row_type = 'article'
        """,
        (project_id,)
    ).fetchone()[0]

    # Détail par statut
    detail = {}
    for row in conn.execute(
        """
        SELECT mapping_status, COUNT(*) n, COALESCE(SUM(total_ht), 0) montant
        FROM devis_lines
        WHERE project_id = ? AND row_type = 'article'
        GROUP BY mapping_status
        """,
        (project_id,)
    ).fetchall():
        detail[row[0]] = {"n": row[1], "montant": row[2]}

    tolerance = 0.02
    ecart = abs(total_calc - (total_src or 0))
    ok    = total_src is not None and ecart <= tolerance

    return {
        "ok":         ok,
        "name":       name,
        "total_src":  total_src,
        "total_calc": total_calc,
        "ecart":      ecart,
        "import_ok":  bool(import_ok),
        "detail":     detail,
    }


# ══════════════════════════════════════════════════════════════
# CHECK 2 — Cohérence des Unités
# ══════════════════════════════════════════════════════════════

def check_unit_coherence(conn: sqlite3.Connection, project_id: int) -> list[dict]:
    """
    Identifie les devis_lines mappées (auto/manual) dont la famille d'unité
    ne correspond pas à celle de l'article DPGF associé.

    Une incohérence 'ml' (câble) vs 'u' (équipement) est une erreur métier grave.
    Sont exclus : familles 'unknown' des deux côtés (unité absente ou non classifiée).
    """
    rows = conn.execute(
        """
        SELECT
            dl.id, dl.excel_row_num, dl.original_designation,
            dl.unit AS devis_unit, dl.total_ht,
            da.id AS dpgf_id, da.excel_row_num AS dpgf_row,
            da.designation AS dpgf_desig, da.unit AS dpgf_unit,
            dl.mapping_status, dl.mapping_score
        FROM devis_lines dl
        JOIN dpgf_articles da ON da.id = dl.dpgf_article_id
        WHERE dl.project_id = ?
          AND dl.mapping_status IN ('auto', 'manual')
          AND dl.row_type = 'article'
        ORDER BY dl.excel_row_num
        """,
        (project_id,)
    ).fetchall()

    incoherences = []
    for r in rows:
        f_devis = unit_family(r[3])   # devis_unit
        f_dpgf  = unit_family(r[8])   # dpgf_unit
        if f_devis == "unknown" or f_dpgf == "unknown":
            continue
        if f_devis != f_dpgf:
            incoherences.append({
                "devis_line_id":    r[0],
                "devis_row":        r[1],
                "devis_desig":      r[2],
                "devis_unit":       r[3],
                "devis_family":     f_devis,
                "total_ht":         r[4],
                "dpgf_id":          r[5],
                "dpgf_row":         r[6],
                "dpgf_desig":       r[7],
                "dpgf_unit":        r[8],
                "dpgf_family":      f_dpgf,
                "mapping_status":   r[9],
                "mapping_score":    r[10],
            })
    return incoherences


# ══════════════════════════════════════════════════════════════
# CHECK 3 — Prix Hors Norme
# ══════════════════════════════════════════════════════════════

def check_price_outliers(
    conn: sqlite3.Connection,
    project_id: int,
    factor: float = 3.0,
) -> list[dict]:
    """
    Identifie les devis_lines dont le prix unitaire normalisé s'écarte
    de plus de `factor` fois la moyenne calculée dans v_ratios.

    Note : avec un seul projet importé, avg = valeur unique → pas d'alerte possible.
    Ce check devient utile à partir de 2 projets mappés sur le même article DPGF.

    Sont vérifiées uniquement les lignes is_stat_valid=1 avec unit_price_ht > 0.
    """
    rows = conn.execute(
        """
        SELECT
            dl.id, dl.excel_row_num, dl.original_designation,
            dl.unit, dl.unit_price_ht, dl.prix_normalise, dl.total_ht,
            da.id AS dpgf_id, da.designation AS dpgf_desig,
            vr.avg_pu_normalise, vr.nb_occurrences,
            dl.mapping_status
        FROM devis_lines dl
        JOIN dpgf_articles da ON da.id = dl.dpgf_article_id
        LEFT JOIN v_ratios vr  ON vr.dpgf_article_id = da.id
        WHERE dl.project_id = ?
          AND dl.mapping_status IN ('auto', 'manual')
          AND dl.row_type = 'article'
          AND dl.is_stat_valid = 1
          AND dl.unit_price_ht > 0
          AND dl.prix_normalise IS NOT NULL
          AND vr.avg_pu_normalise IS NOT NULL
          AND vr.nb_occurrences > 1
        ORDER BY dl.excel_row_num
        """,
        (project_id,)
    ).fetchall()

    outliers = []
    for r in rows:
        pu_norm = r[5]
        avg     = r[9]
        if avg and avg > 0:
            ratio = pu_norm / avg
            if ratio > factor or ratio < (1.0 / factor):
                outliers.append({
                    "devis_line_id":   r[0],
                    "devis_row":       r[1],
                    "devis_desig":     r[2],
                    "unit":            r[3],
                    "unit_price_ht":   r[4],
                    "prix_normalise":  r[5],
                    "total_ht":        r[6],
                    "dpgf_id":         r[7],
                    "dpgf_desig":      r[8],
                    "avg_pu_normalise": r[9],
                    "nb_occurrences":  r[10],
                    "ratio":           round(ratio, 2),
                    "mapping_status":  r[11],
                    "direction":       "HAUT" if ratio > factor else "BAS",
                })
    return outliers


# ══════════════════════════════════════════════════════════════
# CHECK 4 — Intégrité du Référentiel DPGF
# ══════════════════════════════════════════════════════════════

def check_referential_integrity(conn: sqlite3.Connection) -> dict:
    """
    Vérifie l'état de la table dpgf_articles :
      - Nombre total d'articles (réels + virtuels)
      - Liens FK brisés (devis_lines → dpgf_articles)
      - Articles dupliqués sur même position Excel (doublons de renommage)
      - Articles virtuels non promus
    """
    total   = conn.execute("SELECT COUNT(*) FROM dpgf_articles").fetchone()[0]
    virtual = conn.execute("SELECT COUNT(*) FROM dpgf_articles WHERE is_virtual=1").fetchone()[0]
    real    = total - virtual

    # FK brisées
    broken_fk = conn.execute(
        """
        SELECT COUNT(*) FROM devis_lines dl
        WHERE dl.dpgf_article_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM dpgf_articles da WHERE da.id = dl.dpgf_article_id)
        """
    ).fetchone()[0]

    # Doublons de position (même excel_row_num → plusieurs articles)
    duplicates = conn.execute(
        """
        SELECT excel_row_num, COUNT(*) n
        FROM dpgf_articles
        WHERE excel_row_num IS NOT NULL
        GROUP BY excel_row_num
        HAVING n > 1
        ORDER BY excel_row_num
        """
    ).fetchall()

    dup_detail = [{"excel_row_num": r[0], "n": r[1]} for r in duplicates]

    # Articles sans dpgf_article_id pointant dessus (orphelins dans les ratios)
    # Non bloquant : certains articles du DPGF sont juste non utilisés dans les devis
    unused = conn.execute(
        """
        SELECT COUNT(*) FROM dpgf_articles da
        WHERE da.row_type = 'article'
          AND da.is_virtual = 0
          AND NOT EXISTS (SELECT 1 FROM devis_lines dl WHERE dl.dpgf_article_id = da.id)
        """
    ).fetchone()[0]

    ok = broken_fk == 0

    return {
        "ok":                ok,
        "total_articles":    total,
        "real_articles":     real,
        "virtual_articles":  virtual,
        "broken_fk":         broken_fk,
        "duplicate_positions": len(dup_detail),
        "dup_detail":        dup_detail,
        "unused_articles":   unused,
    }


# ══════════════════════════════════════════════════════════════
# Rapport consolidé
# ══════════════════════════════════════════════════════════════

def _fmt_ok(ok: bool) -> str:
    return "OK" if ok else "ALERTE"


def run_full_check(
    conn: sqlite3.Connection,
    project_id: int,
    outlier_factor: float = 3.0,
) -> dict:
    """Lance les 4 vérifications et affiche un rapport complet."""

    logger.info("=" * 62)
    logger.info(f"CONTROLE QUALITE — Projet id={project_id}")
    logger.info("=" * 62)

    results = {}

    # ── CHECK 1 : Intégrité financière ────────────────────────
    fin = check_financial_integrity(conn, project_id)
    results["financial"] = fin
    status = _fmt_ok(fin["ok"])
    logger.info(f"\n[1] INTEGRITE FINANCIERE : {status}")
    if fin.get("error"):
        logger.error(f"    {fin['error']}")
    else:
        logger.info(f"    Projet       : {fin['name']}")
        logger.info(f"    Total source : {fin['total_src']:>14,.2f} €")
        logger.info(f"    Total calculé: {fin['total_calc']:>14,.2f} €")
        logger.info(f"    Écart        : {fin['ecart']:>14,.2f} €  {'<= 0.02 OK' if fin['ok'] else '>> ECART DETECTE'}")
        for status_k, v in fin.get("detail", {}).items():
            logger.info(f"      {status_k:<10} : {v['n']:>4} lignes | {v['montant']:>14,.2f} €")
        if not fin["ok"]:
            logger.error("    >>> ALERTE : Ecart financier non nul. Verifier les reliquats et lignes unmapped.")

    # ── CHECK 2 : Cohérence des unités ────────────────────────
    incoh = check_unit_coherence(conn, project_id)
    results["unit_coherence"] = incoh
    ok2 = len(incoh) == 0
    logger.info(f"\n[2] COHERENCE UNITES : {_fmt_ok(ok2)}")
    if incoh:
        logger.warning(f"    {len(incoh)} incohérence(s) détectée(s) :")
        for i in incoh:
            logger.warning(
                f"    >>> Devis L.{i['devis_row']} [{i['devis_unit']}/"
                f"{i['devis_family']}] "
                f"'{i['devis_desig'][:40]}'"
                f" -> DPGF L.{i['dpgf_row']} [{i['dpgf_unit']}/{i['dpgf_family']}]"
                f" '{i['dpgf_desig'][:40]}'"
                f" | {i['mapping_status']} | {i['total_ht']:,.0f} €"
            )
    else:
        logger.info("    Aucune incohérence d'unité détectée.")

    # ── CHECK 3 : Prix hors norme ─────────────────────────────
    outliers = check_price_outliers(conn, project_id, factor=outlier_factor)
    results["price_outliers"] = outliers
    ok3 = len(outliers) == 0
    logger.info(f"\n[3] PRIX HORS NORME (seuil x{outlier_factor:.1f}) : {_fmt_ok(ok3)}")
    if outliers:
        logger.warning(f"    {len(outliers)} ligne(s) hors norme :")
        for o in outliers:
            logger.warning(
                f"    >>> Devis L.{o['devis_row']} {o['direction']}"
                f" x{o['ratio']:.1f} | PU={o['unit_price_ht']:,.2f} €"
                f" vs avg={o['avg_pu_normalise']:,.2f} € ({o['nb_occurrences']} réf.)"
                f" | '{o['devis_desig'][:40]}'"
            )
    else:
        logger.info("    Aucune ligne hors norme détectée.")

    # ── CHECK 4 : Intégrité référentiel ───────────────────────
    ref = check_referential_integrity(conn)
    results["referential"] = ref
    logger.info(f"\n[4] INTEGRITE REFERENTIEL : {_fmt_ok(ref['ok'])}")
    logger.info(f"    Articles totaux  : {ref['total_articles']} ({ref['real_articles']} réels + {ref['virtual_articles']} virtuels)")
    logger.info(f"    FK brisées       : {ref['broken_fk']}  {'OK' if ref['broken_fk'] == 0 else '>>> ALERTE FK BRISEES'}")
    logger.info(f"    Articles inutilisés dans les ratios : {ref['unused_articles']}")
    if ref["duplicate_positions"] > 0:
        logger.warning(f"    Doublons de position (renommages) : {ref['duplicate_positions']}")
        for d in ref["dup_detail"][:10]:
            logger.warning(f"      L.{d['excel_row_num']} → {d['n']} articles (ancien + nouveau nom)")
        if len(ref["dup_detail"]) > 10:
            logger.warning(f"      ... (+{len(ref['dup_detail']) - 10} autres)")
    else:
        logger.info(f"    Doublons de position : 0  OK")

    # ── Synthèse ──────────────────────────────────────────────
    all_ok = fin["ok"] and ok2 and ok3 and ref["ok"]
    logger.info("\n" + "=" * 62)
    if all_ok:
        logger.success("SYNTHESE : TOUS LES CONTROLES PASSES")
    else:
        issues = []
        if not fin["ok"]:
            issues.append("Ecart financier")
        if not ok2:
            issues.append(f"{len(incoh)} incoherence(s) d'unite")
        if not ok3:
            issues.append(f"{len(outliers)} prix hors norme")
        if not ref["ok"]:
            issues.append(f"{ref['broken_fk']} FK brisee(s)")
        logger.warning(f"SYNTHESE : ALERTES -> {' | '.join(issues)}")
    logger.info("=" * 62 + "\n")

    results["all_ok"] = all_ok
    return results


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
        LOG_DIR / "check_quality.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="1 MB",
        retention="30 days",
        encoding="utf-8",
    )


if __name__ == "__main__":
    setup_logger()

    parser = argparse.ArgumentParser(description="Contrôle qualité du mapping DPGF")
    parser.add_argument("--project-id",     type=int,   default=None,
                        help="ID du projet (défaut : dernier importé)")
    parser.add_argument("--outlier-factor", type=float, default=3.0,
                        help="Seuil de détection hors norme (défaut : 3.0 = 300%%)")
    args = parser.parse_args()

    conn = init_database()
    try:
        if args.project_id:
            project_id = args.project_id
        else:
            row = conn.execute("SELECT id FROM projects ORDER BY id DESC LIMIT 1").fetchone()
            if not row:
                logger.error("Aucun projet en base. Lancez import_devis.py d'abord.")
                sys.exit(1)
            project_id = row[0]
            logger.info(f"Projet selectionné : id={project_id}")

        run_full_check(conn, project_id, outlier_factor=args.outlier_factor)
    finally:
        conn.close()
