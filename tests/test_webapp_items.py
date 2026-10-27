"""Tests for the items listing, item detail and filter contract."""

from __future__ import annotations

import unittest

import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


class ItemsListingTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)
        self.login(username="alice")

    def _store_items(self):
        items = [
            {
                "asset_type": "housing",
                "source_id": "boe",
                "url": "https://example.org/housing/1",
                "reference": "R-1",
                "title": "Piso en Asturias",
                "prices": {"asking": 100000},
                "possession": {"status": "vacant"},
                "availability": {"status": "available"},
                "right_sold": None,
                "selection": {"status": "candidate", "reasons": []},
                "location": {"province": "Asturias"},
            },
            {
                "asset_type": "housing",
                "source_id": "aliseda",
                "url": "https://example.org/housing/2",
                "reference": "R-2",
                "title": "Piso en Madrid",
                "prices": {"asking": 250000},
                "possession": {"status": "vacant"},
                "availability": {"status": "available"},
                "right_sold": None,
                "selection": {"status": "candidate", "reasons": []},
                "location": {"province": "Madrid"},
            },
            {
                "asset_type": "vehicles",
                "source_id": "ayvens",
                "url": "https://example.org/vehicles/1",
                "reference": "V-1",
                "title": "Ford Transit",
                "prices": {"asking": 20000},
                "possession": None,
                "availability": {"status": "available"},
                "mileage_km": 120000,
                "right_sold": None,
                "selection": {"status": "candidate", "reasons": []},
            },
        ]
        keys = []
        for item in items:
            key = ingest.stable_item_key(item)
            models.upsert_item(
                item_id=key,
                asset_type=item["asset_type"],
                source_id=item["source_id"],
                source_ids=[item["source_id"]],
                first_seen="2026-10-17T16:00:00+00:00",
                last_seen="2026-10-17T16:00:00+00:00",
                data=item,
                listing_url=item["url"],
                raw_fingerprint=ingest.substantive_fingerprint(item),
            )
            keys.append(key)
        return keys, items

    def test_filters_by_asset_housing(self):
        self._store_items()
        response = self.client.get("/api/items?asset=housing")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["total"], 2)
        for item in body["items"]:
            self.assertEqual(item["asset_type"], "housing")

    def test_filters_by_asset_vehicles(self):
        self._store_items()
        response = self.client.get("/api/items?asset=vehicles")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["asset_type"], "vehicles")

    def test_status_filter_pending(self):
        keys, _items = self._store_items()
        # Mark second housing item as ``ko``.
        models.add_review(
            item_id=keys[1],
            user_id=self.uid,
            status="ko",
            notes="",
            snapshot={"prices": {"asking": 250000}},
        )
        response = self.client.get("/api/items?status=ko&asset=housing")
        body = response.get_json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["items"][0]["review"]["status"], "ko")

    def test_q_filter_matches_title(self):
        self._store_items()
        response = self.client.get("/api/items?q=Madrid")
        body = response.get_json()
        self.assertEqual(body["total"], 1)
        self.assertIn("Madrid", body["items"][0]["title"])

    def test_changed_only_filter(self):
        keys, _items = self._store_items()
        # Pretend a substantive change happened after the last review.
        models.update_change_flag(keys[0], price=True, possession=False, availability=False)
        response = self.client.get("/api/items?changed=1")
        body = response.get_json()
        self.assertEqual(body["total"], 1)
        self.assertTrue(body["items"][0]["changed_since_review"])

    def test_counts_include_aggregated(self):
        self._store_items()
        response = self.client.get("/api/items")
        body = response.get_json()
        for key in ("pending", "ok", "ko", "changed"):
            self.assertIn(key, body["counts"])

    def test_listing_does_not_leak_notes_only_summary(self):
        self._store_items()
        body = self.client.get("/api/items?asset=housing").get_json()
        for item in body["items"]:
            self.assertIn("review", item)
            self.assertIn("notes", item["review"])
            # The big raw listing is exposed under ``data``; it should
            # not contain notes (those are reviewer notes, not data).
            self.assertNotIn("notes", item["data"])

    def test_invalid_asset_returns_400(self):
        response = self.client.get("/api/items?asset=spaceship")
        self.assertEqual(response.status_code, 400)

    def test_pagination(self):
        self._store_items()
        body = self.client.get("/api/items?page=1").get_json()
        self.assertEqual(body["page"], 1)
        self.assertGreaterEqual(body["pages"], 1)

    def test_unauthenticated_returns_401(self):
        # Drop the active session entirely.
        with self.client.session_transaction() as sess:
            sess.clear()
        self.client.set_cookie("session", "")
        response = self.client.get("/api/items")
        self.assertEqual(response.status_code, 401)


class ItemDetailTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)
        self.login(username="alice")

    def test_item_detail_returns_review_history_and_shares(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/9",
            "reference": "R-9",
            "title": "Casa rural",
            "prices": {"asking": 80000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
            "photos": [
                {"url": "https://example.org/photo/1.jpg", "image_type": "real"},
                {"url": "javascript:alert(1)", "image_type": "real"},
            ],
            "documents": [
                {"url": "https://example.org/doc.pdf", "read_status": "text_extracted"},
                {"url": "data:text/html,x", "read_status": "text_extracted"},
            ],
        }
        key = ingest.stable_item_key(item)
        models.upsert_item(
            item_id=key,
            asset_type=item["asset_type"],
            source_id=item["source_id"],
            source_ids=[item["source_id"]],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-17T16:00:00+00:00",
            data=item,
            listing_url=item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(item),
        )
        models.add_review(
            item_id=key,
            user_id=self.uid,
            status="pending",
            notes="revisar",
            snapshot={"prices": item["prices"]},
        )
        response = self.client.get(f"/api/items/{key}")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["item"]["id"], key)
        self.assertEqual(body["item"]["title"], "Casa rural")
        # Bad photo / document URLs were filtered out.
        photos = body["item"]["photos"]
        self.assertTrue(all(p["url"].startswith("http") for p in photos))
        self.assertFalse(any("javascript:" in p["url"] for p in photos))
        docs = body["item"]["documents"]
        self.assertTrue(all(d["url"].startswith("http") for d in docs))

    def test_unknown_item_returns_404(self):
        response = self.client.get("/api/items/no-such-item")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
