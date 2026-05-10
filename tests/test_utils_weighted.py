"""calculate_weighted_price et get_effective_date (Lot 2)."""
import sqlite3
import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import calculate_weighted_price, get_effective_date


class TestCalculateWeightedPrice(unittest.TestCase):
    def test_two_identical_sources_weighted_equals_actualized(self):
        base_d = date(2024, 1, 1)
        ref = date(2026, 1, 1)
        out = calculate_weighted_price(
            100.0, base_d, 100.0, base_d, today=ref
        )
        self.assertEqual(out["sources_used"], 2)
        self.assertIsNotNone(out["weighted_price"])
        self.assertAlmostEqual(out["base_actualized"], out["devis_actualized"], places=1)

    def test_single_devis_source(self):
        out = calculate_weighted_price(
            None,
            None,
            50.0,
            date(2025, 6, 1),
            today=date(2026, 1, 1),
        )
        self.assertEqual(out["sources_used"], 1)
        self.assertIsNotNone(out["weighted_price"])
        self.assertGreater(out["weighted_price"], 0)

    def test_no_sources(self):
        out = calculate_weighted_price(None, None, None, None, today=date(2026, 1, 1))
        self.assertEqual(out["sources_used"], 0)
        self.assertIsNone(out["weighted_price"])


class TestGetEffectiveDate(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE dpgf_articles (
                id INTEGER PRIMARY KEY,
                chapter TEXT,
                section TEXT,
                last_updated TEXT,
                pu_ht_ref REAL DEFAULT 0
            )
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_article_last_updated_wins(self):
        self.conn.execute(
            "INSERT INTO dpgf_articles (id, chapter, section, last_updated) "
            "VALUES (1, 'Courants Forts', 'A', '2024-03-15')"
        )
        self.conn.commit()
        self.assertEqual(get_effective_date(self.conn, 1), "2024-03-15")

    def test_section_pivot_when_article_null(self):
        self.conn.execute(
            "INSERT INTO dpgf_articles (id, chapter, section, last_updated) "
            "VALUES (1, 'Courants Forts', 'A', NULL)"
        )
        self.conn.execute(
            "INSERT INTO dpgf_articles (id, chapter, section, last_updated) "
            "VALUES (2, 'Courants Forts', 'A', '2022-01-01')"
        )
        self.conn.commit()
        self.assertEqual(get_effective_date(self.conn, 1), "2022-01-01")


if __name__ == "__main__":
    unittest.main()
