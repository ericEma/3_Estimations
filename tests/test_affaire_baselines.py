"""Tests validation carte client (affaire_client_baselines)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import affaire_baselines  # noqa: E402
import models  # noqa: E402


class TestAffaireBaselines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def _create_affaire(self, name="Test baseline"):
        aid, prof = models.create_affaire(
            {
                "name": name,
                "surface_sdo": 1000,
                "puissance_pv_kwc": 50,
                "phase_etude": "APS",
                "ratio_global_cfo_m2": 100,
                "ratio_global_cfa_m2": 80,
                "ratio_global_pv_kwc": 400,
            }
        )
        return aid, prof

    def test_validate_and_supersede(self):
        aid, prof = self._create_affaire()
        preview1 = {
            "prix_cfo": 100000,
            "prix_cfa": 80000,
            "prix_pv": 20000,
            "prix_total": 200000,
            "ratio_m2_cfo": 100,
            "ratio_m2_cfa": 80,
            "ratio_kwc_pv": 400,
        }
        fiche = {
            "phase_etude": "APS",
            "surface_sdo": 1000,
            "ratio_global_cfo_m2": 100,
            "ratio_global_cfa_m2": 80,
            "ratio_global_pv_kwc": 400,
            "coef_complexity_cfo": 1,
            "coef_complexity_cfa": 1,
            "taux_phase": 4,
            "taux_incertitude": 3,
            "coef_risque": 1,
            "pv_system_type": "toiture",
        }
        b1 = affaire_baselines.validate_fiche_baseline(aid, prof, fiche, preview1)
        self.assertEqual(b1["version_num"], 1)
        self.assertEqual(b1["status"], "active")

        preview2 = dict(preview1, prix_total=210000)
        b2 = affaire_baselines.validate_fiche_baseline(aid, prof, fiche, preview2)
        self.assertEqual(b2["version_num"], 2)

        active = affaire_baselines.get_active_baseline(aid, profile=prof)
        self.assertEqual(active["id"], b2["id"])
        history = affaire_baselines.list_baselines(aid, profile=prof)
        self.assertEqual(len(history), 2)
        superseded = [h for h in history if h["status"] == "superseded"]
        self.assertEqual(len(superseded), 1)

    def test_phase_change_marks_remise(self):
        aid, prof = self._create_affaire("Phase change")
        preview = {"prix_cfo": 1, "prix_cfa": 1, "prix_pv": 0, "prix_total": 100}
        fiche = {"phase_etude": "APS", "surface_sdo": 1000, "taux_phase": 4,
                 "taux_incertitude": 3, "coef_risque": 1, "coef_complexity_cfo": 1,
                 "coef_complexity_cfa": 1, "pv_system_type": "toiture"}
        affaire_baselines.validate_fiche_baseline(aid, prof, fiche, preview)
        old = models.get_affaire(aid, profile=prof)
        models.update_affaire(aid, {**old, "phase_etude": "APD"})
        history = affaire_baselines.list_baselines(aid, profile=prof)
        remise = [h for h in history if h["status"] == "remise"]
        self.assertEqual(len(remise), 1)
        self.assertIsNone(affaire_baselines.get_active_baseline(aid, profile=prof))

    def test_estimation_validate_and_new_version(self):
        aid, prof = self._create_affaire("Estim baseline")
        old = models.get_affaire(aid, profile=prof)
        models.update_affaire(aid, {**old, "phase_etude": "APD"})
        totals = {"CFO": 1000, "CFA": 500, "PV": 200, "ALL": 1700}
        b1 = affaire_baselines.validate_estimation_baseline(
            aid, prof, models.get_affaire(aid, profile=prof), totals
        )
        self.assertEqual(b1["scope"], "estimation")
        self.assertEqual(b1["version_num"], 1)
        out = affaire_baselines.start_new_estimation_version(aid, prof)
        self.assertEqual(out["previous_version"], 1)
        self.assertEqual(out["next_version"], 2)
        self.assertIsNone(
            affaire_baselines.get_active_baseline(
                aid, affaire_baselines.SCOPE_ESTIMATION, prof
            )
        )
        totals2 = {"CFO": 1100, "CFA": 500, "PV": 200, "ALL": 1800}
        b2 = affaire_baselines.validate_estimation_baseline(
            aid, prof, models.get_affaire(aid, profile=prof), totals2
        )
        self.assertEqual(b2["version_num"], 2)


if __name__ == "__main__":
    unittest.main()
