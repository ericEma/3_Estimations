"""Nettoyage désignations / tokenisation (élisions françaises)."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestEngineMatchingClean(unittest.TestCase):
    def test_radical_keeps_elided_words(self):
        from engine_matching import _TOKEN_RE, clean_designation_radical

        s = "Création d'un départ dans l'armoire générale chantier"
        out = clean_designation_radical(s)
        toks = _TOKEN_RE.findall(out)
        self.assertIn("d'un", toks)
        self.assertIn("l'armoire", toks)
        self.assertNotIn("d", toks)
        self.assertNotIn("l", toks)
        self.assertNotIn("un", toks)

    def test_radical_unicode_apostrophe(self):
        from engine_matching import _TOKEN_RE, clean_designation_radical

        s = "Création d\u2019un départ dans l\u2019armoire générale"
        out = clean_designation_radical(s)
        toks = _TOKEN_RE.findall(out)
        self.assertTrue(any("d" in t and "un" in t for t in toks))
        self.assertTrue(any("l" in t and "armoire" in t for t in toks))
        self.assertNotIn("d", toks)
        self.assertNotIn("l", toks)


if __name__ == "__main__":
    unittest.main()
