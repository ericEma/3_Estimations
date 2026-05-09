"""
import_referentiel.py — Importation du fichier DPGF/Estimation Master dans dpgf_articles.

Source cible   : 2_ Estimation_Modèle_ 09-05-2026.xlsx  (argument --file)
Défaut fallback: 2_ DPGF_Modèle_ 10-04-2026.xlsx

Structure attendue du fichier Excel :
  Ligne d'en-tête contenant 'Art.' en colonne A.
  Colonnes : Art. | Type ratio | Nature | DESIGNATION | U | Q MOE | Q entreprise | PU € HT | Montant € HT

  Ligne chapitre : col A non vide, col B et C vides.
  Ligne Titre    : col C == 'Titre'  → devient une section
  Ligne Article  : col C == 'Article'
  Ligne Sous-Total : col D commence par 'Sous-Total' → ignorée
  Ligne vide     : toutes les colonnes utiles None → ignorée

Usage :
    python scripts/import_referentiel.py
    python scripts/import_referentiel.py --file "2_ Estimation_Modèle_ 09-05-2026.xlsx"
    python scripts/import_referentiel.py --file mon_fichier.xlsx --yes
    python scripts/import_referentiel.py --dry-run   # prévisualise sans écrire

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
_TARGET_FILE  = "2_ Estimation_Modèle_ 09-05-2026.xlsx"

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

def _parse_excel(filepath: str) -> list[dict]:
    """
    Retourne une liste de dicts représentant les lignes utiles du fichier.
    Chaque dict a les clés :
        row_type      'chapter' | 'section' | 'article'
        chapter       str
        section       str | None
        designation   str
        ratio_type    'SURFACIQUE' | 'UNITAIRE' | None
        unit          str | None
        pu_ht_ref     float | None
        qty_ref       float | None   (Q entreprise)
        excel_row     int
    """
    wb = openpyxl.load_workbook(filepath, data_only=True, read_only=True)
    ws = wb.active

    rows_raw = list(ws.iter_rows(values_only=True))
    wb.close()

    result = []

    # Trouver la ligne d'en-tête (colonne A == 'Art.')
    header_idx = None
    for i, row in enumerate(rows_raw):
        if row and str(row[0] or "").strip().lower() == "art.":
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            "Impossible de trouver la ligne d'en-tête (colonne 'Art.'). "
            "Vérifier le fichier source."
        )

    # Map colonnes par nom d'en-tête
    header = rows_raw[header_idx]
    col = {}
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
        elif "q moe" in hn or "q moe" in hn:
            col["qty_moe"] = j
        elif "pu" in hn and "ht" in hn:
            col["pu_ht"] = j

    # Colonnes obligatoires minimum
    for req in ("art", "nature", "designation"):
        if req not in col:
            raise ValueError(f"Colonne requise introuvable : '{req}'. En-tête : {header}")

    current_chapter = None
    current_section = None
    chapter_row_order = 0
    section_row_order = 0
    article_row_order = 0

    for i, raw in enumerate(rows_raw[header_idx + 1 :], start=header_idx + 2):
        art_val   = raw[col["art"]]         if len(raw) > col.get("art", 0)         else None
        ratio_val = raw[col["ratio_type"]]  if len(raw) > col.get("ratio_type", 0)  else None
        nature_v  = raw[col["nature"]]      if len(raw) > col.get("nature", 0)      else None
        desig_v   = raw[col["designation"]] if len(raw) > col.get("designation", 0) else None
        unit_v    = raw[col.get("unit", 99)] if len(raw) > col.get("unit", 99)      else None
        pu_v      = raw[col["pu_ht"]]       if "pu_ht" in col and len(raw) > col["pu_ht"] else None
        qty_v     = raw[col["qty_ref"]]     if "qty_ref" in col and len(raw) > col["qty_ref"] else None

        art_str   = str(art_val).strip()   if art_val   is not None else ""
        desig_str = str(desig_v).strip()   if desig_v   is not None else ""
        nature_str= str(nature_v).strip()  if nature_v  is not None else ""

        # ── Lignes vides ─────────────────────────────────────────────────────
        if not art_str and not desig_str and not nature_str:
            continue

        # ── Sous-Total → ignorer ─────────────────────────────────────────────
        if desig_str.lower().startswith("sous-total"):
            continue

        # ── Ligne chapitre ───────────────────────────────────────────────────
        # Col A non vide, col B (ratio) et col C (nature) vides → chapitre
        if art_str and not ratio_val and not nature_v and desig_str == "":
            # Le libellé chapitre est en col A
            chapter_candidate = art_str
            if chapter_candidate in _KNOWN_CHAPTERS:
                current_chapter = chapter_candidate
                current_section = None
                chapter_row_order += 1
                section_row_order = 0
                article_row_order = 0
            # Sinon : ligne de titre / commentaire → ignorer
            continue

        # ── Section (Titre) ──────────────────────────────────────────────────
        if nature_str == "Titre" and current_chapter:
            current_section = desig_str
            section_row_order += 1
            article_row_order = 0
            ratio_norm = _normalize_ratio_type(str(ratio_val or ""))
            result.append({
                "row_type":   "section",
                "chapter":    current_chapter,
                "section":    current_section,
                "designation": desig_str,
                "ratio_type": ratio_norm,
                "unit":       str(unit_v).strip() if unit_v else None,
                "pu_ht_ref":  None,
                "qty_ref":    None,
                "excel_row":  i,
                "row_order":  section_row_order,
            })
            continue

        # ── Article ──────────────────────────────────────────────────────────
        if nature_str == "Article" and current_chapter:
            if not desig_str:
                continue  # désignation vide → ignorer

            article_row_order += 1
            ratio_norm = _normalize_ratio_type(str(ratio_val or ""))

            # PU : float ou None
            pu_float = None
            if pu_v is not None:
                try:
                    pu_float = float(pu_v)
                    if pu_float <= 0:
                        pu_float = None
                except (ValueError, TypeError):
                    pu_float = None

            # Qty : float ou None
            qty_float = None
            if qty_v is not None:
                try:
                    qty_float = float(qty_v)
                    if qty_float < 0:
                        qty_float = None
                except (ValueError, TypeError):
                    qty_float = None

            result.append({
                "row_type":    "article",
                "chapter":     current_chapter,
                "section":     current_section,
                "designation": desig_str,
                "ratio_type":  ratio_norm,
                "unit":        str(unit_v).strip() if unit_v else None,
                "pu_ht_ref":   pu_float,
                "qty_ref":     qty_float,
                "excel_row":   i,
                "row_order":   article_row_order,
                "code":        art_str if art_str else None,
            })
            continue

    return result


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
        rows = _parse_excel(filepath)
    except Exception as exc:
        sys.exit(f"Erreur lecture Excel : {exc}")

    sections  = [r for r in rows if r["row_type"] == "section"]
    articles  = [r for r in rows if r["row_type"] == "article"]
    by_chap   = {}
    for r in articles:
        by_chap.setdefault(r["chapter"], 0)
        by_chap[r["chapter"]] += 1

    print(f"  Sections  : {len(sections)}")
    print(f"  Articles  : {len(articles)}")
    for ch, n in by_chap.items():
        print(f"    {ch} : {n} articles")
    print()

    if not rows:
        sys.exit("Aucune ligne utile trouvée dans le fichier. Vérifier la structure.")

    if args.dry_run:
        print("DRY-RUN — rien n'a été écrit en base.")
        return

    # ── Confirmation ─────────────────────────────────────────────────────────
    if not args.yes:
        print("Cette opération va :")
        print("  1. Supprimer tous les articles PSA master (is_custom=0) existants")
        print("     et leurs dépendances (ratio_overrides, mapping_synonyms, affaire_lines PSA)")
        print("  2. Insérer les nouvelles lignes")
        print("  3. Ajouter/vérifier colonne last_updated + table synonyms")
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

        print("Suppression des articles PSA existants…")
        n_deleted = _clear_referentiel_psa(conn)
        print(f"  {n_deleted} articles supprimés.")

        print("Insertion des nouvelles lignes…")
        counts = _insert_rows(conn, rows, ref_date)
        conn.commit()

        print(f"  Sections (méta, non insérées) : {counts['section_skipped']}")
        print(f"  Articles insérés              : {counts['article']}")
        print()

        # Vérification rapide
        total_db = conn.execute(
            "SELECT COUNT(*) FROM dpgf_articles WHERE COALESCE(is_custom, 0) = 0"
        ).fetchone()[0]
        print(f"Total PSA en base   : {total_db}")
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
