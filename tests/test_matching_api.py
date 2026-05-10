"""Smoke API Matching + page cockpit."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TestMatchingApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import models

        models.ensure_app_tables()
        from app import app as flask_app

        cls.app = flask_app
        cls.app.config["TESTING"] = True

    def test_matching_page_contains_shell(self):
        client = self.app.test_client()
        r = client.get("/matching")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"mv-page", r.data)
        self.assertIn(b"matching.js", r.data)

    def test_matching_data_404_unknown_project(self):
        client = self.app.test_client()
        r = client.get("/api/matching/999999999/data")
        self.assertEqual(r.status_code, 404)

    def test_matching_data_ok_when_project_exists(self):
        import models

        conn = models.get_db()
        row = conn.execute(
            "SELECT id FROM projects ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            self.skipTest("Aucun projet en base — importer un devis une fois pour activer ce test.")
        pid = int(row["id"])
        client = self.app.test_client()
        r = client.get(f"/api/matching/{pid}/data")
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True))
        payload = r.get_json()
        self.assertIn("chapters", payload)
        self.assertIn("project", payload)

    def test_validate_synonym_endpoints(self):
        import models

        conn = models.get_db()
        row = conn.execute(
            "SELECT id FROM projects ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            self.skipTest("Aucun projet en base")
        pid = int(row["id"])

        client = self.app.test_client()
        v = client.post(f"/api/matching/{pid}/validate")
        self.assertEqual(v.status_code, 200)
        self.assertEqual(v.get_json().get("status"), "ok")

        s = client.post(
            "/api/matching/synonym",
            json={"original_term": "__pytest_syn__", "mapped_term": "dummy"},
        )
        self.assertEqual(s.status_code, 200)
        conn = models.get_db()
        conn.execute(
            "DELETE FROM synonyms WHERE original_term = ?",
            ("__pytest_syn__",),
        )
        conn.commit()
        conn.close()

    def test_line_candidates_exclude_select_when_line_exists(self):
        import models

        conn = models.get_db()
        row = conn.execute(
            """
            SELECT dl.id, dl.project_id, da.id AS aid
            FROM devis_lines dl
            JOIN dpgf_articles da ON da.id = dl.dpgf_article_id
            WHERE dl.mapping_status IN ('auto', 'manual', 'pending')
            LIMIT 1
            """
        ).fetchone()
        conn.close()
        if not row:
            self.skipTest("Aucune ligne devis mappée pour smoke test")
        line_id = int(row["id"])
        art_id = int(row["aid"])
        client = self.app.test_client()

        c = client.get(f"/api/matching/line/{line_id}/candidates")
        self.assertEqual(c.status_code, 200)
        self.assertIn("candidates", c.get_json())

        ex = client.post(f"/api/matching/line/{line_id}/exclude")
        self.assertEqual(ex.status_code, 200, ex.get_data(as_text=True))
        ex2 = client.post(f"/api/matching/line/{line_id}/exclude")
        self.assertEqual(ex2.status_code, 200)

        sel = client.post(
            f"/api/matching/line/{line_id}/select",
            json={
                "dpgf_article_id": art_id,
                "memorize_synonym": False,
                "cleaned_term": "",
            },
        )
        self.assertEqual(sel.status_code, 200, sel.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
