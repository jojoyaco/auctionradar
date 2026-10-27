"""Run-history regressions: readable structured limits, without raw objects."""

import json

from auctionradar.web import models
from tests.test_webapp_auth import _WebappFixture


class RunPresentationTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner", is_admin=True)
        self.login(username="owner")

    def test_structured_limitations_are_readable_not_object_coercions(self):
        models.create_run(run_id="structured-source", created_by=self.uid, sources=["boe"])
        models.finalize_run(
            "structured-source",
            status="partial",
            summary={
                "ingest": {"ingested": 3, "new": 1, "updated": 2, "skipped": 0},
                "sources": [
                    {
                        "source_id": "boe",
                        "status": "partial",
                        "depth": "listing_and_details",
                        "items_count": 3,
                        "limitations": [
                            {"reason": "max_pages_reached", "limit": 2},
                            {"reason": {"message": "Document unavailable"}},
                            ["Limited coverage", {"detail": "No possession evidence"}],
                        ],
                        "requests": [
                            {"url": "https://example.org", "raw_path": "/private/raw/file"}
                        ],
                    }
                ],
            },
        )
        run = self.client.get("/api/runs").get_json()["runs"][0]
        messages = run["sources"][0]["limitations"]
        self.assertTrue(all(isinstance(value, str) for value in messages))
        self.assertIn("max pages reached", " ".join(messages))
        self.assertIn("Document unavailable", " ".join(messages))
        self.assertNotIn("[object Object]", json.dumps(run))
        self.assertNotIn("requests", run["sources"][0])
        self.assertEqual(run["summary"]["ingest"]["ingested"], 3)

    def test_queued_source_ids_have_consistent_source_shape(self):
        models.create_run(run_id="queued", created_by=self.uid, sources=["boe", "ayvens"])
        run = self.client.get("/api/runs").get_json()["runs"][0]
        self.assertEqual([s["source_id"] for s in run["sources"]], ["boe", "ayvens"])
        self.assertTrue(all(s["status"] == "queued" for s in run["sources"]))

    def test_unknown_report_fields_and_private_config_are_not_exposed(self):
        models.create_run(run_id="minimal-report", created_by=self.uid, sources=["boe"])
        models.finalize_run(
            "minimal-report",
            status="error",
            summary={
                "ingest": {"ingested": 0},
                "debug_config": {"token": "test-marker-not-for-ui"},
                "sources": [
                    {
                        "source_id": "boe",
                        "status": "error",
                        "limitations": [
                            {"reason": "Access failed", "password": "test-marker-not-for-ui"}
                        ],
                    }
                ],
            },
        )
        run = self.client.get("/api/runs").get_json()["runs"][0]
        self.assertNotIn("test-marker-not-for-ui", json.dumps(run))
        self.assertIsInstance(run["duration_seconds"], (int, float))
