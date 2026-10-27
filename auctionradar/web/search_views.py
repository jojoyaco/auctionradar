"""Owner-scoped private API for saved searches and notifications."""

import json
import subprocess
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

from auctionradar.assets import ASSET_TYPES, CATEGORY_CHOICES

from . import ai_filter, db, models, searches, security, workspaces
from .security import login_required

bp = Blueprint("searches", __name__)


def owner():
    return security.load_current_user()["id"]


def payload():
    value = request.get_json(silent=True)
    return value if isinstance(value, dict) else None


def sources():
    cfg = workspaces.current_config()
    return json.loads(Path(cfg.CATALOG_PATH).read_text(encoding="utf-8")).get("sources", [])


def profile():
    cfg = workspaces.current_config()
    data = json.loads(Path(cfg.PROFILE_PATH).read_text(encoding="utf-8"))
    overlay = models.get_setting_raw("settings") or {}
    housing = overlay.get("housing") or {}
    if "budget_max_all_in" in housing:
        data.setdefault("housing", {}).setdefault("budget", {})["max_all_in"] = housing[
            "budget_max_all_in"
        ]
    if "preferred_regions" in housing:
        data.setdefault("housing", {})["preferred_regions"] = housing["preferred_regions"]
    return data


def queue_evaluation(search_id):
    root = Path(__file__).resolve().parents[2]
    from .worker_environment import for_workspace

    env = for_workspace(workspaces.current_config(), role="evaluation")
    # Publish the reservation and PID together, including across Gunicorn workers.
    with db.transaction() as connection:
        row = searches.get_search(search_id, owner())
        if row is None:
            return jsonify({"error": "not_found"}), 404
        if not row["active"]:
            return jsonify({"error": "search_paused"}), 400
        if row["ai_status"] == "running" and models.pid_alive(row["eval_pid"]):
            return jsonify({"error": "evaluation_running"}), 409
        if row["ai_status"] == "queued" and models.pid_alive(row["eval_pid"]):
            return jsonify({"ok": True, "search_id": search_id, "status": "queued"}), 202
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "auctionradar.web.search_runner",
                    "--search-id",
                    str(search_id),
                ],
                cwd=str(root),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            connection.execute(
                "UPDATE saved_searches SET ai_status='error',last_error='runner_unavailable' WHERE id=?",
                (search_id,),
            )
            return jsonify({"error": "runner_unavailable"}), 503
        connection.execute(
            "UPDATE saved_searches SET ai_status='queued',last_error=NULL,eval_pid=? WHERE id=?",
            (proc.pid, search_id),
        )
    return jsonify({"ok": True, "search_id": search_id, "status": "queued"}), 202


@bp.get("/api/categories")
@login_required
def categories():
    return jsonify({"categories": CATEGORY_CHOICES, "asset_types": list(ASSET_TYPES)})


@bp.get("/api/searches")
@login_required
def search_list():
    return jsonify(
        {
            "searches": searches.list_searches(owner()),
            "presets": searches.presets(profile(), neutral=db.workspace_mode() == "private"),
            "ai": ai_filter.public_status(),
        }
    )


@bp.post("/api/searches")
@login_required
def search_create():
    try:
        result = searches.save_search(owner(), payload(), {s["id"] for s in sources()})
    except (ValueError, TypeError) as exc:
        return jsonify({"error": "validation_failed", "details": [str(exc)]}), 400
    if result["active"]:
        searches.process_search(result["id"], evaluate=False)
        if (
            result["ai_enabled"]
            or searches.get_search(result["id"], owner())["ai_status"] == "queued"
        ):
            queue_evaluation(result["id"])
        result = searches.search_payload(searches.get_search(result["id"], owner()))
    return jsonify({"search": result, **result}), 201


@bp.patch("/api/searches/<int:search_id>")
@login_required
def search_update(search_id):
    try:
        result = searches.save_search(owner(), payload(), {s["id"] for s in sources()}, search_id)
    except LookupError:
        return jsonify({"error": "not_found"}), 404
    except (ValueError, TypeError) as exc:
        return jsonify({"error": "validation_failed", "details": [str(exc)]}), 400
    if result["active"]:
        searches.process_search(search_id, evaluate=False)
        if result["ai_enabled"] or searches.get_search(search_id, owner())["ai_status"] == "queued":
            queue_evaluation(search_id)
        result = searches.search_payload(searches.get_search(search_id, owner()))
    return jsonify({"search": result, **result})


@bp.delete("/api/searches/<int:search_id>")
@login_required
def search_archive(search_id):
    if searches.get_search(search_id, owner()) is None:
        return jsonify({"error": "not_found"}), 404
    db.execute(
        "UPDATE saved_searches SET active=0,archived=1,updated_at=? WHERE id=? AND owner_id=?",
        (searches.now(), search_id, owner()),
    )
    return jsonify({"ok": True})


@bp.post("/api/searches/<int:search_id>/evaluate")
@login_required
def search_evaluate(search_id):
    return queue_evaluation(search_id)


@bp.post("/api/searches/<int:search_id>/read")
@login_required
def search_read(search_id):
    if searches.get_search(search_id, owner()) is None:
        return jsonify({"error": "not_found"}), 404
    searches.mark_read(owner(), search_id=search_id)
    return jsonify({"ok": True})


@bp.get("/api/searches/<int:search_id>/results")
@login_required
def search_results(search_id):
    from .views import api_items

    return api_items(search_id_override=search_id)


@bp.get("/api/notifications")
@login_required
def notifications():
    return jsonify(searches.notification_payloads(owner()))


@bp.post("/api/notifications/read")
@login_required
def notifications_read():
    value = payload() or {}
    if value.get("all") is True:
        searches.mark_read(owner())
    else:
        ids = value.get("ids")
        if (
            not isinstance(ids, list)
            or len(ids) > 200
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in ids)
        ):
            return jsonify({"error": "invalid_notification_ids"}), 400
        searches.mark_read(owner(), ids=ids)
    return jsonify({"ok": True})
