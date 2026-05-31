"""Tests moteur dérives ±3 % (affaire_drift)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import affaire_baselines  # noqa: E402
import affaire_drift  # noqa: E402
import models  # noqa: E402


class TestAffaireDrift(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def test_pct_drift_and_threshold(self):
        self.assertAlmostEqual(affaire_drift.pct_drift(100, 104), 0.04)
        self.assertTrue(affaire_drift.exceeds_drift_threshold(0.04))
        self.assertFalse(affaire_drift.exceeds_drift_threshold(0.02))

    def test_fiche_drift_creates_event(self):
        aid, prof = models.create_affaire({
            "name": "Drift fiche",
            "surface_sdo": 1000,
            "phase_etude": "APS",
            "ratio_global_cfo_m2": 100,
            "ratio_global_cfa_m2": 80,
            "ratio_global_pv_kwc": 400,
        })
        preview1 = {
            "prix_cfo": 100000,
            "prix_cfa": 80000,
            "prix_pv": 20000,
            "prix_total": 200000,
        }
        fiche = {
            "phase_etude": "APS",
            "surface_sdo": 1000,
            "ratio_global_cfo_m2": 100,
            "ratio_global_cfa_m2": 80,
            "ratio_global_pv_kwc": 400,
            "taux_phase": 4,
            "taux_incertitude": 3,
            "coef_risque": 1,
            "coef_complexity_cfo": 1,
            "coef_complexity_cfa": 1,
            "pv_system_type": "toiture",
        }
        affaire_baselines.validate_fiche_baseline(aid, prof, fiche, preview1)
        old = models.get_affaire(aid, profile=prof)
        models.update_affaire(aid, {**old, "phase_etude": "APD"})

        preview2 = dict(preview1, prix_total=220000, prix_cfo=120000)
        fiche_apd = dict(fiche, phase_etude="APD")
        out = affaire_drift.check_fiche_drift(aid, fiche_apd, preview2, prof)
        self.assertTrue(out["requires_justification"])
        self.assertIsNotNone(out.get("event"))
        self.assertGreater(abs(out["drift_pct"]), 3)

    def test_save_justifications(self):
        aid, prof = models.create_affaire({"name": "Justif", "surface_sdo": 500, "phase_etude": "APS"})
        ref_preview = {"prix_cfo": 1, "prix_cfa": 1, "prix_pv": 0, "prix_total": 100000}
        fiche = {"phase_etude": "APS", "surface_sdo": 500, "taux_phase": 4,
                 "taux_incertitude": 3, "coef_risque": 1, "coef_complexity_cfo": 1,
                 "coef_complexity_cfa": 1, "pv_system_type": "toiture"}
        bl = affaire_baselines.validate_fiche_baseline(aid, prof, fiche, ref_preview)
        old = models.get_affaire(aid, profile=prof)
        models.update_affaire(aid, {**old, "phase_etude": "APD"})
        out = affaire_drift.check_fiche_drift(
            aid, {**fiche, "phase_etude": "APD"},
            {"prix_cfo": 1, "prix_cfa": 1, "prix_pv": 0, "prix_total": 110000},
            prof,
        )
        event = out["event"]
        items_payload = [
            {"id": it["id"], "justification": f"Motif {it['label']}"}
            for it in event["items"]
            if not str(it.get("item_type", "")).startswith("lever_")
        ]
        saved = affaire_drift.save_justifications(
            event["id"], items_payload, "Justif globale test", prof
        )
        self.assertEqual(saved["status"], affaire_drift.STATUS_JUSTIFIED)


if __name__ == "__main__":
    unittest.main()
