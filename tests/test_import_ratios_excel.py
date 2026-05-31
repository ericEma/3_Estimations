"""Tests import bootstrap Excel REX → estimation_ratios.db."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import db_ratios  # noqa: E402
import engine_ratio_referentiel as err  # noqa: E402
from scripts.import_ratios_excel import (  # noqa: E402
    import_rex_file,
    normalize_category,
    parse_rex_row,
)


class TestImportRatiosExcel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmpdir = tempfile.TemporaryDirectory()
        cls._db_path = Path(cls._tmpdir.name) / "test_ratios.db"
        cls._patch = patch.object(db_ratios, "get_ratios_db_path", return_value=cls._db_path)
        cls._patch.start()
        db_ratios.ensure_schema()

    @classmethod
    def tearDownClass(cls):
        cls._patch.stop()
        cls._tmpdir.cleanup()

    def test_normalize_category(self):
        self.assertEqual(normalize_category("Hopital"), "Hôpital")
        self.assertEqual(normalize_category("Lycée"), "Lycée")
        self.assertEqual(normalize_category("Industrie"), "Industrie")
        self.assertIsNone(normalize_category("A Saisir"))

    def test_import_rex_dry_run(self):
        xlsx = ROOT / "Ratios" / "0_ Ratios de prix_ 17-04-2025.xlsx"
        if not xlsx.is_file():
            self.skipTest("Fichier Excel archive absent")
        stats = import_rex_file(xlsx, dry_run=True)
        self.assertGreater(stats["imported"], 10)
        self.assertEqual(stats["errors"], 0)

    def test_insert_archive_source(self):
        sid = err.insert_archive_source(
            name="Test archive",
            category_name="Hôpital",
            devis_date="2020-06-01",
            surface_sdo=1000,
            total_ht_combined=500000,
            source_file="test.xlsx",
            import_batch_id="test_batch",
        )
        self.assertGreater(sid, 0)
        ratios = err.get_typology_ratios("Hôpital")
        self.assertIsNotNone(ratios)

    def test_replace_batch(self):
        err.insert_archive_source(
            name="A",
            category_name="Industrie",
            devis_date="2019-06-01",
            surface_sdo=500,
            total_ht_combined=100000,
            import_batch_id="batch_replace",
        )
        n = err.delete_sources_by_batch("batch_replace")
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
