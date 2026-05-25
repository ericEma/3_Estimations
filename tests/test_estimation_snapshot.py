"""Sprint 1 — snapshot estimation à la création d'affaire."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import models  # noqa: E402


class TestEstimationSnapshot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def test_create_affaire_initializes_snapshot(self):
        aid = models.create_affaire({
            "name": "Test snapshot unitaire",
            "surface_sdo": 1000,
            "puissance_pv_kwc": 100,
        })
        self.assertGreater(aid, 0)
        aff = models.get_affaire(aid)
        self.assertTrue(aff.get("estimation_initialized_at"))

        conn = models.get_db()
        try:
            n_lines = conn.execute(
                "SELECT COUNT(*) FROM affaire_lines WHERE affaire_id = ? AND dpgf_article_id IS NOT NULL",
                (aid,),
            ).fetchone()[0]
            n_articles = conn.execute(
                """
                SELECT COUNT(*) FROM dpgf_articles
                WHERE row_type = 'article'
                  AND (is_hidden IS NULL OR is_hidden = 0)
                """
            ).fetchone()[0]
        finally:
            conn.close()

        self.assertEqual(n_lines, n_articles)
        self.assertGreater(n_lines, 0)

        rows = models.get_estimation_catalog_rows(aid)
        self.assertTrue(all(r.get("snapshot") for r in rows))
        self.assertTrue(all(r.get("line_id") for r in rows))

        conn = models.get_db()
        try:
            conn.execute("DELETE FROM affaire_lines WHERE affaire_id = ?", (aid,))
            conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id = ?", (aid,))
            conn.execute("DELETE FROM affaires WHERE id = ?", (aid,))
            conn.commit()
        finally:
            conn.close()

    def test_snapshot_pu_immune_to_biblio_change(self):
        """PU figé dans ratio_ref : modification pu_ht_ref catalogue sans impact."""
        aid = models.create_affaire({
            "name": "Test gel PU",
            "surface_sdo": 500,
            "puissance_pv_kwc": 50,
        })
        conn = models.get_db()
        try:
            art_row = conn.execute(
                """
                SELECT id FROM dpgf_articles
                WHERE row_type = 'article'
                  AND COALESCE(pu_ht_ref, 0) > 0
                  AND (is_hidden IS NULL OR is_hidden = 0)
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if not art_row:
            self.skipTest("Aucun article avec pu_ht_ref > 0")
        dpgf_id = int(art_row["id"])

        rows_before = models.get_estimation_catalog_rows(aid)
        sample = next(r for r in rows_before if r["dpgf_id"] == dpgf_id)
        frozen_pu = float(sample["ref_pu_ht"])
        self.assertGreater(frozen_pu, 0)
        self.assertEqual(float(sample.get("unit_price_ht") or 0), frozen_pu)

        conn = models.get_db()
        try:
            conn.execute(
                "UPDATE dpgf_articles SET pu_ht_ref = 99999.99 WHERE id = ?",
                (dpgf_id,),
            )
            conn.commit()
        finally:
            conn.close()

        rows_after = models.get_estimation_catalog_rows(aid)
        row_after = next(r for r in rows_after if r["dpgf_id"] == dpgf_id)
        self.assertEqual(row_after["ref_pu_ht"], frozen_pu)

        conn = models.get_db()
        try:
            conn.execute(
                "UPDATE dpgf_articles SET pu_ht_ref = ? WHERE id = ?",
                (frozen_pu, dpgf_id),
            )
            conn.execute("DELETE FROM affaire_lines WHERE affaire_id = ?", (aid,))
            conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id = ?", (aid,))
            conn.execute("DELETE FROM affaires WHERE id = ?", (aid,))
            conn.commit()
        finally:
            conn.close()


    def test_snapshot_uses_compute_ratios_when_pu_ht_ref_null(self):
        """Aligné bibliothèque : PU devis si pu_ht_ref NULL (ex. Installation de chantier)."""
        conn = models.get_db()
        try:
            row = conn.execute(
                """
                SELECT id FROM dpgf_articles
                WHERE section = 'Installation de chantier'
                  AND designation LIKE 'Création d%armoire%'
                LIMIT 1
                """
            ).fetchone()
        finally:
            conn.close()
        if not row:
            self.skipTest("Section Installation de chantier absente")

        dpgf_id = int(row["id"])
        aid = models.create_affaire({
            "name": "Test PU devis snapshot",
            "surface_sdo": 1000,
            "puissance_pv_kwc": 100,
        })
        try:
            cat = models.get_estimation_catalog_rows(aid)
            art = next(r for r in cat if r["dpgf_id"] == dpgf_id)
            self.assertGreater(float(art["ref_pu_ht"]), 100)
            self.assertGreater(float(art.get("unit_price_ht") or 0), 100)
        finally:
            conn = models.get_db()
            try:
                conn.execute("DELETE FROM affaire_lines WHERE affaire_id = ?", (aid,))
                conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id = ?", (aid,))
                conn.execute("DELETE FROM affaires WHERE id = ?", (aid,))
                conn.commit()
            finally:
                conn.close()

    def test_macro_section_total_switches_to_article_detail_total(self):
        """Section macro : un article chiffré remplace le total ratio de sous-chapitre."""
        aid = models.create_affaire({
            "name": "Test total section detail",
            "surface_sdo": 1000,
            "puissance_pv_kwc": 100,
        })
        conn = models.get_db()
        try:
            row = conn.execute(
                """
                SELECT da.chapter, da.section, al.id AS line_id,
                       acs.qty, acs.ratio_m2_override
                FROM affaire_chapter_settings acs
                JOIN dpgf_articles da
                  ON acs.chapter_key = 'sect:' || da.chapter || '|' || da.section
                JOIN affaire_lines al
                  ON al.affaire_id = acs.affaire_id AND al.dpgf_article_id = da.id
                WHERE acs.affaire_id = ?
                  AND acs.use_macro = 1
                  AND acs.ratio_m2_override > 0
                LIMIT 1
                """,
                (aid,),
            ).fetchone()
            if not row:
                self.skipTest("Aucune section macro avec article disponible")

            before = models.compute_estimation_kpis(aid)
            macro_total = round(float(row["qty"] or 0) * float(row["ratio_m2_override"] or 0), 2)
            detail_total = 123.0
            lot = models.derive_lot_from_chapter(row["chapter"])

            conn.execute(
                """
                UPDATE affaire_lines
                SET quantity = 0, unit_price_ht = NULL, total_ht = 0
                WHERE affaire_id = ?
                  AND dpgf_article_id IN (
                    SELECT id FROM dpgf_articles WHERE chapter = ? AND section = ?
                  )
                """,
                (aid, row["chapter"], row["section"]),
            )
            conn.execute(
                """
                UPDATE affaire_lines
                SET quantity = 1, unit_price_ht = ?, total_ht = ?
                WHERE id = ?
                """,
                (detail_total, detail_total, row["line_id"]),
            )
            conn.commit()

            after = models.compute_estimation_kpis(aid)
            self.assertAlmostEqual(
                after[lot],
                round(before[lot] - macro_total + detail_total, 2),
                places=2,
            )
        finally:
            conn = models.get_db()
            try:
                conn.execute("DELETE FROM affaire_lines WHERE affaire_id = ?", (aid,))
                conn.execute("DELETE FROM affaire_chapter_settings WHERE affaire_id = ?", (aid,))
                conn.execute("DELETE FROM affaire_estimation_section_sort WHERE affaire_id = ?", (aid,))
                conn.execute("DELETE FROM affaires WHERE id = ?", (aid,))
                conn.commit()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
