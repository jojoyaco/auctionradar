"""Personal searches, independent evaluations, review decisions and notifications."""

import fcntl
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from . import ai_filter, catalog, db, models


def now():
    return datetime.now(timezone.utc).isoformat()


def get_search(search_id, owner_id=None):
    sql = "SELECT * FROM saved_searches WHERE id=? AND archived=0"
    params = [search_id]
    if owner_id is not None:
        sql += " AND owner_id=?"
        params.append(owner_id)
    return db.fetchone(sql, tuple(params))


def search_payload(row):
    count = db.fetchone(
        "SELECT COUNT(*) c FROM search_matches WHERE search_id=? AND active=1 AND verdict!='reject'",
        (row["id"],),
    )["c"]
    unread = db.fetchone(
        "SELECT COUNT(*) c FROM notifications WHERE search_id=? AND owner_id=? AND read_at IS NULL",
        (row["id"], row["owner_id"]),
    )["c"]
    value = {
        k: row[k]
        for k in (
            "id",
            "name",
            "prompt",
            "created_at",
            "updated_at",
            "last_checked_at",
            "ai_status",
            "last_error",
        )
    } | {
        "criteria": json.loads(row["criteria_json"]),
        "ai_enabled": bool(row["ai_enabled"]),
        "active": bool(row["active"]),
        "notifications_enabled": bool(row["notifications_enabled"]),
        "match_count": int(count),
        "unread_count": int(unread),
    }
    value["evaluation_counts"] = json.loads(row["evaluation_counts_json"] or "{}")
    if row["ai_status"] == "running" and not models.pid_alive(row["eval_pid"]):
        value["ai_status"] = "pending"
    return value


def list_searches(owner_id):
    return [
        search_payload(row)
        for row in db.fetchall(
            "SELECT * FROM saved_searches WHERE owner_id=? AND archived=0 ORDER BY id", (owner_id,)
        )
    ]


def save_search(owner_id, payload, source_ids, search_id=None):
    allowed = {"name", "criteria", "prompt", "ai_enabled", "active", "notifications_enabled"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("invalid_search")
    if (
        search_id is None
        and db.fetchone(
            "SELECT COUNT(*) c FROM saved_searches WHERE owner_id=? AND archived=0", (owner_id,)
        )["c"]
        >= 50
    ):
        raise ValueError("search_limit_reached")
    existing = get_search(search_id, owner_id) if search_id is not None else None
    if search_id is not None and existing is None:
        raise LookupError("not_found")
    prior = (
        search_payload(existing)
        if existing
        else {
            "name": "",
            "criteria": {},
            "prompt": "",
            "ai_enabled": False,
            "active": True,
            "notifications_enabled": True,
        }
    )
    merged = {**prior, **payload}
    name = merged["name"]
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 80:
        raise ValueError("invalid_search_name")
    prompt = merged["prompt"]
    if not isinstance(prompt, str) or len(prompt) > 6000:
        raise ValueError("invalid_prompt")
    for flag in ("ai_enabled", "active", "notifications_enabled"):
        if not isinstance(merged[flag], bool):
            raise ValueError("invalid_" + flag)
    if merged["ai_enabled"] and not prompt.strip():
        raise ValueError("prompt_required")
    criteria = catalog.normalize_criteria(merged["criteria"], source_ids)
    stamp = now()
    values = (
        name.strip(),
        json.dumps(criteria, ensure_ascii=False),
        prompt,
        merged["ai_enabled"],
        merged["active"],
        merged["notifications_enabled"],
        stamp,
    )
    if existing:
        db.execute(
            "UPDATE saved_searches SET name=?,criteria_json=?,prompt=?,ai_enabled=?,active=?,notifications_enabled=?,updated_at=?,ai_status='pending',last_error=NULL WHERE id=? AND owner_id=?",
            values + (search_id, owner_id),
        )
    else:
        cur = db.execute(
            "INSERT INTO saved_searches(name,criteria_json,prompt,ai_enabled,active,notifications_enabled,updated_at,created_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?)",
            values + (stamp, owner_id),
        )
        search_id = int(cur.lastrowid)
    return search_payload(get_search(search_id, owner_id))


def presets(profile=None, *, neutral=False):
    profile = profile or {}
    housing = profile.get("housing") or {}
    budget = (housing.get("budget") or {}).get("max_all_in")
    budget_text = (
        f"Coste total máximo {budget} EUR, incluidos impuestos, gastos y reforma."
        if budget is not None
        else "No hay un presupuesto fijado; configura tu límite antes de decidir."
    )
    budget_text_en = (
        f"Maximum all-in cost: {budget} EUR, including taxes, fees and renovation."
        if budget is not None
        else "No budget is set; configure your limit before making a decision."
    )
    prefs = [
        p.get("name") if isinstance(p, dict) else p for p in housing.get("preferred_regions", [])
    ]
    return [
        {
            "name": "Housing",
            "label_es": "Viviendas",
            "label_en": "Housing",
            "criteria": catalog.normalize_criteria(
                {
                    "categories": ["housing"],
                    "all_in_max": budget,
                    "preferred_regions": prefs,
                    "exclude_occupied": True,
                    "required_fields": ["possession", "all_in_cost_estimate"],
                }
            ),
            "prompt": f"I am looking for housing for personal use or noncommercial study, preferably in {', '.join(prefs) or 'the selected areas'}. {budget_text_en} Exclude confirmed occupancy, bare ownership or undivided ownership shares. If possession or all-in cost is unknown, leave it pending review. Do not confuse valuation, starting bid or advertised price with final cost.",
            "prompt_es": f"Busco viviendas para uso personal o estudio no comercial, preferentemente {', '.join(prefs) or 'las zonas indicadas'}. {budget_text} Descarta ocupación acreditada, nuda propiedad o proindiviso. Si faltan posesión o coste total deja pendiente de revisión. No confundas tasación, salida o precio anunciado con coste final.",
        },
        {
            "name": "Vans",
            "label_es": "Furgonetas",
            "label_en": "Vans",
            "criteria": catalog.normalize_criteria({"categories": ["vans"]}),
            "prompt": "I am looking for vans to study a personal camper conversion. Compare only published size, mileage, engine, condition and documentation. Flag unknown facts; do not invent approval status, mechanical condition or conversion costs.",
            "prompt_es": "Busco furgonetas adecuadas para estudiar una camperización. Compara únicamente datos publicados de tamaño, kilometraje, motor, estado y documentación. Señala lo desconocido; no inventes homologación, mecánica ni costes de conversión.",
        },
        {
            "name": "Boats",
            "label_es": "Barcos",
            "label_en": "Boats",
            "criteria": catalog.normalize_criteria({"categories": ["boats"]}),
            "prompt": "I am looking for boats for personal use. Summarize the published price and its type, model, length, engine, hours, condition, location and documentation where available. Flag missing information for review; do not invent seaworthiness, encumbrances or maintenance history.",
            "prompt_es": "Busco embarcaciones. Resume precio publicado y su tipo, modelo, eslora, motor, horas, estado, ubicación y documentación cuando consten. Si faltan datos para decidir, marca para revisar; no inventes navegabilidad, cargas ni mantenimiento.",
        },
    ]


def assessment(search_id, item_id):
    row = db.fetchone(
        "SELECT m.assessment_json,m.content_fingerprint,i.data_json FROM search_matches m JOIN items i ON i.id=m.item_id WHERE m.search_id=? AND m.item_id=? AND m.active=1",
        (search_id, item_id),
    )
    if not row or not row["assessment_json"]:
        return None
    if row["content_fingerprint"] != catalog.content_fingerprint(json.loads(row["data_json"])):
        return {
            **_pending(True),
            "status": "pending",
            "stale": True,
            "reason": "Listing data changed after the last evaluation.",
        }
    try:
        return json.loads(row["assessment_json"])
    except ValueError:
        return None


def notification_payloads(owner_id):
    rows = db.fetchall(
        "SELECT n.*,s.name search_name,json_extract(i.data_json,'$.title') item_title FROM notifications n JOIN saved_searches s ON s.id=n.search_id JOIN items i ON i.id=n.item_id WHERE n.owner_id=? AND s.archived=0 ORDER BY n.id DESC LIMIT 100",
        (owner_id,),
    )
    unread = db.fetchone(
        "SELECT COUNT(*) c FROM notifications n JOIN saved_searches s ON s.id=n.search_id WHERE n.owner_id=? AND s.archived=0 AND n.read_at IS NULL",
        (owner_id,),
    )["c"]
    return {
        "notifications": [
            {
                k: r[k]
                for k in (
                    "id",
                    "search_id",
                    "search_name",
                    "item_id",
                    "item_title",
                    "kind",
                    "created_at",
                    "read_at",
                )
            }
            for r in rows
        ],
        "unread_count": int(unread),
    }


def mark_read(owner_id, *, search_id=None, ids=None):
    sql = "UPDATE notifications SET read_at=? WHERE owner_id=? AND read_at IS NULL"
    params = [now(), owner_id]
    if search_id is not None:
        sql += " AND search_id=?"
        params.append(search_id)
    if ids is not None:
        if not ids:
            return
        sql += " AND id IN (" + ",".join("?" for _ in ids) + ")"
        params += ids
    db.execute(sql, tuple(params))


def _pending(enabled):
    return {
        "status": "pending" if enabled else "not_requested",
        "verdict": "needs_review",
        "score": None,
        "reason": "",
        "missing_fields": [],
        "evidence": [],
        "provider": None,
        "model": None,
    }


def process_search(
    search_id, *, settings=None, deadline=None, max_evaluations=30, evaluate=True, lock_held=False
):
    if not lock_held:
        with (Path(db.current_data_dir()) / "search-evaluations.lock").open("a+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                db.execute(
                    "UPDATE saved_searches SET ai_status='queued' WHERE id=? AND ai_status!='running'",
                    (search_id,),
                )
                return {"queued": True}
            return process_search(
                search_id,
                settings=settings,
                deadline=deadline,
                max_evaluations=max_evaluations,
                evaluate=evaluate,
                lock_held=True,
            )
    row = get_search(search_id)
    if row is None or not row["active"]:
        return {"matched": 0, "evaluated": 0, "pending": 0}
    cfg = settings or ai_filter.get_settings()
    criteria = json.loads(row["criteria_json"])
    prompt = row["prompt"]
    enabled = bool(row["ai_enabled"])
    stamp = now()
    previous = {
        r["item_id"]: dict(r)
        for r in db.fetchall("SELECT * FROM search_matches WHERE search_id=?", (search_id,))
    }
    candidates = []
    cursor = db.get_connection().execute("SELECT * FROM items ORDER BY id")
    while True:
        batch = cursor.fetchmany(1000)
        if not batch:
            break
        for item in batch:
            data = json.loads(item["data_json"])
            if catalog.matches_criteria(data, criteria):
                candidates.append((item, data))
    current = get_search(search_id)
    if current is None or current["updated_at"] != row["updated_at"]:
        return {"superseded": True}
    db.execute(
        "UPDATE saved_searches SET ai_status='running',eval_pid=?,last_error=NULL WHERE id=?",
        (os.getpid(), search_id),
    )
    with db.transaction() as conn:
        conn.execute("UPDATE search_matches SET active=0 WHERE search_id=?", (search_id,))
        for item, data in candidates:
            prior = previous.get(item["id"])
            content = catalog.content_fingerprint(data)
            fingerprint = (
                ai_filter.evaluation_fingerprint(prompt, data, criteria, cfg)
                if enabled
                else "not_requested:" + content
            )
            cached = False
            if prior and prior["evaluation_fingerprint"] == fingerprint:
                try:
                    old = json.loads(prior["assessment_json"] or "{}")
                    cached = (
                        old.get("status") == "evaluated"
                        or old.get("error_code") == "invalid_input"
                        or not enabled
                    )
                except ValueError:
                    pass
            result = json.loads(prior["assessment_json"]) if cached else _pending(enabled)
            conn.execute(
                "INSERT INTO search_matches(search_id,item_id,first_matched,last_matched,content_fingerprint,evaluation_fingerprint,assessment_json,verdict,active) VALUES (?,?,?,?,?,?,?,?,1) ON CONFLICT(search_id,item_id) DO UPDATE SET last_matched=excluded.last_matched,content_fingerprint=excluded.content_fingerprint,evaluation_fingerprint=excluded.evaluation_fingerprint,assessment_json=excluded.assessment_json,verdict=excluded.verdict,active=1",
                (
                    search_id,
                    item["id"],
                    prior["first_matched"] if prior else stamp,
                    stamp,
                    content,
                    fingerprint,
                    json.dumps(result, ensure_ascii=False),
                    result.get("verdict", "needs_review"),
                ),
            )
    totals = {
        "matched": len(candidates),
        "evaluated": 0,
        "attempted": 0,
        "cached": 0,
        "pending": 0,
        "errors": 0,
        "notifications": 0,
    }
    failures = 0
    for item, data in candidates:
        current = get_search(search_id)
        if current is None or not current["active"]:
            break
        if current["updated_at"] != row["updated_at"]:
            return {**totals, "superseded": True}
        match = db.fetchone(
            "SELECT * FROM search_matches WHERE search_id=? AND item_id=?", (search_id, item["id"])
        )
        result = json.loads(match["assessment_json"])
        if enabled and result["status"] == "pending":
            can_infer = (
                ai_filter.public_status(cfg)["configured"]
                and data.get("facts_scope") == "current_listing_entity"
            )
            if not evaluate or (
                can_infer
                and (
                    totals["attempted"] >= max_evaluations
                    or (deadline and time.monotonic() >= deadline)
                    or failures >= 3
                )
            ):
                totals["pending"] += 1
                continue
            if can_infer:
                totals["attempted"] += 1
            try:
                result = ai_filter.evaluate_item(prompt, data, criteria, cfg)
            except Exception:
                result = {**_pending(True), "status": "error", "error_code": "evaluation_error"}
            current = get_search(search_id)
            if current is None or current["updated_at"] != row["updated_at"]:
                return {**totals, "superseded": True}
            if result.get("status") == "evaluated":
                totals["evaluated"] += 1
            if result.get("status") != "evaluated":
                totals["errors"] += 1
            if result.get("error_code") in (
                "timeout",
                "transport_error",
                "auth_error",
                "response_error",
            ):
                failures += 1
            result["verdict"] = (
                result.get("verdict")
                if result.get("verdict") in ("keep", "reject", "needs_review")
                else "needs_review"
            )
            db.execute(
                "UPDATE search_matches SET assessment_json=?,verdict=? WHERE id=?",
                (json.dumps(result, ensure_ascii=False), result["verdict"], match["id"]),
            )
        else:
            totals["cached"] += 1
        qualifies = not enabled or (
            result.get("status") == "evaluated" and result.get("verdict") == "keep"
        )
        prior = previous.get(item["id"])
        changed = (
            prior is None
            or prior["content_fingerprint"] != match["content_fingerprint"]
            or (prior["verdict"] != "keep" and result.get("verdict") == "keep")
        )
        if row["notifications_enabled"] and qualifies and changed:
            kind = (
                "ai_qualified"
                if enabled and (prior is None or prior["verdict"] != "keep")
                else ("changed_match" if prior else "new_match")
            )
            key = match["content_fingerprint"] + hashlib.sha256(prompt.encode()).hexdigest()
            cur = db.execute(
                "INSERT OR IGNORE INTO notifications(owner_id,search_id,item_id,kind,fingerprint,created_at) VALUES (?,?,?,?,?,?)",
                (row["owner_id"], search_id, item["id"], kind, key, stamp),
            )
            totals["notifications"] += max(0, cur.rowcount)
    status = "pending" if totals["pending"] else ("partial" if totals["errors"] else "ready")
    if enabled and not ai_filter.public_status(cfg)["configured"]:
        status = "unavailable"
    db.execute(
        "UPDATE saved_searches SET last_checked_at=?,ai_status=?,eval_pid=NULL,last_error=?,evaluation_counts_json=? WHERE id=?",
        (
            now(),
            status,
            "provider_or_evidence_unavailable" if totals["errors"] else None,
            json.dumps(totals),
            search_id,
        ),
    )
    return totals


def process_subscriptions(search_id=None, *, settings=None, budget_seconds=900, max_evaluations=30):
    cfg = settings or ai_filter.get_settings()
    rows = db.fetchall(
        "SELECT s.id FROM saved_searches s JOIN users u ON u.id=s.owner_id WHERE s.active=1 AND s.archived=0 AND u.active=1"
        + (" AND s.id=?" if search_id else "")
        + " ORDER BY s.id",
        (search_id,) if search_id else (),
    )
    deadline = time.monotonic() + budget_seconds
    queue = [row["id"] for row in rows]
    done = set()
    results = {}
    while queue:
        identifier = queue.pop(0)
        row = get_search(identifier)
        if row is None or not row["active"]:
            continue
        version = (identifier, row["updated_at"])
        if version in done:
            continue
        results[str(identifier)] = process_search(
            identifier,
            settings=cfg,
            deadline=deadline,
            max_evaluations=max_evaluations,
            lock_held=True,
        )
        done.add(version)
        if time.monotonic() >= deadline:
            break
        if len(done) >= 100:
            break
        queue += [
            r["id"]
            for r in db.fetchall(
                "SELECT s.id,s.updated_at FROM saved_searches s JOIN users u ON u.id=s.owner_id WHERE s.ai_status='queued' AND s.active=1 AND s.archived=0 AND u.active=1"
            )
            if r["id"] not in queue and (r["id"], r["updated_at"]) not in done
        ]
    return results
