"""Import upload : redirection vers /matching/<id> via lecture BDD (pas regex log)."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestImportRedirect(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import models

        models.ensure_app_tables()
        from app import app as flask_app

        cls.app = flask_app
        cls.app.config["TESTING"] = True

    def test_redirect_uses_latest_project_id_after_success(self):
        import models

        conn = models.get_db()
        conn.execute(
            """
            INSERT INTO projects (name, source_file, devis_date, surface_sdo)
            VALUES (?, ?, ?, ?)
            """,
            ("__pytest_import_redirect__", "dummy.xlsx", "2025-01-01", 1000.0),
        )
        conn.commit()
        row = conn.execute("SELECT id FROM projects ORDER BY id DESC LIMIT 1").fetchone()
        pid = int(row["id"])
        conn.close()

        def fake_run(*_a, **_k):
            return MagicMock(stdout="", stderr="", returncode=0)

        data = {
            "name": "UploadTest",
            "devis_date": "2025-06-01",
            "category_id": "1",
            "sdo": "1000",
            "coef_cfo": "1.0",
            "coef_cfa": "1.0",
            "coef_pv": "1.0",
        }
        with patch("subprocess.run", fake_run):
            client = self.app.test_client()
            resp = client.post(
                "/import/upload",
                data={
                    **data,
                    "file": (io.BytesIO(b"xlsx placeholder"), "pytest_upload.xlsx"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(resp.status_code, 302, resp.data)
        loc = resp.headers.get("Location", "")
        self.assertIn(f"/matching/{pid}", loc)

        conn = models.get_db()
        conn.execute("DELETE FROM devis_lines WHERE project_id = ?", (pid,))
        conn.execute("DELETE FROM projects WHERE id = ?", (pid,))
        conn.commit()
        conn.close()


if __name__ == "__main__":
    unittest.main()
