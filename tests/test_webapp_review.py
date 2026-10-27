"""Review / KO / change-since-review tests.

Review persistence contract:

* a *KO* review must persist even if other fields change;
* ``changed_since_review`` is set when substantive fields (price,
  possession, availability, title, right_sold) differ from the
  snapshot stored with the review;
* volatile fields (timestamps, observations) do **not** trigger the
  flag;
* an item that disappears from a later run is preserved (absence
  never means sold); the latest accepted review is still surfaced in
  the item detail.
"""

from __future__ import annotations

import json
import unittest

import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


class ReviewAndChangeTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")

    def _store_item(self, **overrides):
        item = {
            "id": overrides.get("id", "stable-key"),
            "asset_type": "housing",
            "source_id": overrides.get("source_id", "boe"),
            "url": overrides.get("url", "https://example.com/house/123"),
            "reference": "REF-123",
            "title": overrides.get("title", "Piso en Asturias"),
            "prices": overrides.get(
                "prices",
                {
                    "asking": 100000,
                    "auction_value": 120000,
                    "minimum_bid": 50000,
                },
            ),
            "possession": overrides.get("possession", {"status": "vacant"}),
            "availability": overrides.get("availability", {"status": "available"}),
            "right_sold": overrides.get("right_sold", None),
            "selection": overrides.get("selection", {"status": "candidate", "reasons": []}),
        }
        key = ingest.stable_item_key(item)
        existing = models.get_item(key)
        bool(existing and existing["latest_review_id"])
        first_seen = existing["first_seen"] if existing else "2026-10-17T16:00:00+00:00"
        models.upsert_item(
            item_id=key,
            asset_type=item["asset_type"],
            source_id=item["source_id"],
            source_ids=[item["source_id"]],
            first_seen=first_seen,
            last_seen="2026-10-17T16:00:00+00:00",
            data=item,
            listing_url=item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(item),
        )
        return key, item

    def _review(self, item_id, status, notes="nota"):
        response = self.api_call(
            "POST",
            f"/api/items/{item_id}/review",
            json={"status": status, "notes": notes},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()

    def test_ko_review_persists_across_volatile_changes(self):
        key, item = self._store_item()
        payload = self._review(key, "ko", notes="estado dudoso")
        self.assertEqual(payload["item"]["review"]["status"], "ko")

        # Volatile-only update (observed_at, raw_path) must NOT set the
        # ``changed_since_review`` flag.
        volatile_update = dict(item)
        volatile_update["observed_at"] = "2026-10-18T08:00:00+00:00"
        volatile_update["raw_path"] = "raw/20261018"
        models.upsert_item(
            item_id=key,
            asset_type=volatile_update["asset_type"],
            source_id=volatile_update["source_id"],
            source_ids=[volatile_update["source_id"]],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-18T08:00:00+00:00",
            data=volatile_update,
            listing_url=volatile_update["url"],
            raw_fingerprint=ingest.substantive_fingerprint(volatile_update),
        )
        row = models.get_item(key)
        self.assertFalse(row["changed_since_review"], "volatile change must not flip the flag")
        self.assertEqual(models.latest_review_for_item(key)["status"], "ko")

    def test_ko_review_flags_price_change(self):
        key, item = self._store_item()
        self._review(key, "ko")
        # Substantive change: price drops.
        new_item = dict(item)
        new_item["prices"] = {"asking": 95000, "auction_value": 120000, "minimum_bid": 50000}
        models.upsert_item(
            item_id=key,
            asset_type=new_item["asset_type"],
            source_id=new_item["source_id"],
            source_ids=[new_item["source_id"]],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-18T08:00:00+00:00",
            data=new_item,
            listing_url=new_item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(new_item),
        )
        models.update_change_flag(key, price=True, possession=False, availability=False)
        row = models.get_item(key)
        self.assertTrue(row["price_changed_since_review"])
        self.assertTrue(row["changed_since_review"])
        # The KO verdict is preserved.
        self.assertEqual(models.latest_review_for_item(key)["status"], "ko")

    def test_ko_review_flags_possession_change(self):
        key, item = self._store_item()
        self._review(key, "ko")
        new_item = dict(item)
        new_item["possession"] = {"status": "occupied"}
        models.upsert_item(
            item_id=key,
            asset_type=new_item["asset_type"],
            source_id=new_item["source_id"],
            source_ids=[new_item["source_id"]],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-18T08:00:00+00:00",
            data=new_item,
            listing_url=new_item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(new_item),
        )
        models.update_change_flag(key, price=False, possession=True, availability=False)
        row = models.get_item(key)
        self.assertTrue(row["posession_changed_since_review"])
        self.assertTrue(row["changed_since_review"])

    def test_acknowledged_review_resets_flag(self):
        key, item = self._store_item()
        self._review(key, "ko")
        # Pretend the listing changed; re-acknowledge with ``pending``.
        new_item = dict(item)
        new_item["prices"]["asking"] = 80000
        models.upsert_item(
            item_id=key,
            asset_type=new_item["asset_type"],
            source_id=new_item["source_id"],
            source_ids=[new_item["source_id"]],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-18T08:00:00+00:00",
            data=new_item,
            listing_url=new_item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(new_item),
        )
        models.update_change_flag(key, price=True, possession=False, availability=False)
        self.assertTrue(models.get_item(key)["changed_since_review"])
        # Now acknowledge via a new review.
        self._review(key, "pending", notes="revisar de nuevo")
        self.assertFalse(models.get_item(key)["changed_since_review"])

    def test_absence_does_not_flip_status(self):
        """If a second run doesn't include the item, the stored KO remains KO."""
        key, item = self._store_item()
        self._review(key, "ko")
        # Run again with no items.
        run = {
            "run_id": "empty",
            "items": [],
        }
        history = self.data_dir / "empty.json"
        history.write_text(json.dumps(run), encoding="utf-8")
        summary = ingest.ingest_run(self.data_dir, run_id="empty")
        self.assertEqual(summary["skipped"], 0)
        row = models.get_item(key)
        self.assertIsNotNone(
            row, "previously known items must remain accessible after a missed run"
        )
        self.assertEqual(models.latest_review_for_item(key)["status"], "ko")
        # We never auto-flip to "sold" — the row still exists with KO review.
        self.assertFalse(row["changed_since_review"])

    def test_item_id_stable_across_internal_id_rename(self):
        """A portal renumbering (id changes but URL stays) keeps the row."""
        first, item = self._store_item()
        # Same URL, new internal id from the same source.
        renamed = dict(item)
        renamed["id"] = "new-portal-id"
        new_key = ingest.stable_item_key(renamed)
        # The key derived from URL+reference must be identical to the
        # original — the deduplication must rely on identity keys, not
        # the inner portal id.
        self.assertEqual(first, new_key)

    def test_review_event_history_returned(self):
        key, item = self._store_item()
        self._review(key, "ko", notes="rechazado")
        self._review(key, "pending", notes="revisit")
        response = self.client.get(f"/api/items/{key}")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["history"]), 2)
        self.assertEqual(data["item"]["review"]["status"], "pending")


class ReviewValidationTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")

    def test_invalid_status_returns_400(self):
        # Insert one item first.
        key, _ = self._store_item()
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "maybe", "notes": ""},
        )
        self.assertEqual(response.status_code, 400)

    def test_unknown_item_returns_404(self):
        response = self.api_call(
            "POST",
            "/api/items/does-not-exist/review",
            json={"status": "ok", "notes": ""},
        )
        self.assertEqual(response.status_code, 404)

    def test_non_string_notes_rejected(self):
        key, _ = self._store_item()
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "notes": 123},
        )
        self.assertEqual(response.status_code, 400)

    def _store_item(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/property/9",
            "reference": "REF-9",
            "title": "Piso barato",
            "prices": {"asking": 100000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        key = ingest.stable_item_key(item)
        models.upsert_item(
            item_id=key,
            asset_type="housing",
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-17T16:00:00+00:00",
            data=item,
            listing_url=item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(item),
        )
        return key, item


if __name__ == "__main__":
    unittest.main()
