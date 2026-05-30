"""Tests promotion estimation → base de prix."""

import unittest
import uuid

import models
from estimation_layout import handle_layout_action
from estimation_promote import handle_promote_action


class TestEstimationPromote(unittest.TestCase):
    def setUp(self):
        models.ensure_app_tables()
        self.affaire_id, _ = models.create_affaire(
            {"name": "Promote test", "surface_sdo": 1000, "puissance_pv_kwc": 100}
        )

    def tearDown(self):
        if not getattr(self, "affaire_id", None):
            return
        conn = models.get_db()
        aid = self.affaire_id
        try:
            conn.execute("DELETE FROM affaire_lines WHERE affaire_id=?", (aid,))
            conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id=?", (aid,))
            conn.execute(
                "DELETE FROM affaire_estimation_section_sort WHERE affaire_id=?", (aid,)
            )
            conn.execute("DELETE FROM affaires WHERE id=?", (aid,))
            conn.commit()
        finally:
            conn.close()

    def test_promote_section_creates_bibliotheque_articles(self):
        sec_name = f"Section promo {uuid.uuid4().hex[:8]}"
        out = handle_layout_action(
            self.affaire_id,
            "add_section",
            {
                "chapter": "Courants Forts",
                "section_name": sec_name,
                "after_section": "",
            },
        )
        self.assertEqual(out.get("status"), "ok")

        line_id = out.get("line_id")
        self.assertIsNotNone(line_id)
        conn = models.get_db()
        conn.execute(
            """
            UPDATE affaire_lines SET line_designation='Cable promo', unit_override='u',
                   unit_price_ht=42, ratio_ref=42 WHERE id=?
            """,
            (line_id,),
        )
        conn.commit()
        conn.close()

        promo = handle_promote_action(
            self.affaire_id,
            "promote_section",
            {"chapter": "Courants Forts", "section": sec_name},
        )
        self.assertEqual(promo.get("status"), "ok")
        self.assertEqual(promo.get("articles_promoted"), 1)

        conn = models.get_db()
        art = conn.execute(
            """
            SELECT id, designation FROM dpgf_articles
            WHERE chapter='Courants Forts' AND section=?
              AND COALESCE(is_custom,0)=1
            """,
            (sec_name,),
        ).fetchone()
        self.assertIsNotNone(art)
        self.assertEqual(art["designation"], "Cable promo")

        linked = conn.execute(
            "SELECT dpgf_article_id, line_chapter FROM affaire_lines WHERE id=?",
            (line_id,),
        ).fetchone()
        self.assertEqual(int(linked["dpgf_article_id"]), int(art["id"]))
        self.assertIsNone(linked["line_chapter"])

        st = conn.execute(
            """
            SELECT is_local FROM affaire_chapter_settings
            WHERE affaire_id=? AND chapter_key=?
            """,
            (self.affaire_id, f"sect:Courants Forts|{sec_name}"),
        ).fetchone()
        self.assertEqual(int(st["is_local"]), 0)

        conn.execute("DELETE FROM affaire_lines WHERE affaire_id=?", (self.affaire_id,))
        conn.execute(
            "DELETE FROM ratio_overrides WHERE dpgf_article_id=?", (art["id"],)
        )
        conn.execute(
            """
            DELETE FROM bibliotheque_section_ratios
            WHERE chapter='Courants Forts' AND section=?
            """,
            (sec_name,),
        )
        conn.execute("DELETE FROM dpgf_articles WHERE id=?", (art["id"],))
        conn.execute(
            "DELETE FROM affaire_chapter_settings WHERE affaire_id=?", (self.affaire_id,)
        )
        conn.execute(
            "DELETE FROM affaire_estimation_section_sort WHERE affaire_id=?",
            (self.affaire_id,),
        )
        conn.execute("DELETE FROM affaires WHERE id=?", (self.affaire_id,))
        conn.commit()
        conn.close()
        self.affaire_id = None

    def test_promote_section_rejects_duplicate_bibliotheque(self):
        dup_sec = f"Dup sec {uuid.uuid4().hex[:8]}"
        handle_layout_action(
            self.affaire_id,
            "add_section",
            {
                "chapter": "Courants Forts",
                "section_name": dup_sec,
                "after_section": "",
            },
        )
        conn = models.get_db()
        conn.execute(
            """
            INSERT INTO dpgf_articles
              (designation, unit, chapter, section, row_order, row_type, ratio_type, is_custom)
            VALUES ('Existant', 'u', 'Courants Forts', ?, 1, 'article', 'UNITAIRE', 0)
            """,
            (dup_sec,),
        )
        conn.commit()
        conn.close()

        promo = handle_promote_action(
            self.affaire_id,
            "promote_section",
            {"chapter": "Courants Forts", "section": dup_sec},
        )
        self.assertEqual(promo.get("status"), "error")
        self.assertIn("existe déjà", promo.get("message", "").lower())

        conn = models.get_db()
        conn.execute("DELETE FROM affaire_lines WHERE affaire_id=?", (self.affaire_id,))
        conn.execute(
            "DELETE FROM dpgf_articles WHERE chapter='Courants Forts' AND section=?",
            (dup_sec,),
        )
        conn.commit()
        conn.close()

    def test_promote_section_keeps_estimation_position_in_bibliotheque(self):
        conn = models.get_db()
        try:
            rows = conn.execute(
                """
                SELECT section
                FROM affaire_estimation_section_sort
                WHERE affaire_id=? AND chapter='Courants Forts'
                ORDER BY sort_order, section
                """,
                (self.affaire_id,),
            ).fetchall()
        finally:
            conn.close()
        if len(rows) < 2:
            self.skipTest("Pas assez de sections Courants Forts pour tester l'insertion")

        after_sec = rows[0]["section"]
        next_sec = rows[1]["section"]
        sec_name = f"Ordered promo {uuid.uuid4().hex[:8]}"
        out = handle_layout_action(
            self.affaire_id,
            "add_section",
            {
                "chapter": "Courants Forts",
                "section_name": sec_name,
                "after_section": after_sec,
            },
        )
        self.assertEqual(out.get("status"), "ok")

        line_id = out.get("line_id")
        conn = models.get_db()
        try:
            conn.execute(
                """
                UPDATE affaire_lines SET line_designation='Article ordre promo',
                       unit_override='u', unit_price_ht=12, ratio_ref=12
                WHERE id=?
                """,
                (line_id,),
            )
            conn.commit()
        finally:
            conn.close()

        conn = models.get_db()
        try:
            before_orders = [
                (r["row_order"], r["id"])
                for r in conn.execute(
                    "SELECT id, row_order FROM dpgf_articles WHERE chapter='Courants Forts'"
                ).fetchall()
            ]
        finally:
            conn.close()

        promo = {}
        try:
            promo = handle_promote_action(
                self.affaire_id,
                "promote_section",
                {"chapter": "Courants Forts", "section": sec_name},
            )
            self.assertEqual(promo.get("status"), "ok")

            conn = models.get_db()
            try:
                ordered_sections = [
                    r["section"]
                    for r in conn.execute(
                        """
                        SELECT section, MIN(row_order) AS section_order
                        FROM dpgf_articles
                        WHERE chapter='Courants Forts'
                          AND (is_hidden IS NULL OR is_hidden = 0)
                        GROUP BY section
                        ORDER BY section_order, section
                        """
                    ).fetchall()
                ]
            finally:
                conn.close()
            self.assertIn(after_sec, ordered_sections)
            self.assertIn(sec_name, ordered_sections)
            self.assertIn(next_sec, ordered_sections)
            self.assertLess(ordered_sections.index(after_sec), ordered_sections.index(sec_name))
            self.assertLess(ordered_sections.index(sec_name), ordered_sections.index(next_sec))
        finally:
            conn = models.get_db()
            try:
                conn.execute("DELETE FROM affaire_lines WHERE affaire_id=?", (self.affaire_id,))
                conn.execute(
                    "DELETE FROM ratio_overrides WHERE dpgf_article_id IN ({})".format(
                        ",".join("?" for _ in promo.get("dpgf_ids", [])) or "NULL"
                    ),
                    promo.get("dpgf_ids", []),
                )
                conn.execute(
                    """
                    DELETE FROM bibliotheque_section_ratios
                    WHERE chapter='Courants Forts' AND section=?
                    """,
                    (sec_name,),
                )
                conn.execute(
                    "DELETE FROM dpgf_articles WHERE chapter='Courants Forts' AND section=?",
                    (sec_name,),
                )
                conn.executemany(
                    "UPDATE dpgf_articles SET row_order=? WHERE id=?",
                    before_orders,
                )
                conn.execute(
                    "DELETE FROM affaire_chapter_settings WHERE affaire_id=?",
                    (self.affaire_id,),
                )
                conn.execute(
                    "DELETE FROM affaire_estimation_section_sort WHERE affaire_id=?",
                    (self.affaire_id,),
                )
                conn.execute("DELETE FROM affaires WHERE id=?", (self.affaire_id,))
                conn.commit()
                self.affaire_id = None
            finally:
                conn.close()


class TestEstimationLayoutDelete(unittest.TestCase):
    def setUp(self):
        models.ensure_app_tables()
        self.affaire_id, _ = models.create_affaire(
            {"name": "Delete layout test", "surface_sdo": 1000, "puissance_pv_kwc": 100}
        )

    def tearDown(self):
        if not getattr(self, "affaire_id", None):
            return
        conn = models.get_db()
        aid = self.affaire_id
        try:
            conn.execute("DELETE FROM affaire_lines WHERE affaire_id=?", (aid,))
            conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id=?", (aid,))
            conn.execute(
                "DELETE FROM affaire_estimation_section_sort WHERE affaire_id=?", (aid,)
            )
            conn.execute("DELETE FROM affaires WHERE id=?", (aid,))
            conn.commit()
        finally:
            conn.close()

    def test_delete_tree_article(self):
        rows = models.get_estimation_catalog_rows(self.affaire_id)
        self.assertTrue(rows)
        sample = rows[0]
        out = handle_layout_action(
            self.affaire_id,
            "add_article",
            {"chapter": sample["chapter"], "section": sample["section"]},
        )
        self.assertEqual(out.get("status"), "ok")
        line_id = out["line_id"]
        del_out = handle_layout_action(
            self.affaire_id,
            "delete_article",
            {"line_id": line_id},
        )
        self.assertEqual(del_out.get("status"), "ok")
        conn = models.get_db()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM affaire_lines WHERE id=?",
                (line_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)

    def test_delete_catalog_article_from_affaire(self):
        rows = models.get_estimation_catalog_rows(self.affaire_id)
        self.assertTrue(rows)
        target = rows[0]
        dpgf_id = target["dpgf_id"]
        out = handle_layout_action(
            self.affaire_id,
            "delete_article",
            {"dpgf_id": dpgf_id},
        )
        self.assertEqual(out.get("status"), "ok")
        remaining = models.get_estimation_catalog_rows(self.affaire_id)
        self.assertFalse(any(r.get("dpgf_id") == dpgf_id for r in remaining))

    def test_delete_catalog_section_from_affaire(self):
        rows = models.get_estimation_catalog_rows(self.affaire_id)
        self.assertTrue(rows)
        chap, sec = rows[0]["chapter"], rows[0]["section"]
        before = [r for r in rows if r["chapter"] == chap and r["section"] == sec]
        self.assertTrue(before)
        out = handle_layout_action(
            self.affaire_id,
            "delete_section",
            {"chapter": chap, "section": sec},
        )
        self.assertEqual(out.get("status"), "ok")
        after = models.get_estimation_catalog_rows(self.affaire_id)
        self.assertFalse(
            any(r["chapter"] == chap and r["section"] == sec for r in after)
        )


if __name__ == "__main__":
    unittest.main()
