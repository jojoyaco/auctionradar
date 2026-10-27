import hashlib
import json
from pathlib import Path

from auctionradar.web import ingest, models
from auctionradar.web.boe_sync import (
    import_export,
    prepare_requests,
    public_asset_url,
    trigger_after_search,
)
from tests.test_webapp_auth import _WebappFixture


class BoeSyncTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner")
        self.login(username="owner")
        self.url = "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-TEST123"
        self.item = {
            "source_id": "boe",
            "asset_type": "housing",
            "title": "Vivienda de prueba",
            "url": self.url,
            "photos": [],
            "prices": {"asking": 90000},
        }
        self.id = ingest.stable_item_key(self.item)
        models.upsert_item(
            item_id=self.id,
            asset_type="housing",
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-17T07:00:00+00:00",
            last_seen="2026-10-17T07:00:00+00:00",
            data=self.item,
            listing_url=self.url,
            raw_fingerprint="test",
        )

    def export(self):
        root = Path(self.data_dir) / "export"
        (root / "media").mkdir(parents=True)
        mid = "a" * 32
        image = b"\xff\xd8\xff\xe0" + b"synthetic-image-data"
        (root / "media" / (mid + ".jpg")).write_bytes(image)
        entry = {
            "media_id": mid,
            "file": "media/" + mid + ".jpg",
            "content_type": "image/jpeg",
            "size": len(image),
            "sha256": hashlib.sha256(image).hexdigest(),
            "source_url": self.url + "&ver=3",
            "caption": "Foto 1",
        }
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "generated_at": "2026-10-18T07:00:00+00:00",
                    "items": {self.id: {"status": "ready", "photos": [entry]}},
                }
            )
        )
        self.app.config["BOE_MEDIA_ROOT"] = str(root)
        return root, mid

    def test_requests_are_public_identifiers_without_notes_or_session_query(self):
        path = Path(self.data_dir) / "requests.json"
        result = prepare_requests(path)
        payload = json.loads(path.read_text())
        self.assertEqual(result["requested"], 1)
        self.assertEqual(set(payload["requests"][0]), {"item_id", "url"})
        self.assertEqual(payload["requests"][0]["url"], self.url + "&ver=3")
        for value in (
            "https://evil.example/detalleSubasta.php?idSub=SUB-AT-2026-TEST",
            "https://subastas.boe.es/reg/pujarSubasta.php?idSub=SUB-AT-2026-TEST",
            "https://subastas.boe.es:bad/detalleSubasta.php?idSub=SUB-AT-2026-TEST",
        ):
            self.assertIsNone(public_asset_url(value))

    def test_import_preserves_review_price_and_observation_dates(self):
        models.add_review(
            item_id=self.id, user_id=self.uid, status="ko", notes="Private team note", snapshot={}
        )
        before = dict(models.get_item(self.id))
        root, mid = self.export()
        result = import_export(root)
        self.assertEqual(result["photos"], 1)
        after = dict(models.get_item(self.id))
        for key in (
            "id",
            "latest_review_id",
            "first_seen",
            "last_seen",
            "price_amount",
            "price_type",
            "changed_since_review",
        ):
            self.assertEqual(before[key], after[key])
        self.assertEqual(models.latest_review_for_item(self.id)["notes"], "Private team note")
        self.assertEqual(models.latest_review_for_item(self.id)["status"], "ko")
        self.assertEqual(after["photo_count"], 1)
        item = self.client.get("/api/items/" + self.id).get_json()["item"]
        self.assertIn("/api/items/" + self.id + "/media/" + mid, item["photos"][0]["url"])
        share = self.api_call("POST", "/api/items/" + self.id + "/share", json={}).get_json()
        public = self.client.get("/api/public/" + share["opaque"]).get_json()["item"]
        self.assertIn(
            "/api/public/" + share["opaque"] + "/media/" + mid, public["photos"][0]["url"]
        )
        self.assertNotIn("Private team note", json.dumps(public))

    def test_public_ingestion_keeps_authorised_cached_photos(self):
        root, mid = self.export()
        import_export(root)
        artifact = Path(self.data_dir) / "new-public.json"
        artifact.write_text(
            json.dumps(
                {
                    "run_id": "new-public",
                    "observed_at": "2026-10-18T08:00:00+00:00",
                    "items": [self.item],
                }
            )
        )
        ingest.ingest_run(Path(self.data_dir), run_id="new-public")
        data = json.loads(models.get_item(self.id)["data_json"])
        self.assertEqual(data["photos"][0]["media_id"], mid)
        self.assertEqual(models.get_item(self.id)["photo_count"], 1)

    def test_corrupt_media_cannot_be_imported(self):
        root, mid = self.export()
        (root / "media" / (mid + ".jpg")).write_bytes(b"<html>login</html>")
        result = import_export(root)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(json.loads(models.get_item(self.id)["data_json"])["photos"], [])

    def test_refresh_signal_contains_only_a_timestamp(self):
        trigger_after_search(self.data_dir)
        value = (Path(self.data_dir) / "boe-photo-refresh.request").read_text()
        self.assertRegex(value, r"^\d{4}-\d{2}-\d{2}T")
        self.assertNotIn("password", value)
