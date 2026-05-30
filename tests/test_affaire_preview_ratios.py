"""Sprint 5 — ratios globaux éditables sur la fiche affaire."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import models  # noqa: E402
from app import app, compute_affaire_preview_estimation  # noqa: E402


class TestAffairePreviewRatios(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def test_preview_uses_global_ratio_overrides(self):
        payload = compute_affaire_preview_estimation(
            surface_sdo=100,
            puissance_pv_kwc=10,
            taux_phase=0,
            taux_incertitude=0,
            coef_risque=0,
            coef_complexity_cfo=1,
            coef_complexity_cfa=1,
            pv_system_type="toiture",
            ratio_global_cfo_m2=10,
            ratio_global_cfa_m2=20,
            ratio_global_pv_kwc=30,
        )

        self.assertEqual(payload["prix_cfo"], 1000)
        self.assertEqual(payload["prix_cfa"], 2000)
        self.assertEqual(payload["prix_pv"], 300)
        self.assertEqual(payload["prix_total"], 3300)
        self.assertEqual(payload["ratio_m2_cfo"], 10)
        self.assertEqual(payload["ratio_m2_cfa"], 20)
        self.assertEqual(payload["ratio_kwc_pv"], 30)

    def test_create_affaire_persists_global_ratios(self):
        aid, _ = models.create_affaire(
            {
                "name": "Test ratios globaux fiche",
                "surface_sdo": 1000,
                "puissance_pv_kwc": 100,
                "ratio_global_cfo_m2": 11.5,
                "ratio_global_cfa_m2": 22.5,
                "ratio_global_pv_kwc": 333.0,
            }
        )
        try:
            aff = models.get_affaire(aid)
            self.assertEqual(float(aff["ratio_global_cfo_m2"]), 11.5)
            self.assertEqual(float(aff["ratio_global_cfa_m2"]), 22.5)
            self.assertEqual(float(aff["ratio_global_pv_kwc"]), 333.0)
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

    def test_edit_route_persists_global_ratios(self):
        aid, _ = models.create_affaire(
            {
                "name": "Test edit ratios globaux",
                "surface_sdo": 1000,
                "puissance_pv_kwc": 100,
            }
        )
        try:
            client = app.test_client()
            res = client.post(
                f"/affaire/{aid}/edit",
                data={
                    "name": "Test edit ratios globaux",
                    "client": "",
                    "adresse": "",
                    "surface_sdo": "1000",
                    "category_id": "",
                    "coef_complexity_cfo": "1",
                    "coef_complexity_cfa": "1",
                    "coef_complexity_pv": "1",
                    "ratio_global_cfo_m2": "123.45",
                    "ratio_global_cfa_m2": "67.89",
                    "ratio_global_pv_kwc": "456.78",
                    "coef_risque": "1",
                    "kva_cible": "800",
                    "puissance_pv_kwc": "100",
                    "pv_system_type": "toiture",
                    "phase_etude": "APD",
                    "taux_phase": "3",
                    "taux_incertitude": "3",
                    "notes": "",
                },
            )
            self.assertEqual(res.status_code, 302)

            aff = models.get_affaire(aid)
            self.assertEqual(float(aff["ratio_global_cfo_m2"]), 123.45)
            self.assertEqual(float(aff["ratio_global_cfa_m2"]), 67.89)
            self.assertEqual(float(aff["ratio_global_pv_kwc"]), 456.78)

            res = client.get(f"/affaire/{aid}/edit")
            self.assertEqual(res.status_code, 200)
            self.assertIn(b'value="123.45"', res.data)
            self.assertIn(b'value="67.89"', res.data)
            self.assertIn(b'value="456.78"', res.data)
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
