"""
import_referentiel.py — Importation du fichier DPGF/Estimation Master dans dpgf_articles.

Source cible   : 2_estimation_modele_09-05-2026.xlsx  (argument --file)
Défaut fallback: 2_ DPGF_Modèle_ 10-04-2026.xlsx

Règles d'extraction (v2 — 2026-05-09) :
  - Nature='Titre'   → section courante (pas de ligne en DB)
  - Nature='Article' → article importé
  - Nature vide + DESIGNATION commence par '.' → .VARIANTE → normalisé en Article
  - Nature vide + DESIGNATION sans '.' → importé si :
      DESIGNATION non vide ET (PU > 0 OU Unité renseignée OU Nature='Article')
  - Exclusions : "Sous-Total", "TVA", "TOTAL" (case-insensitive)
  - Type ratio vide → UNITAIRE par défaut
  - Reset affaires + affaire_lines intégré avant l'insertion

Usage :
    python scripts/import_referentiel.py
    python scripts/import_referentiel.py --file "2_estimation_modele_09-05-2026.xlsx"
    python scripts/import_referentiel.py --dry-run
    python scripts/import_referentiel.py --yes

Options :
    --file   <path>  Chemin du fichier Excel (absolu ou relatif au répertoire projet)
    --yes            Pas de confirmation interactive
    --dry-run        Analyse et affiche, n'écrit pas en base
    --date   <YYYY-MM-DD>  Force la date last_updated (défaut : today)
"""

import sys
import os
import argparse
import sqlite3
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import openpyxl
except ImportError:
    sys.exit("openpyxl manquant — pip install openpyxl")

import models  # noqa: E402

# ─── Constantes ───────────────────────────────────────────────────────────────

_DEFAULT_FILE = "2_ DPGF_Modèle_ 10-04-2026.xlsx"
_TARGET_FILE  = "2_estimation_modele_09-05-2026.xlsx"

# Mots-clés d'exclusion (casse-insensitive, sur la DESIGNATION)
_EXCLUDE_KEYWORDS = ("sous-total", "tva", "total")
# Mots acceptés malgré la présence de "total" dans le libellé (ex : "Total mensuel KVA")
_EXCLUDE_WHITELIST = ()  # à enrichir si besoin

# Chapitres reconnus (casse exacte telle qu'en BDD)
_KNOWN_CHAPTERS = {"Courants Forts", "Courants faibles", "Photovoltaïque"}

# Dérivation lot depuis chapitre (réplique de models.derive_lot_from_chapter)
def _lot_from_chapter(chapter: str) -> str:
    d = (chapter or "").lower()
    if "faible" in d or "cfa" in d:
        return "CFA"
    if "photovolta" in d:
        return "PV"
    return "CFO"


# ─── Parsing Excel ────────────────────────────────────────────────────────────

def _is_excluded(desig: str) -> bool:
    """True si la désignation doit être ignorée (Sous-Total, TVA, TOTAL…)."""
    dl = desig.lower()
    return any(kw in dl for kw in _EXCLUDE_KEYWORDS)


def _parse_pu(val) -> float | None:
    """Convertit une valeur cellule en float > 0, ou None."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def _parse_qty(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return f if f >= 0 else None
    except (ValueError, TypeError):
        return None


def _parse_excel(filepath: str) -> list[dict]:
    """Retourne la liste des articles à importer depuis le fichier Excel.

    Règles (v2) :
    - Nature='Titre'       → mise à jour section courante, pas de ligne DB.
    - Nature='Article'     → importé.
    - Nature vide + désig  commence par '.' → .VARIANTE → normalisé Article.
    - Nature vide + désig  sans '.'         → importé si :
          désignation non vide ET (PU > 0 OU unité renseignée)
    - Exclusions : mots-clés _EXCLUDE_KEYWORDS (sous-total, tva, total).
    - Type ratio vide → UNITAIRE.
    """
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    ws = wb.active
    rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()

    # ── Trouver la ligne d'en-tête ────────────────────────────────────────────
    header_idx = None
    for i, row in enumerate(rows_raw):
        if row and str(row[0] or "").strip().lower() == "art.":
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("Ligne d'en-tête 'Art.' introuvable. Vérifier le fichier source.")

    # ── Mapper les colonnes ───────────────────────────────────────────────────
    header = rows_raw[header_idx]
    col: dict[str, int] = {}
    for j, h in enumerate(header):
        if h is None:
            continue
        hn = str(h).strip().lower()
        if hn == "art.":
            col["art"] = j
        elif "type ratio" in hn:
            col["ratio_type"] = j
        elif hn == "nature":
            col["nature"] = j
        elif "designation" in hn or "désignation" in hn:
            col["designation"] = j
        elif hn in ("u", "unité", "unite"):
            col["unit"] = j
        elif "q entreprise" in hn:
            col["qty_ref"] = j
        elif "pu" in hn and "ht" in hn:
            col["pu_ht"] = j

    for req in ("art", "nature", "designation"):
        if req not in col:
            raise ValueError(f"Colonne requise absente : '{req}'. En-tête : {header}")

    def _cell(raw, key, default=None):
        idx = col.get(key, -1)
        if idx < 0 or idx >= len(raw):
            return default
        return raw[idx]

    result = []
    current_chapter = None
    current_section = None
    order_global = 0
    stats = {"titre": 0, "article_tagged": 0, "variante": 0, "untagged": 0,
             "excluded": 0, "empty": 0}

    for i, raw in enumerate(rows_raw[header_idx + 1:], start=header_idx + 2):
        art_v    = _cell(raw, "art")
        ratio_v  = _cell(raw, "ratio_type")
        nature_v = _cell(raw, "nature")
        desig_v  = _cell(raw, "designation")
        unit_v   = _cell(raw, "unit")
        pu_v     = _cell(raw, "pu_ht")
        qty_v    = _cell(raw, "qty_ref")

        art_s    = str(art_v).strip()    if art_v    is not None else ""
        desig_s  = str(desig_v).strip()  if desig_v  is not None else ""
        nature_s = str(nature_v).strip() if nature_v is not None else ""
        unit_s   = str(unit_v).strip()   if unit_v   is not None else ""
        ratio_s  = str(ratio_v).strip()  if ratio_v  is not None else ""

        # ── 1. Ligne entièrement vide ─────────────────────────────────────────
        if not art_s and not desig_s and not nature_s and not unit_s:
            stats["empty"] += 1
            continue

        # ── 2. Exclusions (Sous-Total / TVA / TOTAL) ─────────────────────────
        if desig_s and _is_excluded(desig_s):
            stats["excluded"] += 1
            continue

        # ── 3. Ligne chapitre (col A non vide, B et C vides, D vide) ─────────
        if art_s and not ratio_v and not nature_v and not desig_s:
            if art_s in _KNOWN_CHAPTERS:
                current_chapter = art_s
                current_section = None
            # sinon : ligne de commentaire / texte legale → ignorer
            continue

        # ── 4. Section / Titre ────────────────────────────────────────────────
        if nature_s == "Titre" and current_chapter:
            current_section = desig_s
            stats["titre"] += 1
            continue  # les sections ne sont pas insérées en DB (clé implicite)

        # ── Helpers communs ───────────────────────────────────────────────────
        if not current_chapter:
            continue  # on n'est pas encore entré dans un chapitre connu

        pu_float  = _parse_pu(pu_v)
        qty_float = _parse_qty(qty_v)
        ratio_norm = _normalize_ratio_type(ratio_s)
        unit_clean = unit_s if unit_s else None

        def _make_article(designation: str, is_variante: bool = False) -> dict:
            order_global  # non-local read — Python closure
            return {
                "row_type":    "article",
                "chapter":     current_chapter,
                "section":     current_section,
                "designation": designation,
                "ratio_type":  ratio_norm,
                "unit":        unit_clean,
                "pu_ht_ref":   pu_float,
                "qty_ref":     qty_float,
                "excel_row":   i,
                "code":        art_s if art_s else None,
                "is_variante": is_variante,
            }

        # ── 5. Article taggé (Nature='Article') ──────────────────────────────
        if nature_s == "Article":
            if not desig_s:
                continue
            # Normalisation : retire le '.' de tête si présent (cohérence DB)
            desig_clean = desig_s.lstrip(".").strip() if desig_s.startswith(".") else desig_s
            if not desig_clean:
                continue
            stats["article_tagged"] += 1
            result.append(_make_article(desig_clean))
            continue

        # ── 6. .VARIANTE (désignation commence par '.') → normalisé Article ──
        if desig_s.startswith("."):
            desig_clean = desig_s.lstrip(".").strip()
            if not desig_clean:
                continue
            stats["variante"] += 1
            result.append(_make_article(desig_clean, is_variante=True))
            continue

        # ── 7. Ligne non-taggée : filtre DESIGNATION + (PU > 0 OU unité) ─────
        if desig_s and (pu_float is not None or unit_clean):
            stats["untagged"] += 1
            result.append(_make_article(desig_s))
            continue

    return result, stats


def _normalize_ratio_type(val: str) -> str:
    """'Surfacique' → 'SURFACIQUE', 'Unitaire' → 'UNITAIRE', autre → 'UNITAIRE'."""
    v = val.strip().upper()
    if "SURF" in v:
        return "SURFACIQUE"
    return "UNITAIRE"


# ─── Écriture en base ─────────────────────────────────────────────────────────

def _ensure_last_updated_column(conn: sqlite3.Connection) -> None:
    """Migration idempotente : ajoute last_updated et lot à dpgf_articles si absentes."""
    cols = {c[1] for c in conn.execute("PRAGMA table_info(dpgf_articles)").fetchall()}
    added = []
    if "last_updated" not in cols:
        conn.execute("ALTER TABLE dpgf_articles ADD COLUMN last_updated DATE")
        added.append("last_updated")
    if "lot" not in cols:
        conn.execute("ALTER TABLE dpgf_articles ADD COLUMN lot TEXT")
        added.append("lot")
    if added:
        conn.commit()
        print(f"  Migration : colonnes ajoutées à dpgf_articles : {', '.join(added)}")


def _ensure_synonyms_table(conn: sqlite3.Connection) -> None:
    """Crée la table synonyms si absente."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS synonyms (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            original_term TEXT NOT NULL,
            mapped_term   TEXT NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(original_term)
        )
    """)
    conn.commit()


def _clear_referentiel_psa(conn: sqlite3.Connection) -> int:
    """Supprime tous les articles PSA master (is_custom=0) et dépendances FK.

    Ordre :
      1. ratio_overrides → dpgf_articles
      2. mapping_synonyms → dpgf_articles
      3. mapping_knowledge → dpgf_articles
      4. affaire_lines → dpgf_articles (déjà vides si init_db a tourné, sinon nettoyage défensif)
      5. DELETE dpgf_articles WHERE is_custom=0
    """
    conn.execute("PRAGMA foreign_keys = OFF")

    psa_ids = [
        r[0] for r in conn.execute(
            "SELECT id FROM dpgf_articles WHERE COALESCE(is_custom, 0) = 0"
        ).fetchall()
    ]
    n = len(psa_ids)
    if not psa_ids:
        conn.execute("PRAGMA foreign_keys = ON")
        return 0

    ph = ",".join("?" * len(psa_ids))
    for tbl in ("ratio_overrides", "mapping_synonyms", "affaire_lines"):
        try:
            conn.execute(
                f"DELETE FROM {tbl} WHERE dpgf_article_id IN ({ph})", psa_ids
            )
        except Exception:
            pass
    for tbl in ("mapping_knowledge",):
        try:
            conn.execute(
                f"DELETE FROM {tbl} WHERE dpgf_article_id IN ({ph})", psa_ids
            )
        except Exception:
            pass

    conn.execute(
        "DELETE FROM dpgf_articles WHERE COALESCE(is_custom, 0) = 0"
    )
    conn.execute("PRAGMA foreign_keys = ON")
    return n


def _insert_rows(conn: sqlite3.Connection, rows: list[dict], ref_date: str) -> dict:
    """Insère les articles en base. Retourne les compteurs.

    Architecture DB : les sections ne sont PAS des lignes dpgf_articles.
    Elles sont représentées implicitement par le champ ``section`` de chaque article.
    La reconstruction Chapitre→Section→Articles se fait par GROUP BY.
    """
    counts = {"section_skipped": 0, "article": 0}
    order_global = 0

    # Pré-calcul : pour chaque section, récupère le ratio_type et l'unité de la tête Titre
    section_meta: dict[tuple, dict] = {}
    for r in rows:
        if r["row_type"] == "section":
            key = (r["chapter"], r["designation"])
            section_meta[key] = {
                "ratio_type": r["ratio_type"],
                "unit":        r["unit"],
            }

    for r in rows:
        if r["row_type"] == "section":
            counts["section_skipped"] += 1
            continue  # sections = métadonnées uniquement, pas de ligne DB

        if r["row_type"] == "article":
            order_global += 1
            lot = _lot_from_chapter(r["chapter"])

            conn.execute(
                """
                INSERT INTO dpgf_articles
                    (code, designation, unit, chapter, section, row_order,
                     excel_row_num, is_virtual, is_custom, row_type,
                     ratio_type, ratio_type_source, pu_ht_ref, qty_ref,
                     last_updated, is_hidden, lot)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 'article', ?, 'auto_unit', ?, ?, ?, 0, ?)
                """,
                (
                    r.get("code"),
                    r["designation"],
                    r["unit"],
                    r["chapter"],
                    r["section"],
                    order_global,
                    r["excel_row"],
                    r["ratio_type"] or "UNITAIRE",
                    r["pu_ht_ref"],
                    r["qty_ref"] or 0,
                    ref_date,
                    lot,
                ),
            )
            counts["article"] += 1

    return counts


# ─── Date Pivot ───────────────────────────────────────────────────────────────

def get_effective_date(conn: sqlite3.Connection, article_id: int) -> str | None:
    """Retourne last_updated de l'article, ou date pivot section/chapitre.

    Logique :
      1. Article propre non NULL → retourne sa date
      2. MAX(last_updated) parmi la section → date pivot section
      3. MAX(last_updated) parmi le chapitre → date pivot chapitre
      4. None
    """
    row = conn.execute(
        "SELECT chapter, section, last_updated FROM dpgf_articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        return None

    if row[2]:
        return row[2]

    chapter, section = row[0], row[1]

    if section:
        pivot = conn.execute(
            """
            SELECT MAX(last_updated)
            FROM dpgf_articles
            WHERE chapter = ? AND section = ? AND last_updated IS NOT NULL
            """,
            (chapter, section),
        ).fetchone()
        if pivot and pivot[0]:
            return pivot[0]

    pivot = conn.execute(
        """
        SELECT MAX(last_updated)
        FROM dpgf_articles
        WHERE chapter = ? AND last_updated IS NOT NULL
        """,
        (chapter,),
    ).fetchone()
    if pivot and pivot[0]:
        return pivot[0]

    return None


# ─── Point d'entrée ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Import référentiel DPGF → dpgf_articles")
    parser.add_argument(
        "--file", default=None,
        help=f"Fichier Excel source (défaut : {_TARGET_FILE} ou {_DEFAULT_FILE})"
    )
    parser.add_argument("--yes", "-y", action="store_true", help="Pas de confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Analyse sans écrire")
    parser.add_argument("--date", default=None, help="Date last_updated (YYYY-MM-DD)")
    args = parser.parse_args()

    # ── Résolution du fichier source ─────────────────────────────────────────
    if args.file:
        filepath = args.file if os.path.isabs(args.file) else os.path.join(PROJECT_DIR, args.file)
    else:
        # Essaie d'abord le fichier cible nommé par Eric
        target = os.path.join(PROJECT_DIR, _TARGET_FILE)
        fallback = os.path.join(PROJECT_DIR, _DEFAULT_FILE)
        if os.path.exists(target):
            filepath = target
        elif os.path.exists(fallback):
            print(f"[INFO] Fichier cible '{_TARGET_FILE}' absent — utilisation du fallback.")
            filepath = fallback
        else:
            sys.exit(
                f"Aucun fichier source trouvé.\n"
                f"  Cible  : {_TARGET_FILE}\n"
                f"  Fallback: {_DEFAULT_FILE}\n"
                f"Utiliser --file pour spécifier le chemin."
            )

    if not os.path.exists(filepath):
        sys.exit(f"Fichier introuvable : {filepath}")

    ref_date = args.date or date.today().isoformat()

    print("=" * 60)
    print("  IMPORT RÉFÉRENTIEL DPGF")
    print("=" * 60)
    print(f"  Source     : {os.path.basename(filepath)}")
    print(f"  Date pivot : {ref_date}")
    print(f"  Mode       : {'DRY-RUN' if args.dry_run else 'ÉCRITURE'}")
    print()

    # ── Parsing ──────────────────────────────────────────────────────────────
    print("Lecture du fichier Excel…")
    try:
        rows, stats = _parse_excel(filepath)
    except Exception as exc:
        sys.exit(f"Erreur lecture Excel : {exc}")

    articles = rows  # _parse_excel ne retourne que des articles désormais
    by_lot   = {"CFO": 0, "CFA": 0, "PV": 0}
    by_chap  = {}
    variantes = [r for r in articles if r.get("is_variante")]
    for r in articles:
        lot = _lot_from_chapter(r["chapter"])
        by_lot[lot] = by_lot.get(lot, 0) + 1
        by_chap.setdefault(r["chapter"], 0)
        by_chap[r["chapter"]] += 1

    print(f"  Titres/sections (méta)       : {stats['titre']}")
    print(f"  Articles Nature='Article'    : {stats['article_tagged']}")
    print(f"  Articles .VARIANTE normalisés: {stats['variante']}")
    print(f"  Articles non-taggés (filtrés): {stats['untagged']}")
    print(f"  Lignes exclues (sous-tot/TVA): {stats['excluded']}")
    print(f"  Lignes vides                 : {stats['empty']}")
    print(f"  ─────────────────────────────")
    print(f"  TOTAL à importer             : {len(articles)}")
    print()
    print("  Répartition par chapitre :")
    for ch, n in by_chap.items():
        lot = _lot_from_chapter(ch)
        print(f"    [{lot}] {ch} : {n} articles")
    print()
    print("  Répartition par LOT :")
    for lot, n in by_lot.items():
        print(f"    {lot} : {n}")
    print()

    if not articles:
        sys.exit("Aucun article à importer. Vérifier la structure du fichier.")

    if args.dry_run:
        # Vérification lignes 37-40 (R037-R040 Excel)
        target_rows = [r for r in articles if r["excel_row"] in (37, 38, 39, 40)]
        if target_rows:
            print("Vérification R037-R040 (Postes de transformation) :")
            for r in target_rows:
                flag = ".VARIANTE normalisé" if r.get("is_variante") else "Article taggé"
                print(f"  R{r['excel_row']:03d} [{flag}] {r['designation'][:55]} | pu={r['pu_ht_ref']}")
        print()
        print("DRY-RUN — rien n'a été écrit en base.")
        return

    # ── Confirmation ─────────────────────────────────────────────────────────
    if not args.yes:
        print("Cette opération va :")
        print("  1. Vider affaires + affaire_lines (reset sécurisé)")
        print("  2. Supprimer tous les articles PSA master (is_custom=0)")
        print("  3. Insérer les nouvelles lignes depuis le fichier source")
        print()
        rep = input("Confirmer ? (oui / non) : ").strip().lower()
        if rep not in ("oui", "o", "yes", "y"):
            print("Annulé.")
            sys.exit(1)

    # ── Écriture ─────────────────────────────────────────────────────────────
    conn = models.get_db()
    try:
        _ensure_last_updated_column(conn)
        _ensure_synonyms_table(conn)

        # Reset affaires + affaire_lines (sécurité intégrée — building_categories intact)
        print("Reset affaires / affaire_lines…")
        conn.execute("PRAGMA foreign_keys = OFF")
        for tbl in ("affaire_lines", "affaire_chapter_settings",
                    "ratio_overrides", "affaires",
                    "bibliotheque_section_ratios"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                if n:
                    conn.execute(f"DELETE FROM {tbl}")
                    conn.execute("DELETE FROM sqlite_sequence WHERE name=?", (tbl,))
                    print(f"  {tbl:<35} {n} lignes supprimées")
            except Exception:
                pass
        conn.execute("PRAGMA foreign_keys = ON")

        print("Suppression articles PSA existants…")
        n_deleted = _clear_referentiel_psa(conn)
        print(f"  {n_deleted} articles supprimés.")

        print("Insertion des nouvelles lignes…")
        counts = _insert_rows(conn, articles, ref_date)
        conn.commit()

        print(f"  Articles insérés : {counts['article']}")
        print()

        # ── Rapport de conformité ─────────────────────────────────────────────
        print("=" * 50)
        print("  RAPPORT DE CONFORMITÉ")
        print("=" * 50)
        for lot in ("CFO", "CFA", "PV"):
            n = conn.execute(
                "SELECT COUNT(*) FROM dpgf_articles "
                "WHERE COALESCE(is_custom,0)=0 AND lot=?", (lot,)
            ).fetchone()[0]
            print(f"  LOT {lot} : {n} articles")
        total_db = conn.execute(
            "SELECT COUNT(*) FROM dpgf_articles WHERE COALESCE(is_custom,0)=0"
        ).fetchone()[0]
        print(f"  TOTAL PSA : {total_db}")
        print()

        # Vérification R037-R040
        print("Vérification lignes R037–R040 (Postes de transformation) :")
        check = conn.execute(
            """SELECT excel_row_num, designation, ratio_type, lot, pu_ht_ref, last_updated
               FROM dpgf_articles
               WHERE excel_row_num IN (37,38,39,40) AND COALESCE(is_custom,0)=0
               ORDER BY excel_row_num"""
        ).fetchall()
        if check:
            for r in check:
                print(f"  R{r[0]:03d} | lot={r[3]} | ratio={r[2]} | pu={r[4]} | {r[1][:50]}")
        else:
            print("  (aucune ligne R037-R040 trouvée en base)")

        print()
        print("Import terminé avec succès.")

    except Exception as exc:
        conn.rollback()
        print(f"\nERREUR — rollback : {exc}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    main()
