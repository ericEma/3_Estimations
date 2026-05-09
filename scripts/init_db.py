"""
init_db.py — Réinitialisation sélective de la base estimation_elec.db

Vide :  affaires, affaire_lines, affaire_chapter_settings,
        ratio_overrides, devis_lines, projects,
        bibliotheque_section_ratios

Préserve (INTOUCHABLES) :
        building_categories  — 15 types de bâtiments
        dpgf_articles        — référentiel PSA (géré par import_referentiel.py)
        mapping_synonyms     — synonymes de mapping devis
        synonyms             — table de correspondance termes
        mapping_knowledge    — base de connaissances fuzzy

Usage :
    python scripts/init_db.py          # mode interactif (demande confirmation)
    python scripts/init_db.py --yes    # mode non-interactif (CI / enchaînement)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import models  # noqa: E402  (path patché ci-dessus)

_TABLES_TO_CLEAR = [
    # Ordre FK-safe : dépendants d'abord
    "affaire_lines",
    "affaire_chapter_settings",
    "ratio_overrides",
    "affaires",
    "devis_lines",
    "projects",
    "bibliotheque_section_ratios",
]

_TABLES_PROTECTED = [
    "building_categories",
    "dpgf_articles",
    "mapping_synonyms",
    "synonyms",
    "mapping_knowledge",
]


def init_db(yes: bool = False) -> bool:
    """Vide les tables opérationnelles. Retourne True si exécuté."""
    if not yes:
        print("=" * 60)
        print("  RÉINITIALISATION BASE — Estimation Élec")
        print("=" * 60)
        print()
        print("Tables qui seront VIDÉES :")
        for t in _TABLES_TO_CLEAR:
            print(f"  ✗  {t}")
        print()
        print("Tables PRÉSERVÉES :")
        for t in _TABLES_PROTECTED:
            print(f"  ✓  {t}")
        print()
        rep = input("Confirmer ? (oui / non) : ").strip().lower()
        if rep not in ("oui", "o", "yes", "y"):
            print("Annulé.")
            return False

    conn = models.get_db()
    try:
        conn.execute("PRAGMA foreign_keys = OFF")

        for tbl in _TABLES_TO_CLEAR:
            # La table peut ne pas exister si la migration n'a pas encore tourné
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                conn.execute(f"DELETE FROM {tbl}")
                print(f"  {tbl:<35} {n:>6} lignes supprimées")
            except Exception as exc:
                print(f"  {tbl:<35} IGNORÉE ({exc})")

        # Remet les séquences AUTOINCREMENT à zéro
        for tbl in _TABLES_TO_CLEAR:
            conn.execute(
                "DELETE FROM sqlite_sequence WHERE name = ?", (tbl,)
            )

        conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()
        print()
        print("Base réinitialisée avec succès.")
        return True

    except Exception as exc:
        conn.rollback()
        print(f"\nERREUR : {exc}")
        raise

    finally:
        conn.close()


if __name__ == "__main__":
    auto = "--yes" in sys.argv or "-y" in sys.argv
    ok = init_db(yes=auto)
    sys.exit(0 if ok else 1)
