"""Ingestion tests: idempotency, identity stability, vehicle filtering."""

from __future__ import annotations

import json
import unittest

import auctionradar.web.filters as filters
import auctionradar.web.ingest as ingest
import auctionradar.web.models as models
from tests.test_webapp_auth import _WebappFixture


def _make_run(items):
    return {
        "run_id": "20261017-test",
        "observed_at": "2026-10-17T16:00:00+00:00",
        "items": items,
    }


class IngestTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="alice", is_admin=True)

    def _write_run(self, items, run_id="20261017-test"):
        path = self.data_dir / f"{run_id}.json"
        path.write_text(json.dumps(_make_run(items)), encoding="utf-8")
        return self.data_dir

    def test_ingest_idempotent(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/X",
            "reference": "RX-1",
            "title": "Piso en Asturias",
            "prices": {"asking": 100000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        history = self._write_run([item])
        summary_one = ingest.ingest_run(history, run_id="20261017-test")
        summary_two = ingest.ingest_run(history, run_id="20261017-test")
        self.assertEqual(summary_one["ingested"], 1)
        # The second ingestion should recognise the same item and
        # preserve whatever the first one stored.
        self.assertEqual(summary_two["new"], 0)
        self.assertEqual(summary_two["updated"], 1)

    def test_ingest_preserves_ko_review(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/KO",
            "reference": "RK-1",
            "title": "Piso KO",
            "prices": {"asking": 110000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        history = self._write_run([item], run_id="20261017-ko")
        ingest.ingest_run(history, run_id="20261017-ko")
        key = ingest.stable_item_key(item)
        models.add_review(
            item_id=key,
            user_id=self.uid,
            status="ko",
            notes="",
            snapshot={"prices": item["prices"]},
        )
        # Re-ingest the same run; review must survive.
        ingest.ingest_run(history, run_id="20261017-ko")
        review = models.latest_review_for_item(key)
        self.assertEqual(review["status"], "ko")

    def test_vehicle_filter_applied_at_ingest(self):
        models.set_setting(
            "settings",
            {"vehicles": {"max_vehicle_price": 25000, "max_mileage_km": 150000}},
            updated_by=None,
        )
        cheap_vehicle = {
            "asset_type": "vehicles",
            "source_id": "ayvens",
            "url": "https://example.org/vehicles/cheap",
            "reference": "VC-1",
            "title": "Berlingo",
            "prices": {"asking": 18000},
            "possession": None,
            "availability": {"status": "available"},
            "mileage_km": 100000,
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        expensive_vehicle = {
            "asset_type": "vehicles",
            "source_id": "ayvens",
            "url": "https://example.org/vehicles/expensive",
            "reference": "VC-2",
            "title": "Sprinter",
            "prices": {"asking": 60000},
            "possession": None,
            "availability": {"status": "available"},
            "mileage_km": 10000,
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        history = self._write_run([cheap_vehicle, expensive_vehicle], run_id="20261017-veh")
        summary = ingest.ingest_run(history, run_id="20261017-veh")
        # Both items are stored — the budget filter is advisory, not a
        # deletion filter.
        self.assertEqual(summary["ingested"], 2)
        # The filter helpers do classify the second item as out-of-budget.
        settings = filters.VehicleBudgetFilter.from_overlay(
            {"max_vehicle_price": 25000, "max_mileage_km": 150000}
        )
        self.assertTrue(filters.passes_vehicle_filter(cheap_vehicle, settings))
        self.assertFalse(filters.passes_vehicle_filter(expensive_vehicle, settings))

    def test_change_flag_set_when_volumes_change_across_runs(self):
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/CHG",
            "reference": "RC-1",
            "title": "Casa",
            "prices": {"asking": 100000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        history = self._write_run([item], run_id="20261017-chg")
        ingest.ingest_run(history, run_id="20261017-chg")
        key = ingest.stable_item_key(item)
        models.add_review(
            item_id=key,
            user_id=self.uid,
            status="pending",
            notes="",
            snapshot={
                "prices": item["prices"],
                "possession": item["possession"],
                "availability": item["availability"],
                "right_sold": None,
                "title": item["title"],
            },
        )
        new_item = dict(item)
        new_item["prices"] = {"asking": 120000}
        history2 = self._write_run([new_item], run_id="20261017-chg-2")
        ingest.ingest_run(history2, run_id="20261017-chg-2")
        row = models.get_item(key)
        self.assertTrue(row["price_changed_since_review"])
        self.assertTrue(row["changed_since_review"])

    def test_import_run_cli_invokes_ingest(self):
        # Simulate ``python -m auctionradar.web.manage import-run`` by hitting
        # the helper functions directly — there's no subprocess in
        # this test on purpose (no real network allowed).
        item = {
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/housing/IMP",
            "reference": "RI-1",
            "title": "Piso",
            "prices": {"asking": 110000},
            "possession": {"status": "vacant"},
            "availability": {"status": "available"},
            "right_sold": None,
            "selection": {"status": "candidate", "reasons": []},
        }
        self._write_run([item], run_id="20261017-imp")
        from auctionradar.web.manage import _import_run

        args = type("Args", (), {"path": str(self.data_dir), "run_id": "20261017-imp"})()
        # Make stdout JSON so we don't interact with stderr noise.
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _import_run(args)
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["ingested"], 1)


if __name__ == "__main__":
    unittest.main()
