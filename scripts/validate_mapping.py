"""
validate_mapping.py - Interface de validation manuelle du mapping DPGF
Sprint 2

Affiche les lignes devis_lines en statut 'pending' (score fuzzy < 80%)
et permet a l'utilisateur de selectionner le bon article DPGF.

Usage :
  python scripts/validate_mapping.py
  python scripts/validate_mapping.py --project-id 1
  python scripts/validate_mapping.py --project-id 1 --top 10

Options :
  --project-id N   ID du projet a valider (defaut : dernier projet importe)
  --top N          Nombre de candidats DPGF affiches (defaut : 5)

Commandes interactives :
  [1..N]     Selectionner un candidat de la liste
  [l NNN]    Selectionner par numero de ligne Excel du DPGF (ex: l 145)
             Accepte aussi les articles virtuels (ex: l 145.1)
  [a]        Ajouter la designation courante comme nouvel article dans le DPGF
             (herite chapitre/section/unite d'un article de reference)
  [s]        Passer cette ligne (skip)
  [u]        Marquer comme non mappable (unmapped)
  [q]        Quitter la session
"""
import re
import sys
import io
import sqlite3
import argparse
from pathlib import Path
from loguru import logger
from rapidfuzz import process, fuzz

from scripts.scoring import score_candidate, unit_penalty_label, unit_family
import scripts.mapping_knowledge as mk

# Fix Windows console encoding (cp1252 -> utf-8)
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))
from init_db import init_database

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ══════════════════════════════════════════════════════════════
# Helpers — lecture BDD
# ══════════════════════════════════════════════════════════════

def get_last_project_id(conn: sqlite3.Connection) -> int | None:
    """Retourne l'id du dernier projet importe."""
    row = conn.execute("SELECT id, name FROM projects ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        logger.info(f"Projet selectionne : id={row[0]} | {row[1]}")
        return row[0]
    return None


def count_pending(conn: sqlite3.Connection, project_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM devis_lines WHERE project_id = ? AND mapping_status = 'pending'",
        (project_id,)
    ).fetchone()[0]


def fetch_pending_lines(conn: sqlite3.Connection, project_id: int) -> list:
    return conn.execute(
        """
        SELECT id, original_designation, unit, quantity, unit_price_ht,
               mapping_score, mapping_candidate,
               excel_row_num, context_path
        FROM devis_lines
        WHERE project_id = ? AND mapping_status = 'pending'
        ORDER BY mapping_score ASC
        """,
        (project_id,)
    ).fetchall()


def fetch_dpgf_candidates(
    conn: sqlite3.Connection,
    designation: str,
    top: int,
    devis_unit: str | None = None,
) -> list:
    """
    Retourne les N meilleurs articles DPGF (fuzzy match unit-aware) pour une designation.
    Si un mapping appris existe pour (designation, unit), il est retourné en premier
    avec score=100.0 et source='knowledge'.

    Scoring :
      1. Score textuel brut via fuzz.WRatio
      2. × facteur de pénalité unité (0.5 si familles différentes, 1.0 sinon)
    Les candidats sont classés par score_final décroissant.
    """
    # ── Lookup connaissance mémorisée (priorité absolue) ─────────
    knowledge_hit = mk.lookup(conn, designation, devis_unit)

    all_rows = conn.execute(
        "SELECT id, designation, unit, chapter, section, excel_row_num, excel_row_label, is_virtual "
        "FROM dpgf_articles WHERE row_type = 'article'"
    ).fetchall()

    # Pré-filtrage par famille d'unité : on remonte les articles de même famille
    # en priorité (mais on garde TOUS les articles dans le top pour ne rien masquer)
    devis_family = unit_family(devis_unit)

    scored = []
    for row in all_rows:
        dpgf_desig = row[1]
        dpgf_unit  = row[2]
        final, text, pf = score_candidate(designation, devis_unit, dpgf_desig, dpgf_unit)
        scored.append((final, text, pf, row))

    # Tri par score final décroissant, puis score textuel comme départage
    scored.sort(key=lambda x: (-x[0], -x[1]))

    candidates = []
    for final, text, pf, row in scored[:top]:
        penalty_lbl = unit_penalty_label(devis_unit, row[2])
        candidates.append({
            "id":              row[0],
            "designation":     row[1],
            "unit":            row[2],
            "chapter":         row[3],
            "section":         row[4],
            "excel_row_num":   row[5],
            "excel_row_label": row[6] or (str(row[5]) if row[5] else "?"),
            "is_virtual":      bool(row[7]),
            "score":           final,
            "score_text":      text,
            "unit_penalty":    pf,
            "penalty_label":   penalty_lbl,
            "source":          "fuzzy",
        })

    # Injecter la connaissance apprise en tête si trouvée (et pas déjà en [1])
    if knowledge_hit:
        kh = {
            "id":              knowledge_hit["id"],
            "designation":     knowledge_hit["designation"],
            "unit":            knowledge_hit["unit"],
            "chapter":         knowledge_hit["chapter"],
            "section":         knowledge_hit["section"],
            "excel_row_num":   knowledge_hit["excel_row_num"],
            "excel_row_label": knowledge_hit["excel_row_label"],
            "is_virtual":      knowledge_hit["is_virtual"],
            "score":           100.0,
            "score_text":      100.0,
            "unit_penalty":    1.0,
            "penalty_label":   "",
            "source":          "knowledge",
            "occurrence_count": knowledge_hit.get("occurrence_count", 1),
        }
        # Supprimer le doublon éventuel dans la liste fuzzy (même id)
        candidates = [c for c in candidates if c["id"] != kh["id"]]
        candidates.insert(0, kh)

    return candidates


def find_article_by_ref(conn: sqlite3.Connection, ref: str) -> dict | None:
    """
    Cherche un article DPGF par son repere de ligne Excel.

    - ref entier  (ex: '145')   -> recherche par excel_row_num = 145
    - ref decimal (ex: '145.1') -> recherche par excel_row_label = '145.1' (article virtuel)

    Retourne None si aucun article trouve.
    """
    if not re.match(r'^\d+(\.\d+)?$', ref):
        return None

    if "." in ref:
        # Article virtuel : recherche par label
        row = conn.execute(
            "SELECT id, designation, unit, chapter, section, excel_row_num, excel_row_label, is_virtual "
            "FROM dpgf_articles WHERE excel_row_label = ? AND row_type = 'article'",
            (ref,)
        ).fetchone()
    else:
        # Article reel : recherche par numero entier
        row = conn.execute(
            "SELECT id, designation, unit, chapter, section, excel_row_num, excel_row_label, is_virtual "
            "FROM dpgf_articles WHERE excel_row_num = ? AND row_type = 'article'",
            (int(ref),)
        ).fetchone()

    if not row:
        return None
    return {
        "id":              row[0],
        "designation":     row[1],
        "unit":            row[2],
        "chapter":         row[3],
        "section":         row[4],
        "excel_row_num":   row[5],
        "excel_row_label": row[6] or str(row[5]),
        "is_virtual":      bool(row[7]),
        "score":           100.0,   # selection directe = score parfait
    }


# ══════════════════════════════════════════════════════════════
# Helpers — enrichissement du referentiel DPGF
# ══════════════════════════════════════════════════════════════

def _get_next_virtual_label(conn: sqlite3.Connection, parent_row_num: int) -> str:
    """
    Calcule le prochain label virtuel disponible pour un parent donne.

    Exemple :
        parent_row_num = 134
        Si '134.1' existe deja -> retourne '134.2'
        Si rien n'existe       -> retourne '134.1'
    """
    prefix = f"{parent_row_num}."
    rows = conn.execute(
        "SELECT excel_row_label FROM dpgf_articles WHERE excel_row_label LIKE ?",
        (f"{prefix}%",)
    ).fetchall()

    existing_suffixes = []
    for r in rows:
        label = r[0] or ""
        if label.startswith(prefix):
            tail = label[len(prefix):]
            if tail.isdigit():
                existing_suffixes.append(int(tail))

    next_n = max(existing_suffixes, default=0) + 1
    return f"{parent_row_num}.{next_n}"


def _add_to_dpgf(
    conn: sqlite3.Connection,
    designation: str,
    parent_row_num: int,
) -> dict | None:
    """
    Cree un nouvel article DPGF virtuel en heritant des proprietes d'un article parent.

    Regles :
      - Designation = designation exacte de la ligne devis courante
      - Chapitre, Section, Unite, ratio_type = copies depuis l'article parent
      - excel_row_num = NULL  (pas de ligne Excel reelle)
      - excel_row_label = '{parent_row_num}.N'  (ex: '134.1')
      - is_virtual = 1

    Retourne le dict article cree (pret pour _apply_mapping), ou None si parent introuvable.
    """
    parent = conn.execute(
        """
        SELECT id, chapter, chapter_num, section, unit,
               ratio_type, ratio_type_source
        FROM dpgf_articles
        WHERE excel_row_num = ? AND row_type = 'article'
        """,
        (parent_row_num,)
    ).fetchone()

    if not parent:
        return None

    virtual_label = _get_next_virtual_label(conn, parent_row_num)

    # row_order : on place l'article virtuel apres tous les existants
    max_order = conn.execute(
        "SELECT COALESCE(MAX(row_order), 0) FROM dpgf_articles"
    ).fetchone()[0]

    cur = conn.execute(
        """
        INSERT INTO dpgf_articles
            (code, designation, unit, chapter, chapter_num, section,
             row_order, excel_row_num, excel_row_label,
             row_type, ratio_type, ratio_type_source, is_virtual, is_custom, version_model)
        VALUES (NULL, ?, ?, ?, ?, ?,
                ?, NULL, ?,
                'article', ?, ?, 1, 1, '1.0')
        """,
        (
            designation,
            parent[4],      # unit
            parent[1],      # chapter
            parent[2],      # chapter_num
            parent[3],      # section
            max_order + 1,
            virtual_label,
            parent[5],      # ratio_type
            parent[6],      # ratio_type_source
        ),
    )
    conn.commit()
    new_id = cur.lastrowid

    logger.success(
        f"  Nouvel article DPGF cree : id={new_id} | [{virtual_label}] "
        f"| {designation[:55]} | unite={parent[4] or '?'}"
    )
    logger.info(
        f"  Herite de L.{parent_row_num} -> chapitre={parent[1]} | section={parent[3] or '(aucune)'}"
    )

    return {
        "id":              new_id,
        "designation":     designation,
        "unit":            parent[4],
        "chapter":         parent[1],
        "section":         parent[3],
        "excel_row_num":   None,
        "excel_row_label": virtual_label,
        "is_virtual":      True,
        "score":           100.0,
    }


# ══════════════════════════════════════════════════════════════
# Interface de validation
# ══════════════════════════════════════════════════════════════

def _apply_mapping(conn: sqlite3.Connection, line_id: int, article: dict):
    """Applique le mapping manuel sur une devis_line."""
    conn.execute(
        """
        UPDATE devis_lines
        SET mapping_status    = 'manual',
            dpgf_article_id   = ?,
            mapping_score     = ?,
            mapping_candidate = ?
        WHERE id = ?
        """,
        (article["id"], article["score"], article["designation"], line_id)
    )
    conn.commit()


def _cmd_add_to_dpgf(
    conn: sqlite3.Connection,
    line_id: int,
    designation: str,
    unit: str | None = None,
    mk_counts: dict | None = None,
) -> str | None:
    """
    Gere la commande [a] : ajoute la designation comme nouvel article DPGF virtuel.

    Demande a l'utilisateur la ligne de reference, affiche un apercu, confirme, puis
    cree l'article et mappe la ligne.

    Retourne 'validated' si OK, None si abandon.
    """
    print()
    print("  --- Ajout au referentiel DPGF ---")
    print(f"  Designation a creer : \"{designation}\"")
    print()
    print("  Sous quel numero de ligne du modele Excel faut-il inserer cet article ?")
    print("  (Indiquez la ligne d'un article existant pour en copier chapitre/section/unite)")

    while True:
        ref_input = input("  Ligne de reference (entier) ou [annuler] : ").strip().lower()
        if ref_input in ("annuler", "a", ""):
            print("  Ajout annule.")
            return None

        if not ref_input.isdigit():
            print("  Saisissez un numero entier valide.")
            continue

        parent_row = int(ref_input)

        # Apercu du parent
        parent = conn.execute(
            "SELECT designation, chapter, section, unit, excel_row_label "
            "FROM dpgf_articles WHERE excel_row_num = ? AND row_type = 'article'",
            (parent_row,)
        ).fetchone()

        if not parent:
            print(f"  Aucun article DPGF a la ligne {parent_row}.")
            print("  Verifiez le numero (doit etre un article avec unite dans le DPGF).")
            continue

        # Calcul du label virtuel qui sera attribue
        next_label = _get_next_virtual_label(conn, parent_row)

        print()
        print(f"  Reference parent  : L.{parent[4] or parent_row} | {parent[0]}")
        print(f"  Chapitre          : {parent[1]}")
        print(f"  Section           : {parent[2] or '(aucune)'}")
        print(f"  Unite copiee      : {parent[3] or '?'}")
        print(f"  Nouvel article    : [{next_label}] {designation}")
        print()

        confirm = input("  Confirmer la creation ? (o/n) : ").strip().lower()
        if confirm != "o":
            print("  Ajout annule.")
            return None

        new_article = _add_to_dpgf(conn, designation, parent_row)
        if new_article is None:
            print(f"  Erreur inattendue : creation impossible.")
            return None

        _apply_mapping(conn, line_id, new_article)
        rec = mk.record(conn, designation, unit, new_article["id"])
        if mk_counts is not None:
            mk_counts[rec] = mk_counts.get(rec, 0) + 1
        logger.success(
            f"  Line {line_id} -> nouveau DPGF [{new_article['excel_row_label']}] "
            f"| {designation[:55]}"
        )
        return "validated"


def validate_line(conn: sqlite3.Connection, line: tuple, top: int, mk_counts: dict) -> str:
    """
    Presente une ligne pending a l'utilisateur et applique sa decision.

    Returns :
        'validated'   -> mapping_status = 'manual'
        'unmapped'    -> mapping_status = 'unmapped'
        'skip'        -> ligne ignoree (repassee plus tard)
        'quit'        -> arret de la session
    """
    line_id, designation, unit, qty, pu, score, candidate, devis_excel_row, ctx_path = line

    print("\n" + "-" * 70)
    # Contexte de localisation dans le devis
    if ctx_path:
        print(f"  Localisation  : {ctx_path}")
    if devis_excel_row:
        print(f"  Ligne Excel   : {devis_excel_row}  (devis PSA)")
    print(f"  Designation   : {designation}")
    print(f"  Unite / Qte   : {unit or '?'} / {qty or '?'}")
    print(f"  PU HT         : {f'{pu:,.2f} EUR' if pu else '?'}")
    print(f"  Meilleur match: {candidate or '(aucun)'} (score {score:.0f}%)")
    print()

    # Candidats DPGF fuzzy + connaissance (unit-aware)
    candidates = fetch_dpgf_candidates(conn, designation or "", top, devis_unit=unit)

    # Bannière "CONNU" si le premier candidat est une connaissance mémorisée
    if candidates and candidates[0].get("source") == "knowledge":
        kc = candidates[0]
        occ = kc.get("occurrence_count", 1)
        print(f"  ╔══════════════════════════════════════════════════════════╗")
        print(f"  ║  ★  CORRESPONDANCE APPRISE  ({occ}x validee)             ║")
        print(f"  ╚══════════════════════════════════════════════════════════╝")

    print(f"  Top {top} candidats DPGF  [*] = article virtuel  [!] = pénalité unité :")
    for i, c in enumerate(candidates, 1):
        label    = c["excel_row_label"]
        virtual  = " *" if c["is_virtual"] else "  "
        row_tag  = f"L.{label:<6}"
        penalty  = c.get("penalty_label", "")
        source_tag = " [Appris]" if c.get("source") == "knowledge" else ""
        # Afficher score_text si pénalité appliquée (pour transparence)
        if c.get("unit_penalty", 1.0) < 1.0:
            score_str = f"{c['score']:>5.1f}% ({c['score_text']:.0f}%×0.5)"
        else:
            score_str = f"{c['score']:>5.1f}%"
        print(
            f"  [{i}] {score_str:<20}  {row_tag}{virtual}  "
            f"{c['designation'][:42]:<42}  | {c['unit'] or '?':>4} |{source_tag} {penalty}"
        )

    print()
    print("  [1..N] candidat  |  [l NNN] ligne DPGF directe (ex: l 145 ou l 145.1)")
    print("  [a] ajouter au modele  |  [s] skip  |  [u] unmapped  |  [q] quitter")

    while True:
        choice = input("  Choix : ").strip()
        choice_lower = choice.lower()

        if choice_lower == "q":
            return "quit"

        if choice_lower == "s":
            return "skip"

        if choice_lower == "u":
            conn.execute(
                "UPDATE devis_lines SET mapping_status = 'unmapped' WHERE id = ?",
                (line_id,)
            )
            conn.commit()
            logger.info(f"  Line {line_id} -> unmapped")
            return "unmapped"

        # ── Commande [a] : ajout au referentiel ─────────────────
        if choice_lower == "a":
            result = _cmd_add_to_dpgf(conn, line_id, designation or "", unit=unit, mk_counts=mk_counts)
            if result:
                return result
            # Si abandon : reafficher les commandes et boucler
            print()
            print("  [1..N] candidat  |  [l NNN] ligne DPGF directe  |  [a] ajouter  |  [s] [u] [q]")
            continue

        # ── Commande [l NNN] : selection directe par ligne Excel ─
        parts = choice_lower.split()
        if len(parts) == 2 and parts[0] == "l":
            ref = parts[1]
            if not re.match(r'^\d+(\.\d+)?$', ref):
                print("  Format invalide. Exemples : l 145  ou  l 145.1")
                continue
            selected = find_article_by_ref(conn, ref)
            if selected is None:
                print(f"  Aucun article DPGF trouve a la reference L.{ref}.")
                if "." not in ref:
                    print("  (Verifiez que c'est une ligne article avec unite dans le DPGF.)")
                else:
                    print("  (Les articles virtuels sont crees via la commande [a].)")
                continue
            _apply_mapping(conn, line_id, selected)
            rec = mk.record(conn, designation or "", unit, selected["id"])
            mk_counts[rec] = mk_counts.get(rec, 0) + 1
            virt_tag = " [virtuel]" if selected["is_virtual"] else ""
            logger.success(
                f"  Line {line_id} -> manual [L.{ref}]{virt_tag} | {selected['designation'][:55]}"
            )
            return "validated"

        # ── Selection numerique dans la liste ────────────────────
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            selected = candidates[int(choice) - 1]
            _apply_mapping(conn, line_id, selected)
            rec = mk.record(conn, designation or "", unit, selected["id"])
            mk_counts[rec] = mk_counts.get(rec, 0) + 1
            virt_tag = " [virtuel]" if selected["is_virtual"] else ""
            logger.success(
                f"  Line {line_id} -> manual{virt_tag} | {selected['designation'][:60]}"
            )
            return "validated"

        print(f"  Entree non reconnue. Saisissez 1-{len(candidates)}, l NNN, a, s, u ou q.")


# ══════════════════════════════════════════════════════════════
# Session de validation
# ══════════════════════════════════════════════════════════════

def run_validation_session(conn: sqlite3.Connection, project_id: int, top: int):
    """Lance la session interactive de validation."""
    mk.ensure_table(conn)

    n_pending = count_pending(conn, project_id)
    if n_pending == 0:
        logger.success("Aucune ligne pending — mapping complet !")
        return

    logger.info(f"{n_pending} lignes a valider pour le projet id={project_id}")

    lines     = fetch_pending_lines(conn, project_id)
    stats     = {"validated": 0, "unmapped": 0, "skip": 0, "added_to_dpgf": 0}
    mk_counts = {"new": 0, "updated": 0, "unchanged": 0}

    for i, line in enumerate(lines, 1):
        print(f"\n  === Ligne {i}/{len(lines)} ===")
        result = validate_line(conn, line, top, mk_counts)

        if result == "quit":
            logger.info("Session interrompue par l'utilisateur.")
            break

        if result in stats:
            stats[result] += 1

    # Compter les articles virtuels crees dans cette session
    n_virtual = conn.execute(
        "SELECT COUNT(*) FROM dpgf_articles WHERE is_virtual = 1"
    ).fetchone()[0]

    # Recapitulatif
    print("\n" + "=" * 70)
    remaining = count_pending(conn, project_id)
    print("  Session terminee :")
    print(f"    Validees  (manual)        : {stats['validated']}")
    print(f"    Non mappees               : {stats['unmapped']}")
    print(f"    Passees (skip)            : {stats['skip']}")
    print(f"    Articles virtuels en BDD  : {n_virtual}")
    print(f"    Encore pending            : {remaining}")
    print("=" * 70 + "\n")

    if remaining == 0:
        logger.success("Mapping 100% complet — pret pour le Sprint 3.")
    else:
        logger.info(
            f"{remaining} ligne(s) encore pending. "
            "Relancez validate_mapping.py pour continuer."
        )

    mk.session_report(conn, mk_counts)


# ══════════════════════════════════════════════════════════════
# Entree principale
# ══════════════════════════════════════════════════════════════

def setup_logger():
    logger.remove()
    logger.add(
        sys.stdout,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
        colorize=True,
    )
    logger.add(
        LOG_DIR / "validate_mapping.log",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
        rotation="1 MB",
        retention="30 days",
        encoding="utf-8",
    )


if __name__ == "__main__":
    setup_logger()

    parser = argparse.ArgumentParser(description="Validation manuelle du mapping DPGF")
    parser.add_argument("--project-id", type=int, default=None,
                        help="ID du projet (defaut : dernier importe)")
    parser.add_argument("--top",        type=int, default=5,
                        help="Nombre de candidats a afficher (defaut : 5)")
    args = parser.parse_args()

    conn = init_database()
    try:
        project_id = args.project_id or get_last_project_id(conn)
        if project_id is None:
            logger.error("Aucun projet en base. Lancez import_devis.py d'abord.")
            sys.exit(1)

        run_validation_session(conn, project_id, args.top)
    finally:
        conn.close()
