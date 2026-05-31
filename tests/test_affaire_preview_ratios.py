"""Sprint 5 — ratios globaux éditables sur la fiche affaire."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import engine_ratio_referentiel as ratio_ref  # noqa: E402
import models  # noqa: E402
from app import (  # noqa: E402
    app,
    compute_affaire_preview_estimation,
    _typology_ratio_defaults,
)


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
            # Sans typologie : affichage = repli bibliothèque (pas les ratios stockés)
            defaults = _typology_ratio_defaults(affaire=aff)
            self.assertIn(
                f'value="{defaults["preview_ratio_global_cfo_m2"]:.2f}"'.encode(),
                res.data,
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

    def test_typology_defaults_prefer_statistics_over_stored(self):
        hopital = ratio_ref.get_typology_ratios("Hôpital")
        if not hopital or hopital.get("ratio_m2_cfo") is None:
            self.skipTest("Pas de ratios statistiques Hôpital en BDD test")

        hopital_id = next(
            (c["id"] for c in models.get_categories() if c["name"] == "Hôpital"),
            None,
        )
        if not hopital_id:
            self.skipTest("Catégorie Hôpital absente")

        aid, prof = models.create_affaire(
            {
                "name": "Typo stats",
                "surface_sdo": 1000,
                "category_id": hopital_id,
                "ratio_global_cfo_m2": 999.0,
                "ratio_global_cfa_m2": 888.0,
            }
        )
        try:
            aff = models.get_affaire(aid, profile=prof)
            ctx = _typology_ratio_defaults(affaire=aff, category_id=hopital_id)
            self.assertEqual(ctx["ratio_source"], "typologie")
            self.assertAlmostEqual(ctx["preview_ratio_global_cfo_m2"], hopital["ratio_m2_cfo"], places=2)
            self.assertAlmostEqual(ctx["preview_ratio_global_cfa_m2"], hopital["ratio_m2_cfa"], places=2)
        finally:
            models.delete_affaire(aid, profile=prof)

    def test_preview_ignores_stale_form_ratios_when_typology_available(self):
        hopital = ratio_ref.get_typology_ratios("Hôpital")
        if not hopital or hopital.get("ratio_m2_cfo") is None:
            self.skipTest("Pas de ratios statistiques Hôpital en BDD test")

        hopital_id = next(
            (c["id"] for c in models.get_categories() if c["name"] == "Hôpital"),
            None,
        )
        if not hopital_id:
            self.skipTest("Catégorie Hôpital absente")

        client = app.test_client()
        res = client.get(
            "/api/affaire/preview_estimation"
            f"?category_id={hopital_id}&surface_sdo=1000"
            "&ratio_global_cfo_m2=573.56&ratio_global_cfa_m2=210.56"
            "&taux_phase=3&taux_incertitude=3&coef_risque=1"
            "&coef_complexity_cfo=1&coef_complexity_cfa=1"
            "&puissance_pv_kwc=100&pv_system_type=toiture&ratio_manual=0"
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["ratio_source"], "typologie")
        self.assertAlmostEqual(data["ratio_m2_cfo"], hopital["ratio_m2_cfo"], places=2)
        self.assertAlmostEqual(data["ratio_m2_cfa"], hopital["ratio_m2_cfa"], places=2)

    def test_ehpad_uses_hopital_typology_alias(self):
        hopital = ratio_ref.get_typology_ratios("Hôpital")
        ehpad = ratio_ref.get_typology_ratios("EHPAD")
        if not hopital or not ehpad:
            self.skipTest("Ratios Hôpital/EHPAD indisponibles")
        self.assertAlmostEqual(ehpad["ratio_m2_cfo"], hopital["ratio_m2_cfo"], places=2)
        self.assertAlmostEqual(ehpad["ratio_m2_cfa"], hopital["ratio_m2_cfa"], places=2)

    def test_bureaux_uses_autres_average_not_bibliotheque(self):
        avg = ratio_ref.get_autres_typology_average()
        if not avg or avg.get("ratio_m2_cfo") is None:
            self.skipTest("Moyenne Autres indisponible")

        bureaux_id = next(
            (c["id"] for c in models.get_categories() if c["name"] == "Bureaux"),
            None,
        )
        if not bureaux_id:
            self.skipTest("Catégorie Bureaux absente")

        ctx = _typology_ratio_defaults(category_id=bureaux_id)
        self.assertEqual(ctx["ratio_source"], "typologie_moyenne_autres")
        self.assertAlmostEqual(ctx["preview_ratio_global_cfo_m2"], avg["ratio_m2_cfo"], places=2)
        self.assertAlmostEqual(ctx["preview_ratio_global_cfa_m2"], avg["ratio_m2_cfa"], places=2)
        self.assertLess(ctx["preview_ratio_global_cfo_m2"], 400)


if __name__ == "__main__":
    unittest.main()
