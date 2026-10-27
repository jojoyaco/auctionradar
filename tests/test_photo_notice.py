import json

from auctionradar.web import models
from tests.test_webapp_auth import _WebappFixture


class PhotoNoticeTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner")
        self.login(username="owner")

    def insert(self, limitation):
        data = {
            "id": "boe-photos",
            "title": "Buque de prueba",
            "asset_type": "boats",
            "source_id": "boe",
            "url": "https://subastas.boe.es/detalleSubasta.php?idSub=TEST",
            "photos": [],
            "enrichment_limitations": [limitation],
            "published_text": "private diagnostics not for public API",
        }
        models.upsert_item(
            item_id="boe-photos",
            asset_type="boats",
            source_id="boe",
            source_ids=["boe"],
            first_seen="now",
            last_seen="now",
            data=data,
            listing_url=data["url"],
            raw_fingerprint="test",
        )

    def test_specific_boe_auth_notice_is_public_without_internal_diagnostics(self):
        self.insert(
            {
                "reason": "boe_gallery_behind_authentication",
                "url": "https://subastas.boe.es/detalleSubasta.php?idSub=TEST&ver=3",
                "raw_path": "/private/secret-path",
            }
        )
        result = self.client.get("/api/items/boe-photos").get_json()
        self.assertEqual(result["item"]["photo_notice"]["code"], "boe_login_required")
        self.assertNotIn("secret-path", json.dumps(result))
        share = self.api_call("POST", "/api/items/boe-photos/share", json={}).get_json()
        public = self.client.get("/api/public/" + share["opaque"]).get_json()
        self.assertEqual(public["item"]["photo_notice"]["code"], "boe_login_required")
        self.assertNotIn("private diagnostics", json.dumps(public))

    def test_notice_cannot_send_readers_to_arbitrary_hosts(self):
        self.insert(
            {"reason": "boe_gallery_behind_authentication", "url": "https://example.org/steal"}
        )
        result = self.client.get("/api/items/boe-photos").get_json()
        self.assertIsNone(result["item"]["photo_notice"])
