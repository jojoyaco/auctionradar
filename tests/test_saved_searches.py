"""Personal searches: ACLs, alerts, AI cache, and separation from human review."""

import fcntl
import json
from unittest import mock

from auctionradar.web import db, models, searches
from auctionradar.web.ai_filter import AISettings
from tests.test_ai_filter import scripted_server
from tests.test_webapp_auth import _WebappFixture


class SavedSearchTests(_WebappFixture):
    def setUp(self):
        super().setUp()
        self.uid = self.make_user(username="owner", is_admin=True)
        self.login(username="owner")

    def item(self, suffix="1", price=12000, kind="boats"):
        data = {
            "id": "boat-" + suffix,
            "record_type": "asset",
            "title": "Lancha de prueba " + suffix,
            "asset_type": kind,
            "category": kind,
            "source_id": "boe",
            "url": "https://example.org/boat/" + suffix,
            "facts_scope": "current_listing_entity",
            "prices": {"asking": price},
            "length_m": 5,
            "facts": [{"field": "prices.asking", "value": price}],
            "photos": [],
            "availability": {"status": "available"},
        }
        models.upsert_item(
            item_id=data["id"],
            asset_type=kind,
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-17T07:00:00+00:00",
            last_seen="2026-10-17T07:00:00+00:00",
            data=data,
            listing_url=data["url"],
            raw_fingerprint="x",
        )
        return data

    def create(self, **extra):
        result = self.api_call(
            "POST",
            "/api/searches",
            json={"name": "Mis barcos", "criteria": {"categories": ["boats"]}, **extra},
        )
        self.assertEqual(result.status_code, 201, result.data)
        return result.get_json()["search"]

    def test_create_matches_existing_catalog_and_notifications_are_idempotent(self):
        self.item()
        saved = self.create()
        self.assertEqual(saved["match_count"], 1)
        self.assertEqual(self.client.get("/api/notifications").get_json()["unread_count"], 1)
        searches.process_search(saved["id"])
        self.assertEqual(self.client.get("/api/notifications").get_json()["unread_count"], 1)
        items = self.client.get("/api/items?search_id=" + str(saved["id"])).get_json()
        self.assertEqual(items["total"], 1)

    def test_searches_prompts_and_notifications_are_private_to_owner(self):
        self.item()
        saved = self.create(prompt="A private search prompt")
        notification = self.client.get("/api/notifications").get_json()["notifications"][0]["id"]
        self.make_user(username="other")
        self.login(username="other")
        self.assertEqual(self.client.get("/api/searches").get_json()["searches"], [])
        for method, path, body in [
            ("PATCH", f"/api/searches/{saved['id']}", {"name": "stolen"}),
            ("DELETE", f"/api/searches/{saved['id']}", {}),
            ("POST", f"/api/searches/{saved['id']}/evaluate", {}),
            ("POST", f"/api/searches/{saved['id']}/read", {}),
        ]:
            self.assertEqual(self.api_call(method, path, json=body).status_code, 404)
        self.assertEqual(
            self.client.get("/api/items?search_id=" + str(saved["id"])).status_code, 404
        )
        self.assertEqual(self.client.get("/api/notifications").get_json()["unread_count"], 0)
        self.api_call("POST", "/api/notifications/read", json={"ids": [notification]})
        self.assertIsNone(
            db.fetchone("SELECT read_at FROM notifications WHERE id=?", (notification,))["read_at"]
        )

    def test_archive_is_non_destructive(self):
        self.item()
        saved = self.create()
        self.assertEqual(
            self.api_call("DELETE", f"/api/searches/{saved['id']}", json={}).status_code, 200
        )
        self.assertEqual(self.client.get("/api/searches").get_json()["searches"], [])
        self.assertEqual(db.fetchone("SELECT COUNT(*) c FROM search_matches")["c"], 1)
        self.assertEqual(self.client.get("/api/notifications").get_json()["unread_count"], 0)
        self.assertEqual(models.iter_items()[1], 1)

    def test_malformed_search_criteria_rejected_and_csrf_required(self):
        for value in (
            {"name": "x", "criteria": {}},
            {"name": "Invalid", "criteria": {"categories": ["made_up"]}},
            {"name": "Invalid", "criteria": {"price_min": 10, "price_max": 2}},
            {"name": "Invalid", "criteria": {}, "ai_enabled": True, "prompt": ""},
        ):
            self.assertEqual(self.api_call("POST", "/api/searches", json=value).status_code, 400)
        self.assertEqual(
            self.client.post(
                "/api/searches", json={"name": "Missing token", "criteria": {}}
            ).status_code,
            400,
        )

    def test_ai_pipeline_uses_real_http_and_cache_without_overwriting_human_ko(self):
        a = self.item("a", 12000)
        self.item("b", 20000)
        models.add_review(
            item_id=a["id"],
            user_id=self.uid,
            status="ko",
            notes="Private team review must not reach the model",
            snapshot={},
        )
        saved = searches.save_search(
            self.uid,
            {
                "name": "AI boats",
                "criteria": {"categories": ["boats"]},
                "prompt": "Keep boats below 15000 EUR",
                "ai_enabled": True,
            },
            {"boe"},
        )

        def response(body, headers):
            request = json.loads(body)
            facts = json.loads(
                request["messages"][1]["content"].split("```json\n", 1)[1].rsplit("\n```", 1)[0]
            )
            fact = next(f for f in facts["facts"] if f["field"] == "prices.asking")
            keep = fact["value"] < 15000
            output = {
                "status": "evaluated",
                "verdict": "keep" if keep else "reject",
                "score": 90 if keep else 10,
                "reason": "Matches the requested published price"
                if keep
                else "Published price exceeds request",
                "missing_fields": [],
                "limitations": [],
                "factual_claims": [
                    {
                        "claim": "Published price",
                        "field": fact["field"],
                        "value": fact["value"],
                        "evidence_id": fact["id"],
                    }
                ],
            }
            return json.dumps({"message": {"content": json.dumps(output)}}).encode()

        with scripted_server(response) as server:
            cfg = AISettings(
                provider="ollama",
                base_url="http://127.0.0.1:" + str(server.server_address[1]),
                model="fixture",
                timeout=5,
                max_retries=0,
            )
            first = searches.process_search(saved["id"], settings=cfg)
            second = searches.process_search(saved["id"], settings=cfg)
            self.assertEqual(first["evaluated"], 2)
            self.assertEqual(second["cached"], 2)
            self.assertEqual(server.request_count, 2)
            self.assertNotIn(b"Private team review", server.last_body)
        self.assertEqual(models.latest_review_for_item(a["id"])["status"], "ko")
        self.assertEqual(searches.notification_payloads(self.uid)["unread_count"], 1)
        listed = self.client.get(
            "/api/items?search_id=" + str(saved["id"]) + "&ai=shortlist&status=all"
        ).get_json()
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["items"][0]["ai"]["verdict"], "keep")

    def test_evaluate_launches_isolated_runner_and_honors_ownership(self):
        saved = self.create()
        with mock.patch(
            "auctionradar.web.search_views.subprocess.Popen", return_value=mock.Mock(pid=99999)
        ) as launch:
            response = self.api_call("POST", f"/api/searches/{saved['id']}/evaluate", json={})
        self.assertEqual(response.status_code, 202)
        self.assertIn("auctionradar.web.search_runner", launch.call_args.args[0])
        self.assertEqual(
            launch.call_args.kwargs["env"]["AUCTIONRADAR_DATA_DIR"], str(self.data_dir)
        )

    def test_mark_search_read_does_not_change_other_search_notifications(self):
        self.item()
        a = self.create()
        b = self.create(name="Second search")
        self.api_call("POST", f"/api/searches/{a['id']}/read", json={})
        notifications = searches.notification_payloads(self.uid)
        self.assertEqual(notifications["unread_count"], 1)
        self.assertIsNone(
            next(n for n in notifications["notifications"] if n["search_id"] == b["id"])["read_at"]
        )

    def test_inline_search_processing_does_not_race_background_evaluator(self):
        self.item()
        saved = self.create()
        with (self.data_dir / "search-evaluations.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = searches.process_search(saved["id"], evaluate=False)
        self.assertTrue(result["queued"])
        self.assertEqual(searches.get_search(saved["id"])["ai_status"], "queued")

    def test_live_queued_pid_prevents_duplicate_launch(self):
        import os

        saved = self.create()
        db.execute(
            "UPDATE saved_searches SET ai_status='queued',eval_pid=? WHERE id=?",
            (os.getpid(), saved["id"]),
        )
        with mock.patch("auctionradar.web.search_views.subprocess.Popen") as launch:
            response = self.api_call("POST", f"/api/searches/{saved['id']}/evaluate", json={})
        self.assertEqual(response.status_code, 202)
        launch.assert_not_called()

    def test_notifications_disabled_remains_silent_and_backlog_is_explicit(self):
        self.item()
        saved = self.create(notifications_enabled=False)
        self.assertEqual(searches.notification_payloads(self.uid)["unread_count"], 0)
        self.assertIn("evaluation_counts", saved)

    def test_next_pass_resumes_backlog_instead_of_reevaluating_cached_matches(self):
        for index in range(3):
            self.item(str(index))
        saved = searches.save_search(
            self.uid,
            {
                "name": "Budgeted AI",
                "criteria": {"categories": ["boats"]},
                "prompt": "Keep boats",
                "ai_enabled": True,
            },
            {"boe"},
        )
        cfg = AISettings(provider="ollama", base_url="http://127.0.0.1:11434", model="fixture")
        response = {
            "status": "evaluated",
            "verdict": "keep",
            "score": 90,
            "reason": "Published boat",
            "evidence": [],
        }
        with mock.patch(
            "auctionradar.web.searches.ai_filter.evaluate_item", return_value=response
        ) as evaluate:
            first = searches.process_search(saved["id"], settings=cfg, max_evaluations=2)
            second = searches.process_search(saved["id"], settings=cfg, max_evaluations=2)
        self.assertEqual((first["evaluated"], first["pending"]), (2, 1))
        self.assertEqual((second["evaluated"], second["cached"], second["pending"]), (1, 2, 0))
        self.assertEqual(evaluate.call_count, 3)
        self.assertEqual(searches.get_search(saved["id"])["ai_status"], "ready")
