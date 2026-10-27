"""Sharing + public endpoint tests.

Verifies:

* the ``/api/items/<id>/share`` endpoint returns an opaque token;
* ``/api/shares/<id>/revoke`` removes the link for everyone, including
  holders who previously opened the URL;
* the ``/share/<token>`` page is openable *without* login;
* ``/api/public/<token>`` exposes only the safe listing payload — no
  notes, no review histories, no internal config or keys.
"""

from __future__ import annotations

import unittest

import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


class ShareTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=False)
        self.login(username="alice")

    def _store_item(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/77",
            "reference": "R-77",
            "title": "Casa compartida",
            "prices": {"asking": 90000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
            "secret_notes": "no debería filtrarse",
            "extra_field": "internal-only",
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
            status="ko",
            notes="rechazado por estado dudoso, secreto",
            snapshot={"prices": item["prices"]},
        )
        return key

    def test_share_returns_opaque_token(self):
        key = self._store_item()
        response = self.api_call("POST", f"/api/items/{key}/share", json={})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("opaque", data)
        self.assertGreater(len(data["opaque"]), 16)

    def test_share_revoke_blocks_public_endpoint(self):
        key = self._store_item()
        created = self.api_call("POST", f"/api/items/{key}/share", json={}).get_json()
        share_id = created["id"]
        # Public endpoint works before revoke.
        public = self.client.get(f"/api/public/{created['opaque']}")
        self.assertEqual(public.status_code, 200)
        # Revoke via API.
        revoke = self.client.post(
            f"/api/shares/{share_id}/revoke", headers={"X-CSRFToken": self.api_csrf_token()}
        )
        self.assertEqual(revoke.status_code, 200)
        after = self.client.get(f"/api/public/{created['opaque']}")
        self.assertEqual(after.status_code, 404)
        # Page route is also rejected.
        page = self.client.get(f"/share/{created['opaque']}")
        self.assertEqual(page.status_code, 404)

    def test_invalid_token_returns_404(self):
        response = self.client.get("/api/public/random-invalid-token-xyz")
        self.assertEqual(response.status_code, 404)

    def test_public_does_not_leak_sensitive_fields(self):
        key = self._store_item()
        created = self.api_call("POST", f"/api/items/{key}/share", json={}).get_json()
        body = self.client.get(f"/api/public/{created['opaque']}").get_json()
        item = body["item"]
        # Extra / secret fields are not exposed.
        self.assertNotIn("notes", item.get("data", {}))
        self.assertNotIn("secret_notes", item.get("data", {}))
        self.assertNotIn("extra_field", item.get("data", {}))
        self.assertNotIn("selection_feedback", item.get("data", {}))
        # The 'review' node must never appear in the public listing.
        self.assertNotIn("review", item)
        # The 'data_json' keys (raw listing) are not included either.
        self.assertNotIn("data_json", item)

    def test_share_page_renders_without_login(self):
        key = self._store_item()
        created = self.api_call("POST", f"/api/items/{key}/share", json={}).get_json()
        # Now drop the active session (logout).
        with self.client.session_transaction() as sess:
            sess.clear()
        page = self.client.get(f"/share/{created['opaque']}")
        self.assertEqual(page.status_code, 200)


if __name__ == "__main__":
    unittest.main()
