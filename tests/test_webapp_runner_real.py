"""Real Popen → CLI artifact → ingestion flow, without --dry-run or network access."""

import json
from unittest import mock

from auctionradar.web import ingest, models, runner
from tests.test_webapp_auth import _WebappFixture


class RunnerRealSubprocessTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner", is_admin=True)
        self.fake_root = self.data_dir / "cli"
        self.fake_root.mkdir()
        package = self.fake_root / "auctionradar"
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "cli.py").write_text(
            """
import argparse,json
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--output');p.add_argument('--history-dir');p.add_argument('--region',action='append',default=[])
a,other=p.parse_known_args()
item={'id':'original-cli-id','source_id':'boe','asset_type':'housing','url':'https://example.org/real-child', 'title':'Casa de prueba', 'prices':{'asking':99000},'location':{'municipality':'Oviedo'}}
run={'run_id':'different-cli-generated-id','observed_at':'2026-10-20T05:00:00+00:00','items':[item], 'sources':[{'source_id':'boe','status':'blocked','limitations':['fixture']}], 'summary':{'source_status_counts':{'blocked':1}}, 'filters':{'regions':a.region}}
Path(a.output).write_text(json.dumps(run))
""",
            encoding="utf-8",
        )

    def test_real_process_uses_fresh_output_and_preferences_are_not_hard_filters(self):
        models.set_setting(
            "settings",
            {
                "housing": {"budget_max_all_in": None, "preferred_regions": ["Asturias"]},
                "regions": [],
                "sources": {"boe": {"enabled": True}},
            },
            updated_by=self.uid,
        )
        models.create_run(run_id="real-subprocess", created_by=self.uid, sources=["boe"])
        history = self.data_dir / "history"
        with (
            mock.patch.object(runner, "PROJECT_ROOT", self.fake_root),
            mock.patch.dict(
                "os.environ",
                {"AUCTIONRADAR_DATA_DIR": str(self.data_dir), "AUCTIONRADAR_PROFILE_OVERLAY": ""},
            ),
        ):
            result = runner.main(
                [
                    "--run-id",
                    "real-subprocess",
                    "--data-dir",
                    str(self.data_dir),
                    "--output-dir",
                    str(history),
                ]
            )
        self.assertEqual(result, 0)
        row = models.get_run("real-subprocess")
        self.assertEqual(row["status"], "partial")
        summary = json.loads(row["summary_json"])
        self.assertEqual(summary["sources"][0]["source_id"], "boe")
        artifact = json.loads((history / "real-subprocess.json").read_text())
        self.assertEqual(artifact["run_id"], "different-cli-generated-id")
        self.assertEqual(artifact["filters"]["regions"], [])
        self.assertEqual(models.iter_items()[1], 1)
        profile = json.loads((self.data_dir / runner.PROFILE_FILENAME).read_text())
        self.assertIsNone(profile["housing"]["budget"]["max_all_in"])

    def test_can_clear_preferred_regions(self):
        path = runner.write_profile_overlay({"housing": {"preferred_regions": []}}, self.data_dir)
        self.assertEqual(json.loads(path.read_text())["housing"]["preferred_regions"], [])

    def test_older_import_does_not_overwrite_newer_price_or_date(self):
        history = self.data_dir / "history"
        history.mkdir()
        base = {
            "id": "x",
            "asset_type": "housing",
            "source_id": "boe",
            "url": "https://example.org/chronology",
            "title": "Casa",
        }
        for name, stamp, price in [
            ("new", "2026-10-21T05:00:00+00:00", 90000),
            ("old", "2026-10-20T05:00:00+00:00", 100000),
        ]:
            (history / (name + ".json")).write_text(
                json.dumps({"observed_at": stamp, "items": [{**base, "prices": {"asking": price}}]})
            )
            ingest.ingest_run(history, run_id=name)
        row = models.iter_items()[0][0]
        self.assertEqual(json.loads(row["data_json"])["prices"]["asking"], 90000)
        self.assertEqual(row["last_seen"], "2026-10-21T05:00:00+00:00")
