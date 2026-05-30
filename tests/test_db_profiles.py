"""Profils BDD — mapping catégorie → fichier SQLite."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_profiles import (  # noqa: E402
    profile_for_category_name,
    normalize_profile,
    normalize_profile_filter,
)


class TestDbProfiles(unittest.TestCase):
    def test_category_mapping(self):
        self.assertEqual(profile_for_category_name("Hôpital"), "hopitaux")
        self.assertEqual(profile_for_category_name("Industrie"), "industriel")
        self.assertEqual(profile_for_category_name("EHPAD"), "autres")
        self.assertEqual(profile_for_category_name("Bureaux"), "autres")

    def test_normalize_profile(self):
        self.assertEqual(normalize_profile("Hopitaux"), "hopitaux")
        self.assertEqual(normalize_profile_filter("tous"), "tous")
        self.assertEqual(normalize_profile_filter(""), "tous")


if __name__ == "__main__":
    unittest.main()
