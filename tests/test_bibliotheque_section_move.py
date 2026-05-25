"""Déplacement des sous-chapitres dans la bibliothèque DPGF."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import models  # noqa: E402


class TestBibliothequeSectionMove(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def test_section_move_up_reorders_row_order(self):
        chapter = "Courants Forts"
        conn = models.get_db()
        try:
            before_orders = [
                (r["row_order"], r["id"])
                for r in conn.execute(
                    "SELECT id, row_order FROM dpgf_articles WHERE chapter=?",
                    (chapter,),
                ).fetchall()
            ]
            sections = [
                r["section"]
                for r in conn.execute(
                    """
                    SELECT section, MIN(row_order) AS section_order
                    FROM dpgf_articles
                    WHERE chapter=? AND row_type='article'
                      AND (is_hidden IS NULL OR is_hidden = 0)
                    GROUP BY section
                    ORDER BY section_order, section
                    """,
                    (chapter,),
                ).fetchall()
            ]
        finally:
            conn.close()

        if len(sections) < 2:
            self.skipTest("Pas assez de sections pour tester le déplacement")

        moved = sections[1]
        previous = sections[0]
        try:
            models.save_bibliotheque_save(
                [
                    {
                        "id": None,
                        "field": "section_move",
                        "chapter": chapter,
                        "section": moved,
                        "direction": "up",
                    }
                ]
            )

            conn = models.get_db()
            try:
                after_sections = [
                    r["section"]
                    for r in conn.execute(
                        """
                        SELECT section, MIN(row_order) AS section_order
                        FROM dpgf_articles
                        WHERE chapter=? AND row_type='article'
                          AND (is_hidden IS NULL OR is_hidden = 0)
                        GROUP BY section
                        ORDER BY section_order, section
                        """,
                        (chapter,),
                    ).fetchall()
                ]
            finally:
                conn.close()

            self.assertLess(after_sections.index(moved), after_sections.index(previous))
        finally:
            conn = models.get_db()
            try:
                conn.executemany(
                    "UPDATE dpgf_articles SET row_order=? WHERE id=?",
                    before_orders,
                )
                conn.commit()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
