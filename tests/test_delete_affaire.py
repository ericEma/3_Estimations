"""Tests suppression affaire (événements dérive ±3 %)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import affaire_drift  # noqa: E402
import models  # noqa: E402


class TestDeleteAffaire(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        models.ensure_app_tables()

    def test_delete_with_drift_events(self):
        aid, prof = models.create_affaire(
            {
                "name": "Affaire delete drift",
                "surface_sdo": 1000,
                "phase_etude": "APS",
            }
        )
        conn = models.get_db(prof)
        affaire_drift.ensure_drift_tables(conn)
        conn.execute(
            """
            INSERT INTO affaire_client_baselines
                (affaire_id, scope, phase_etude, version_num, status, total_ht, payload_json)
            VALUES (?, 'fiche', 'APS', 1, 'remise', 100, '{}')
            """,
            (aid,),
        )
        bid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            """
            INSERT INTO affaire_change_events
                (affaire_id, scope, reference_baseline_id, old_total_ht, new_total_ht, drift_pct)
            VALUES (?, 'fiche', ?, 100, 110, 0.1)
            """,
            (aid, bid),
        )
        conn.commit()
        conn.close()

        models.delete_affaire(aid, profile=prof)

        conn = models.get_db(prof)
        try:
            self.assertIsNone(
                conn.execute("SELECT 1 FROM affaires WHERE id = ?", (aid,)).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM affaire_change_events WHERE affaire_id = ?", (aid,)
                ).fetchone()
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
