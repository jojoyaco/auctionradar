"""Safety checks for the fictional-content, test-only demo preview."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from tests.demo_preview import (
    ARTWORK_SCENES,
    SOURCE_ID,
    configure_preview_environment,
    fixture_items,
    illustration_svg,
    should_block_job,
)


class DemoPreviewSafetyTests(unittest.TestCase):
    def test_fixtures_are_fictional_and_images_are_same_origin(self):
        origin = "http://127.0.0.1:8769"
        items = fixture_items(origin)

        self.assertEqual(len(items), 5)
        self.assertEqual({item["source_id"] for item in items}, {SOURCE_ID})
        self.assertEqual(len({item["id"] for item in items}), len(items))
        for item in items:
            self.assertTrue(item["title"].startswith("DEMO · "))
            self.assertEqual(item["facts_scope"], "synthetic_demo_fixture")
            self.assertTrue(item["url"].startswith("https://example.invalid/"))
            self.assertEqual(item["location"]["province"], "Provincia ficticia")
            self.assertGreaterEqual(len(item["photos"]), 1)
            for photo in item["photos"]:
                self.assertTrue(photo["url"].startswith(origin + "/__demo/illustration/"))
                self.assertIn("no es una fotografía", photo["caption"])
                self.assertEqual(photo["image_type"], "synthetic_illustration")
                self.assertEqual(photo["origin"], "local_synthetic_fixture")

    def test_original_svg_art_has_visible_synthetic_label_and_no_remote_assets(self):
        for scene in ARTWORK_SCENES:
            with self.subTest(scene=scene):
                markup = illustration_svg(scene)
                self.assertIn('<svg xmlns="http://www.w3.org/2000/svg"', markup)
                self.assertIn("SYNTHETIC DEMO", markup)
                self.assertIn("ORIGINAL VECTOR ILLUSTRATION · NOT A PHOTOGRAPH", markup)
                self.assertNotIn("https://", markup)
                self.assertNotIn("<image", markup)
                self.assertNotIn("<script", markup)
                self.assertNotIn("href=", markup)

    def test_preview_environment_discards_inherited_operator_paths_and_secrets(self):
        original = dict(os.environ)
        try:
            os.environ["AUCTIONRADAR_SECRET_KEY"] = "must-not-survive-preview-setup"
            os.environ["AUCTIONRADAR_AI_API_KEY"] = "must-not-survive-preview-setup"
            os.environ["AUCTIONRADAR_DATA_DIR"] = "/not-a-demo-workspace"
            os.environ["CREDENTIALS_DIRECTORY"] = "/not-a-demo-credentials-dir"

            configure_preview_environment(Path("/tmp/auctionradar-demo-test"))

            self.assertNotIn("AUCTIONRADAR_SECRET_KEY", os.environ)
            self.assertNotIn("AUCTIONRADAR_AI_API_KEY", os.environ)
            self.assertNotIn("CREDENTIALS_DIRECTORY", os.environ)
            self.assertEqual(os.environ["AUCTIONRADAR_DATA_DIR"], "/tmp/auctionradar-demo-test")
            self.assertEqual(os.environ["AUCTIONRADAR_WORKSPACE_MODE"], "private")
            self.assertEqual(os.environ["AUCTIONRADAR_AI_PROVIDER"], "disabled")
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_preview_disables_run_and_saved_search_job_mutations(self):
        blocked = (
            ("/api/runs", "POST"),
            ("/api/runs", "DELETE"),
            ("/api/searches", "POST"),
            ("/api/searches/12", "PATCH"),
            ("/api/searches/12/evaluate", "POST"),
        )
        for path, method in blocked:
            with self.subTest(path=path, method=method):
                self.assertTrue(should_block_job(path, method))

        allowed = (
            ("/api/runs", "GET"),
            ("/api/searches", "GET"),
            ("/api/items/synthetic-item/review", "POST"),
            ("/__demo/seed", "POST"),
        )
        for path, method in allowed:
            with self.subTest(path=path, method=method):
                self.assertFalse(should_block_job(path, method))


if __name__ == "__main__":
    unittest.main()
