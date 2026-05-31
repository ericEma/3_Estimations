"""Test export Excel carte client."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

RAPPORT = ROOT / "Rapport_Excel"
sys.path.insert(0, str(RAPPORT))

from generer_carte_client import generer  # noqa: E402


class TestGenererCarteClient(unittest.TestCase):
    def test_generer_creates_xlsx(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "carte.xlsx"
            generer(
                {
                    "affaire_name": "Test affaire",
                    "phase_etude": "APS",
                    "surface_sdo": 1000,
                    "prix_cfo": 100000,
                    "prix_cfa": 80000,
                    "prix_pv": 20000,
                    "prix_total": 200000,
                    "ratio_total_m2": 200,
                },
                str(out),
            )
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()
