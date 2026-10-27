"""Contract tests for the detail-view review autosave.

The detail-view status radios (``[data-review-status]``) now persist on
click with ``preserve_notes: true`` so that they behave like the grid
quick actions (OK/KO) and like ``Guardar decisión`` (which sends both
status and notes). These tests guard the atomic API contract the
client-side autosave depends on:

* a status-only POST with ``preserve_notes: true`` never clobbers the
  notes that other users (or the same user in a different tab) have
  stored against the item;
* the existing ``preserve_notes`` semantics are independent from the
  status field, so the next ``Guardar decisión`` call still saves
  notes normally;
* invalid statuses still return ``400`` and never mutate the row;
* a failed review leaves the row unchanged (the client relies on this
  to roll the radios back to the persisted status without removing the
  card from the grid).
"""

from __future__ import annotations

import unittest

import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


def _store_item(
    item_id: str = "stable-key", asset_type: str = "housing", title: str = "Piso en Asturias"
) -> tuple[str, dict]:
    """Insert a minimal item row directly through the models layer."""
    item = {
        "id": item_id,
        "asset_type": asset_type,
        "source_id": "boe",
        "url": f"https://example.com/{asset_type}/{item_id}",
        "reference": f"REF-{item_id}",
        "title": title,
        "prices": {"asking": 100000, "auction_value": 120000, "minimum_bid": 50000},
        "possession": {"status": "vacant"},
        "availability": {"status": "available"},
        "right_sold": None,
        "selection": {"status": "candidate", "reasons": []},
    }
    key = ingest.stable_item_key(item)
    existing = models.get_item(key)
    first_seen = existing["first_seen"] if existing else "2026-10-17T16:00:00+00:00"
    models.upsert_item(
        item_id=key,
        asset_type=asset_type,
        source_id="boe",
        source_ids=["boe"],
        first_seen=first_seen,
        last_seen="2026-10-17T16:00:00+00:00",
        data=item,
        listing_url=item["url"],
        raw_fingerprint=ingest.substantive_fingerprint(item),
    )
    return key, item


class DetailReviewContractTests(_WebappFixture):
    """Contract for the POST /api/items/<id>/review endpoint as the
    detail-view autosave relies on it."""

    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")

    def test_preserve_notes_only_changes_status(self):
        key, _ = _store_item()
        # Initial rich note that another user could have added in another
        # tab. The detail radios must never overwrite it.
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ko", "notes": "Otro agente: necesita fotos reales"},
        )
        # Detail radio click: only status changes; notes preserved.
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "preserve_notes": True},
        )
        self.assertEqual(response.status_code, 200, response.data)
        item = response.get_json()["item"]
        self.assertEqual(item["review"]["status"], "ok")
        self.assertEqual(
            item["review"]["notes"],
            "Otro agente: necesita fotos reales",
            "preserve_notes must not overwrite notes with an empty string",
        )

    def test_preserve_notes_keeps_latest_concurrent_notes(self):
        key, _ = _store_item()
        # Initial save with notes.
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "pending", "notes": "Borrador inicial"},
        )
        # Simulate a second tab: it saves new notes while the first tab
        # only flips the status with preserve_notes.
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ko", "notes": "Borrador reescrito por otra pestaña"},
        )
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "preserve_notes": True},
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["item"]
        self.assertEqual(item["review"]["status"], "ok")
        self.assertEqual(
            item["review"]["notes"],
            "Borrador reescrito por otra pestaña",
            "preserve_notes must keep the most recent note from the DB",
        )

    def test_explicit_save_decision_overwrites_notes(self):
        """Guardar decisión must keep its previous semantics: the user
        is explicitly saving them, so notes are replaced."""
        key, _ = _store_item()
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ko", "notes": "antiguo"},
        )
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "notes": "nuevo"},
        )
        self.assertEqual(response.status_code, 200)
        item = response.get_json()["item"]
        self.assertEqual(item["review"]["status"], "ok")
        self.assertEqual(item["review"]["notes"], "nuevo")

    def test_invalid_status_returns_400_without_mutating(self):
        key, _ = _store_item()
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "pending", "notes": "snapshot"},
        )
        bad = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "maybe", "preserve_notes": True},
        )
        self.assertEqual(bad.status_code, 400)
        # Row is unchanged: status, notes, updated_at (last_review_id).
        models.get_item(key)
        review = models.latest_review_for_item(key)
        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["notes"], "snapshot")

    def test_preserve_notes_for_unknown_item_returns_404(self):
        response = self.api_call(
            "POST",
            "/api/items/does-not-exist/review",
            json={"status": "ok", "preserve_notes": True},
        )
        self.assertEqual(response.status_code, 404)

    def test_preserve_notes_clears_change_flags(self):
        """A review (status only or status+notes) must clear the
        substantive change flags so the card stops showing "Cambios
        desde revisión". The autosave contract depends on this so the
        grid reflects a clean state once the user acknowledges."""
        key, _ = _store_item()
        # First review (KO with notes) acknowledges the listing.
        self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ko", "notes": "revisar precio"},
        )
        # Pretend a substantive change happened afterwards.
        new_item = dict(_store_item(key)[1])
        new_item["prices"]["asking"] = 80000
        models.upsert_item(
            item_id=key,
            asset_type=new_item["asset_type"],
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-17T16:00:00+00:00",
            last_seen="2026-10-18T08:00:00+00:00",
            data=new_item,
            listing_url=new_item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(new_item),
        )
        models.update_change_flag(key, price=True, possession=False, availability=False)
        self.assertTrue(models.get_item(key)["changed_since_review"])
        # The user only flips the status from the drawer (no notes change).
        response = self.api_call(
            "POST",
            f"/api/items/{key}/review",
            json={"status": "ok", "preserve_notes": True},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(models.get_item(key)["changed_since_review"])


class DetailReviewApiFilterTests(_WebappFixture):
    """The grid only disappears from a ``status=pending`` filter when
    the server actually has a non-pending review for that item. These
    tests confirm the server-side filter that the drawer relies on
    after the autosave POST returns."""

    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)
        self.login(username="alice")
        self.key, _ = _store_item()

    def test_pending_filter_hides_item_after_ko(self):
        self.api_call("POST", f"/api/items/{self.key}/review", json={"status": "ko", "notes": "x"})
        pending = self.client.get("/api/items?status=pending&asset=all")
        ids = [i["id"] for i in pending.get_json()["items"]]
        self.assertNotIn(self.key, ids)
        ko = self.client.get("/api/items?status=ko&asset=all")
        ids = [i["id"] for i in ko.get_json()["items"]]
        self.assertIn(self.key, ids)

    def test_pending_filter_hides_item_after_ok(self):
        self.api_call("POST", f"/api/items/{self.key}/review", json={"status": "ok", "notes": "y"})
        pending = self.client.get("/api/items?status=pending&asset=all")
        ids = [i["id"] for i in pending.get_json()["items"]]
        self.assertNotIn(self.key, ids)
        ok = self.client.get("/api/items?status=ok&asset=all")
        ids = [i["id"] for i in ok.get_json()["items"]]
        self.assertIn(self.key, ids)

    def test_counts_reflect_status_change(self):
        # Baseline: 1 pending.
        baseline = self.client.get("/api/items?status=all&asset=all").get_json()
        self.assertEqual(baseline["counts"]["pending"], 1)
        self.assertEqual(baseline["counts"]["ko"], 0)
        # Flip to KO via preserve_notes.
        self.api_call(
            "POST",
            f"/api/items/{self.key}/review",
            json={"status": "ko", "preserve_notes": True},
        )
        after = self.client.get("/api/items?status=all&asset=all").get_json()
        self.assertEqual(after["counts"]["pending"], 0)
        self.assertEqual(after["counts"]["ko"], 1)


if __name__ == "__main__":
    unittest.main()
