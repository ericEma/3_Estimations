"""Tests scripts/backup_db.py — sauvegarde SQLite."""

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.backup_db as backup_db  # noqa: E402


class TestBackupDb(unittest.TestCase):
    def test_resolve_source_prefers_dedicated_then_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = root / "estimation_elec.db"
            dedicated = root / "estimation_hopitaux.db"
            legacy.write_bytes(b"")
            dedicated.write_bytes(b"x")

            with mock.patch.object(backup_db, "ROOT", root), mock.patch.object(
                backup_db, "LEGACY_DB", legacy
            ), mock.patch.object(
                backup_db, "PROFILES", {"Hopitaux": "estimation_hopitaux.db"}
            ):
                src = backup_db._resolve_source_db("Hopitaux")
                self.assertEqual(src, dedicated)

                dedicated.unlink()
                src2 = backup_db._resolve_source_db("Hopitaux")
                self.assertEqual(src2, legacy)

    def test_sqlite_backup_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src.db"
            dst = root / "out" / "copy.db"
            conn = __import__("sqlite3").connect(str(src))
            conn.execute("CREATE TABLE t (id INTEGER)")
            conn.commit()
            conn.close()

            backup_db._sqlite_backup(src, dst)
            self.assertTrue(dst.is_file())
            self.assertGreater(dst.stat().st_size, 0)

    def test_iso_week_id(self):
        self.assertRegex(backup_db._iso_week_id(date(2026, 5, 29)), r"^\d{4}-W\d{2}$")


if __name__ == "__main__":
    unittest.main()
