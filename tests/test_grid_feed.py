from auctionradar.web import models
from tests.test_webapp_auth import _WebappFixture


class GridFeedTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.make_user(username="owner")
        self.login(username="owner")
        for number in range(61):
            data = {
                "source_id": "boe",
                "asset_type": "housing",
                "title": "Synthetic " + str(number),
                "prices": {"asking": number + 1},
            }
            models.upsert_item(
                item_id="fixture-" + str(number),
                asset_type="housing",
                source_id="boe",
                source_ids=["boe"],
                data=data,
                first_seen="2026-10-18T07:00:00+00:00",
                last_seen="2026-10-18T07:00:00+00:00",
                listing_url="https://example.org/" + str(number),
                raw_fingerprint=str(number),
            )

    def page(self, offset=0, status="pending"):
        response = self.client.get(
            "/api/items?asset=housing&status=" + status + "&sort=price_asc&offset=" + str(offset)
        )
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_offset_feed_is_bounded_and_globally_sorted(self):
        rows = []
        offset = 0
        for count in (25, 25, 11):
            response = self.page(offset)
            self.assertEqual(len(response["items"]), count)
            self.assertEqual(response["offset"], offset)
            rows.extend(response["items"])
            offset = response["next_offset"]
        self.assertFalse(response["has_more"])
        self.assertEqual([row["display_price"] for row in rows], list(range(1, 62)))
        self.assertEqual(len({row["id"] for row in rows}), 61)

    def test_removal_uses_adjusted_offset_without_skipping_next_row(self):
        first = self.page()
        target = first["items"][10]["id"]
        response = self.api_call(
            "POST",
            "/api/items/" + target + "/review",
            json={"status": "ok", "preserve_notes": True},
        )
        self.assertEqual(response.status_code, 200)
        second = self.page(first["next_offset"] - 1)
        self.assertEqual(second["items"][0]["display_price"], 26)
        self.assertNotIn(target, [row["id"] for row in second["items"]])
        self.assertEqual(self.page(status="all")["total"], 61)
        self.assertEqual(self.page()["total"], 60)

    def test_invalid_offsets_fail_closed(self):
        for value in ("-1", "oops", "1.5", "1000001"):
            self.assertEqual(self.client.get("/api/items?offset=" + value).status_code, 400)

    def test_legacy_page_clients_remain_compatible(self):
        legacy = self.client.get(
            "/api/items?asset=housing&status=pending&sort=price_asc&page=2"
        ).get_json()
        self.assertEqual(
            [row["id"] for row in legacy["items"]], [row["id"] for row in self.page(25)["items"]]
        )
