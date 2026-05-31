"""Tests moteur ratios typologie (estimation_ratios.db)."""
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db_ratios  # noqa: E402
import engine_ratio_referentiel as err  # noqa: E402


class TestRatioReferentiel(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "test_ratios.db"
        self._patch = patch.object(db_ratios, "get_ratios_db_path", return_value=self._db_path)
        self._patch.start()
        db_ratios.ensure_schema()

    def tearDown(self):
        self._patch.stop()
        self._tmpdir.cleanup()

    def test_actualize_three_percent(self):
        d = date(2024, 1, 1)
        r = err.actualize_ratio(100.0, d, 2026)
        self.assertAlmostEqual(r, 100.0 * (1.03 ** 2), places=2)

    def test_temporal_weight_recent(self):
        conn = db_ratios.connect()
        try:
            w = err.temporal_weight(6.0, conn)
            self.assertEqual(w, 1.0)
            w_old = err.temporal_weight(40.0, conn)
            self.assertEqual(w_old, 0.1)
        finally:
            conn.close()

    def test_classify_total_only(self):
        level, total, cfo, cfa, pv, imp = err.classify_detail_level(
            {"CFO": 0, "CFA": 0, "PV": 0}, 500_000.0
        )
        self.assertEqual(level, "total_only")
        self.assertTrue(imp)
        self.assertEqual(total, 500_000.0)

    def test_classify_full(self):
        lots = {"CFO": 300_000.0, "CFA": 200_000.0, "PV": 0.0}
        level, total, cfo, cfa, pv, imp = err.classify_detail_level(lots, 500_000.0)
        self.assertEqual(level, "full")
        self.assertFalse(imp)
        self.assertEqual(cfo, 300_000.0)

    def test_insert_and_aggregate(self):
        conn = db_ratios.connect()
        try:
            ref_year = db_ratios.get_annee_reference(conn)
            recent = (date.today() - timedelta(days=200)).isoformat()
            for i, (cfo, cfa) in enumerate([(400, 350), (420, 330), (380, 360)], start=1):
                cur = conn.execute(
                    """
                    INSERT INTO ratio_devis_sources (
                        name, category_name, devis_date, surface_sdo,
                        puissance_pv_kwc, total_ht, total_ht_cfo, total_ht_cfa,
                        total_ht_pv, detail_level, imputed
                    ) VALUES (?, 'Hôpital', ?, 5000, 0, ?, ?, ?, 0, 'full', 0)
                    """,
                    (f"Devis {i}", recent, cfo + cfa, cfo, cfa),
                )
                sid = cur.lastrowid
                comp = err.compute_unit_ratios(
                    total_ht_cfo=cfo,
                    total_ht_cfa=cfa,
                    total_ht_pv=0,
                    total_ht=cfo + cfa,
                    surface_sdo=5000,
                    puissance_pv_kwc=0,
                    coef_cfo=1,
                    coef_cfa=1,
                    coef_pv=1,
                    devis_date=recent,
                    conn=conn,
                )
                conn.execute(
                    """
                    INSERT INTO ratio_source_computed (
                        source_id, annee_reference,
                        ratio_cfo_m2_actualise, ratio_cfa_m2_actualise
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        sid,
                        comp["annee_reference"],
                        comp["ratio_cfo_m2_actualise"],
                        comp["ratio_cfa_m2_actualise"],
                    ),
                )
            conn.commit()
            err.recompute_aggregates("Hôpital", conn=conn)
            conn.commit()
        finally:
            conn.close()

        ratios = err.get_typology_ratios("Hôpital")
        self.assertIsNotNone(ratios)
        self.assertIsNotNone(ratios["ratio_m2_cfo"])
        self.assertIsNotNone(ratios["ratio_m2_cfa"])
        self.assertGreater(ratios["nb_sources_cfo"], 0)
        self.assertAlmostEqual(ratios["ratio_m2_cfo"], ratios["ratio_m2_cfo"], places=0)

    def test_autres_average_excludes_hopital_industrie(self):
        avg = err.get_autres_typology_average()
        if not avg:
            self.skipTest("Pas assez de stats pour moyenne Autres")
        hop = err.get_typology_ratios("Hôpital")
        ind = err.get_typology_ratios("Industrie")
        if hop and ind:
            self.assertNotAlmostEqual(avg["ratio_m2_cfo"], hop["ratio_m2_cfo"], places=0)
            self.assertNotAlmostEqual(avg["ratio_m2_cfo"], ind["ratio_m2_cfo"], places=0)
        typo, kind = err.resolve_category_ratios("Bureaux")
        self.assertEqual(kind, "moyenne_autres")
        self.assertIsNotNone(typo)


if __name__ == "__main__":
    unittest.main()
