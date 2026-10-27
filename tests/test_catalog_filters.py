"""Global ordering and filtering before pagination; unknown categories and prices."""

from auctionradar.web import models
from tests.test_webapp_auth import _WebappFixture


class CatalogFilterTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner")
        self.login(username="owner")

    def add(
        self, index, price=None, status=None, title="Piso de prueba", kind="housing", location=None
    ):
        item_id = str(index)
        data = {
            "id": item_id,
            "asset_type": kind,
            "title": title,
            "url": "https://example.org/item/" + item_id,
            "source_id": "boe",
            "prices": {"asking": price},
            "location": location,
            "photos": [],
        }
        models.upsert_item(
            item_id=item_id,
            asset_type=kind,
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-17T07:00:00+00:00",
            last_seen="2026-10-17T07:00:00+00:00",
            data=data,
            listing_url=data["url"],
            raw_fingerprint="x",
        )
        if status:
            models.add_review(
                item_id=item_id, user_id=self.uid, status=status, notes="", snapshot={}
            )

    def test_price_order_is_global_and_unknown_is_last_both_directions(self):
        for i, p in enumerate([None, 50000, 2000, 10000]):
            self.add(i, p)
        for sort, expected in [
            ("price_asc", [2000, 10000, 50000, None]),
            ("price_desc", [50000, 10000, 2000, None]),
        ]:
            result = self.client.get("/api/items?status=all&sort=" + sort).get_json()
            self.assertEqual([i["display_price"] for i in result["items"]], expected)

    def test_status_filter_is_applied_before_pagination(self):
        for i in range(30):
            self.add(i, 10000, status="ko" if i < 25 else None)
        result = self.client.get("/api/items?status=pending").get_json()
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["items"]), 5)

    def test_boats_and_price_region_filters(self):
        self.add(
            1, 12000, title="Lancha de recreo", kind="boats", location={"municipality": "Ávila"}
        )
        self.add(
            2, 30000, title="Lancha de recreo", kind="boats", location={"municipality": "Madrid"}
        )
        self.add(3, 10000)
        result = self.client.get(
            "/api/items?category=boats&price_max=15000&location=avila"
        ).get_json()
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["category"], "boats")

    def test_last_updated_is_not_the_last_failed_attempt(self):
        self.add(1, 10000)
        before = self.client.get("/api/me").get_json()["last_updated_at"]
        models.create_run(run_id="failed-attempt", created_by=self.uid, sources=["boe"])
        models.finalize_run("failed-attempt", status="error", error="test")
        self.assertEqual(self.client.get("/api/me").get_json()["last_updated_at"], before)

    def test_invalid_sort_and_price_rejected(self):
        for query in (
            "sort=arbitrary",
            "price_min=nan",
            "price_min=50&price_max=2",
            "category=not-real",
        ):
            self.assertEqual(self.client.get("/api/items?" + query).status_code, 400)
