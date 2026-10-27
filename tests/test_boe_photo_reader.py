import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auctionradar.connectors.boe.client import BoeAccessError
from auctionradar.connectors.boe.photo_reader import collect, read_json


class FakeClient:
    def login(self):
        self.logged_in = True

    def iter_asset_photos(self, url, **kwargs):
        yield {
            "data": b"\xff\xd8\xff\xe0synthetic-photo",
            "content_type": "image/jpeg",
            "caption": "Foto 1",
            "source_url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST&ver=3",
        }


class CacheReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.export = self.root / "export"
        self.export.mkdir()
        self.requests = self.root / "requests.json"
        self.requests.write_text(
            json.dumps(
                {
                    "version": 1,
                    "requests": [
                        {
                            "item_id": "fixture-one",
                            "url": "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST",
                        }
                    ],
                }
            )
        )

    def run_collect(self, client=None):
        with patch("auctionradar.connectors.boe.photo_reader.time.sleep"):
            return collect(client or FakeClient(), self.requests, self.export)

    def test_real_cache_writer_publishes_only_sanitised_manifest_and_images(self):
        result = self.run_collect()
        self.assertEqual(result["photos"], 1)
        text = (self.export / "manifest.json").read_text()
        payload = json.loads(text)
        photo = payload["items"]["fixture-one"]["photos"][0]
        data = (self.export / photo["file"]).read_bytes()
        self.assertEqual(photo["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(photo["size"], len(data))
        self.assertRegex(photo["media_id"], r"^[a-f0-9]{32}$")
        self.assertEqual((self.export / photo["file"]).stat().st_mode & 0o777, 0o640)
        for secret in ("PHPSESSID", "idDoc", "BEGIN PRIVATE", "password", "cookie"):
            self.assertNotIn(secret, text)

    def test_transient_failure_keeps_previous_manifest_entry(self):
        self.run_collect()
        before = json.loads((self.export / "manifest.json").read_text())["items"]

        class Failed(FakeClient):
            def iter_asset_photos(self, *args, **kwargs):
                raise BoeAccessError("asset_login_required")

        result = self.run_collect(Failed())
        self.assertEqual(result["errors"], 1)
        after = json.loads((self.export / "manifest.json").read_text())["items"]
        self.assertEqual(after["fixture-one"].pop("last_error"), "asset_login_required")
        self.assertEqual(before, after)
        self.assertEqual(result["error_codes"], {"asset_login_required": 1})

    def test_empty_gallery_does_not_delete_known_photographs(self):
        self.run_collect()
        before = json.loads((self.export / "manifest.json").read_text())["items"]

        class Empty(FakeClient):
            def iter_asset_photos(self, *args, **kwargs):
                return iter(())

        self.run_collect(Empty())
        self.assertEqual(before, json.loads((self.export / "manifest.json").read_text())["items"])

    def test_repeated_collection_uses_same_media_ids(self):
        self.run_collect()
        self.run_collect()
        self.assertEqual(len(list((self.export / "media").glob("*.jpg"))), 1)

    def test_symlinked_requests_rejected(self):
        link = self.root / "link.json"
        link.symlink_to(self.requests)
        with self.assertRaises(OSError):
            read_json(link, 1024)

    def test_fifo_rejected_without_waiting(self):
        path = self.root / "pipe"
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            read_json(path, 1024)

    def test_symlinked_output_directory_rejected(self):
        target = self.root / "outside"
        target.mkdir()
        (self.export / "media").symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.run_collect()


if __name__ == "__main__":
    unittest.main()
