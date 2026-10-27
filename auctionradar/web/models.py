"""Persistence helpers for the auctionradar.web.

The webapp never modifies the canonical CLI artefacts (catalog,
profile, feedback, seeds). It reads them at ingest / settings time
through :mod:`search_cli`. Here we only describe the webapp's own
state (users, items, reviews, shares, runs, settings, audit).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from . import db

# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def create_user(username: str, password_hash: str, password_salt: str, *, is_admin: bool) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db.identity_context():
        cursor = db.execute(
            "INSERT INTO users(username, password_hash, password_salt, is_admin, active, created_at, last_password_change, session_version) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, 1)",
            (username, password_hash, password_salt, 1 if is_admin else 0, now, now),
        )
        return int(cursor.lastrowid)


def get_user_by_username(username: str) -> Optional[sqlite3.Row]:
    with db.identity_context():
        return db.fetchone(
            "SELECT id, username, password_hash, password_salt, is_admin, active, session_version, created_at, last_password_change FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        )


def get_user_by_id(user_id: int) -> Optional[sqlite3.Row]:
    with db.identity_context():
        return db.fetchone(
            "SELECT id, username, password_hash, password_salt, is_admin, active, session_version FROM users WHERE id = ?",
            (user_id,),
        )


def list_users() -> list[sqlite3.Row]:
    with db.identity_context():
        return db.fetchall(
            "SELECT id, username, is_admin, active, created_at, last_password_change, session_version FROM users ORDER BY username"
        )


def count_active_admins() -> int:
    with db.identity_context():
        row = db.fetchone("SELECT COUNT(*) AS c FROM users WHERE is_admin = 1 AND active = 1")
        return int(row["c"]) if row else 0


def deactivate_user(user_id: int) -> bool:
    row = get_user_by_id(user_id)
    if row is None:
        return False
    if row["is_admin"]:
        if count_active_admins() <= 1:
            return False
    with db.identity_context():
        db.execute(
            "UPDATE users SET active = 0, session_version = session_version + 1 WHERE id = ?",
            (user_id,),
        )
    return True


def change_password(user_id: int, new_hash: str, new_salt: str) -> None:
    """Rotate the password and invalidate every active session for that user."""
    now = datetime.now(timezone.utc).isoformat()
    with db.identity_context():
        db.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, last_password_change = ?, session_version = session_version + 1 WHERE id = ?",
            (new_hash, new_salt, now, user_id),
        )


def set_password(user_id: int, new_hash: str, new_salt: str) -> None:
    """Administrative password reset (no current-password check)."""
    now = datetime.now(timezone.utc).isoformat()
    with db.identity_context():
        db.execute(
            "UPDATE users SET password_hash = ?, password_salt = ?, last_password_change = ?, session_version = session_version + 1 WHERE id = ?",
            (new_hash, new_salt, now, user_id),
        )


# ---------------------------------------------------------------------------
# Items and reviews
# ---------------------------------------------------------------------------


def upsert_item(
    *,
    item_id: str,
    asset_type: str,
    source_id: str,
    source_ids: list[str],
    first_seen: str,
    last_seen: str,
    data: dict,
    listing_url: str,
    raw_fingerprint: str,
) -> tuple[bool, dict]:
    """Insert or update an item preserving the user-driven state.

    Returns ``(changed_since_review_before, item_row)``. The caller uses
    the boolean to recompute the change flag based on the freshly produced
    snapshot.
    """
    existing = db.fetchone(
        "SELECT id, latest_review_id, price_changed_since_review, availability_changed_since_review, posession_changed_since_review, "
        "title_changed_since_review, right_sold_changed_since_review, exact_price_changed_since_review, "
        "changed_since_review, raw_fingerprint FROM items WHERE id = ?",
        (item_id,),
    )
    data_json = json.dumps(data, ensure_ascii=False, default=str)
    from .catalog import derived_fields

    category, price_amount, price_type, photo_count = derived_fields(data, asset_type)
    source_ids_text = ",".join(dict.fromkeys([source_id, *source_ids]))
    if existing is None:
        db.execute(
            "INSERT INTO items(id, asset_type, source_id, source_ids, first_seen, last_seen, data_json, listing_url, raw_fingerprint,category,price_amount,price_type,photo_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                item_id,
                asset_type,
                source_id,
                source_ids_text,
                first_seen,
                last_seen,
                data_json,
                listing_url,
                raw_fingerprint,
                category,
                price_amount,
                price_type,
                photo_count,
            ),
        )
        row = db.fetchone(
            "SELECT changed_since_review, price_changed_since_review, availability_changed_since_review, posession_changed_since_review, latest_review_id, raw_fingerprint FROM items WHERE id = ?",
            (item_id,),
        )
        return False, _row_to_dict(row)

    # Preserve the user review (if any); only update the source pieces.
    db.execute(
        "UPDATE items SET asset_type = ?, source_id = ?, source_ids = ?, last_seen = ?, data_json = ?, listing_url = ?, raw_fingerprint = ?,category=?,price_amount=?,price_type=?,photo_count=? WHERE id = ?",
        (
            asset_type,
            source_id,
            source_ids_text,
            last_seen,
            data_json,
            listing_url,
            raw_fingerprint,
            category,
            price_amount,
            price_type,
            photo_count,
            item_id,
        ),
    )
    row = db.fetchone(
        "SELECT changed_since_review, price_changed_since_review, availability_changed_since_review, posession_changed_since_review, latest_review_id, raw_fingerprint FROM items WHERE id = ?",
        (item_id,),
    )
    return bool(row and row["latest_review_id"]), _row_to_dict(row)


def update_change_flag(
    item_id: str,
    *,
    price: bool = False,
    possession: bool = False,
    availability: bool = False,
    title: bool = False,
    right_sold: bool = False,
    exact_price: bool = False,
) -> dict:
    """Update the substantive-change flags for a known review snapshot.

    Each flag is positional: callers report whichever fields differ
    between the latest review snapshot and the currently observed
    listing. ``changed_since_review`` is the OR of all flags, used by
    the API as a coarse filter.
    """
    any_change = bool(price or possession or availability or title or right_sold or exact_price)
    db.execute(
        "UPDATE items SET price_changed_since_review = ?, posession_changed_since_review = ?, availability_changed_since_review = ?, "
        "title_changed_since_review = ?, right_sold_changed_since_review = ?, exact_price_changed_since_review = ?, "
        "changed_since_review = ? WHERE id = ?",
        (
            1 if price else 0,
            1 if possession else 0,
            1 if availability else 0,
            1 if title else 0,
            1 if right_sold else 0,
            1 if exact_price else 0,
            1 if any_change else 0,
            item_id,
        ),
    )
    row = db.fetchone(
        "SELECT changed_since_review, price_changed_since_review, availability_changed_since_review, "
        "posession_changed_since_review, title_changed_since_review, right_sold_changed_since_review, "
        "exact_price_changed_since_review, latest_review_id, raw_fingerprint FROM items WHERE id = ?",
        (item_id,),
    )
    return _row_to_dict(row)


def get_item(item_id: str) -> Optional[sqlite3.Row]:
    return db.fetchone(
        "SELECT id, asset_type, category, price_amount, price_type, photo_count, source_id, source_ids, first_seen, last_seen, data_json, listing_url, raw_fingerprint, "
        "       price_changed_since_review, availability_changed_since_review, posession_changed_since_review, "
        "       title_changed_since_review, right_sold_changed_since_review, exact_price_changed_since_review, "
        "       changed_since_review, latest_review_id "
        "FROM items WHERE id = ?",
        (item_id,),
    )


def iter_items(
    asset: Optional[str] = None,
    status: Optional[str] = None,
    *,
    query_text: Optional[str] = None,
    source_id: Optional[str] = None,
    changed_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    category: Optional[str] = None,
    sort: str = "updated",
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    include_unknown_price: bool = False,
    location: Optional[str] = None,
    has_photos: bool = False,
    search_id: Optional[int] = None,
    ai: str = "all",
) -> tuple[list[sqlite3.Row], int]:
    """Return ``(rows, total_after_filtering)`` for the items list endpoint.

    Filtering, latest review, and ordering are resolved in SQL before
    pagination. Counts also use the complete filtered set.
    """
    from .catalog import SORTS, fold_text

    if sort not in SORTS:
        raise ValueError("invalid_sort")
    clauses, params = [], []
    joins = " LEFT JOIN reviews r ON r.id=i.latest_review_id "
    if search_id is not None:
        joins += " JOIN search_matches sm ON sm.item_id=i.id AND sm.search_id=? AND sm.active=1 "
        params.append(search_id)
        if ai in ("keep", "reject", "needs_review"):
            clauses.append("sm.verdict=?")
            params.append(ai)
        elif ai == "shortlist":
            clauses.append("sm.verdict!='reject'")
    if asset:
        clauses.append("i.asset_type=?")
        params.append(asset)
    if category:
        if category == "vehicles":
            clauses.append("i.asset_type='vehicles'")
        else:
            clauses.append("i.category=?")
            params.append(category)
    if query_text:
        clauses.append(
            "(radar_fold(i.listing_url) LIKE ? OR radar_fold(json_extract(i.data_json,'$.title')) LIKE ? OR radar_fold(json_extract(i.data_json,'$.location')) LIKE ?)"
        )
        params += ["%" + fold_text(query_text) + "%"] * 3
    if location:
        clauses.append("radar_fold(json_extract(i.data_json,'$.location')) LIKE ?")
        params.append("%" + fold_text(location) + "%")
    if source_id:
        clauses.append("(i.source_id=? OR instr(','||i.source_ids||',',','||?||',')>0)")
        params += [source_id, source_id]
    if changed_only or status == "changed":
        clauses.append("i.changed_since_review=1")
    if status in ("pending", "ok", "ko"):
        clauses.append("COALESCE(r.status,'pending')=?")
        params.append(status)
    if has_photos:
        clauses.append("i.photo_count>0")
    for value, operator in ((price_min, ">="), (price_max, "<=")):
        if value is not None:
            condition = "i.price_amount " + operator + " ?"
            clauses.append(
                "(" + condition + " OR i.price_amount IS NULL)"
                if include_unknown_price
                else condition
            )
            params.append(value)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    base = " FROM items i " + joins + where
    total = int(db.fetchone("SELECT COUNT(*) AS c" + base, tuple(params))["c"])
    orders = {
        "updated": "i.last_seen DESC",
        "newest": "i.first_seen DESC",
        "oldest": "i.first_seen ASC",
        "price_asc": "(i.price_amount IS NULL) ASC,i.price_amount ASC",
        "price_desc": "(i.price_amount IS NULL) ASC,i.price_amount DESC",
        "ai_score": "(json_extract(sm.assessment_json,'$.score') IS NULL) ASC,CAST(json_extract(sm.assessment_json,'$.score') AS REAL) DESC"
        if search_id is not None
        else "i.last_seen DESC",
    }
    rows = db.fetchall(
        "SELECT i.*,COALESCE(r.status,'pending') AS review_status"
        + base
        + " ORDER BY "
        + orders[sort]
        + ",i.id ASC LIMIT ? OFFSET ?",
        tuple(params + [limit, offset]),
    )
    return rows, total


def latest_review_for_item(item_id: str) -> Optional[dict]:
    row = db.fetchone(
        "SELECT r.id, r.status, r.notes, r.created_at, r.user_id, u.username "
        "FROM reviews r LEFT JOIN users u ON u.id = r.user_id "
        "WHERE r.item_id = ? ORDER BY r.id DESC LIMIT 1",
        (item_id,),
    )
    if row is None:
        return None
    return {
        "id": int(row["id"]),
        "status": row["status"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "user_id": int(row["user_id"]),
        "username": row["username"],
    }


def list_reviews(item_id: str) -> list[dict]:
    rows = db.fetchall(
        "SELECT r.id, r.status, r.notes, r.created_at, r.acknowledged_fingerprint, u.username "
        "FROM reviews r LEFT JOIN users u ON u.id = r.user_id "
        "WHERE r.item_id = ? ORDER BY r.id ASC",
        (item_id,),
    )
    return [
        {
            "id": int(r["id"]),
            "status": r["status"],
            "notes": r["notes"],
            "created_at": r["created_at"],
            "username": r["username"],
            "acknowledged_fingerprint": r["acknowledged_fingerprint"],
        }
        for r in rows
    ]


def add_review(
    *,
    item_id: str,
    user_id: int,
    status: str,
    notes: str,
    snapshot: dict,
    preserve_notes: bool = False,
) -> int:
    """Insert a review and clear all the substantive-change flags.

    Returns the new review id.
    """
    now = datetime.now(timezone.utc).isoformat()
    snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
    with db.transaction() as conn:
        notes_sql = (
            "COALESCE((SELECT notes FROM reviews WHERE item_id = ? ORDER BY id DESC LIMIT 1), '')"
            if preserve_notes
            else "?"
        )
        cursor = conn.execute(
            "INSERT INTO reviews(item_id, user_id, status, notes, created_at, raw_snapshot_json, acknowledged_fingerprint) "
            "VALUES (?, ?, ?, " + notes_sql + ", ?, ?, ?)",
            (
                item_id,
                user_id,
                status,
                item_id if preserve_notes else notes,
                now,
                snapshot_json,
                json.dumps(snapshot, sort_keys=True, default=str),
            ),
        )
        review_id = int(cursor.lastrowid)
        conn.execute(
            "UPDATE items SET latest_review_id = ?, changed_since_review = 0, "
            "price_changed_since_review = 0, availability_changed_since_review = 0, posession_changed_since_review = 0, "
            "title_changed_since_review = 0, right_sold_changed_since_review = 0, exact_price_changed_since_review = 0 "
            "WHERE id = ?",
            (review_id, item_id),
        )
    return review_id


def last_review_snapshot(item_id: str) -> Optional[dict]:
    if not item_id:
        return None
    row = db.fetchone(
        "SELECT acknowledged_fingerprint FROM reviews WHERE item_id = ? AND acknowledged_fingerprint IS NOT NULL "
        "ORDER BY id DESC LIMIT 1",
        (item_id,),
    )
    if row is None or not row["acknowledged_fingerprint"]:
        return None
    try:
        return json.loads(row["acknowledged_fingerprint"])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Shares
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_share(item_id: str, token: str, created_by: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    token_hash = hash_token(token)
    cursor = db.execute(
        "INSERT INTO shares(item_id, token_hash, created_by, created_at) VALUES (?, ?, ?, ?)",
        (item_id, token_hash, created_by, now),
    )
    share_id = int(cursor.lastrowid)
    if db.workspace_mode() == "private":
        try:
            with db.identity_context() as conn:
                conn.execute(
                    "INSERT INTO public_shares(token_hash,owner_id,created_at) VALUES(?,?,?)",
                    (token_hash, created_by, now),
                )
        except Exception:
            db.execute("DELETE FROM shares WHERE id=?", (share_id,))
            raise
    return share_id


def get_share_by_token(token: str) -> Optional[sqlite3.Row]:
    token_hash = hash_token(token)
    if db.workspace_mode() == "private":
        with db.identity_context() as conn:
            owner = conn.execute(
                "SELECT p.owner_id,p.revoked_at FROM public_shares p JOIN users u ON u.id=p.owner_id "
                "WHERE p.token_hash=? AND u.active=1",
                (token_hash,),
            ).fetchone()
        if owner is None or owner["revoked_at"]:
            return None
        from flask import current_app

        from .workspaces import workspace_data_dir

        if (
            db.current_data_dir().resolve()
            != workspace_data_dir(
                int(owner["owner_id"]), current_app.config["WEBAPP_CONFIG"]
            ).resolve()
        ):
            return None
    return db.fetchone(
        "SELECT id, item_id, created_at, revoked_at FROM shares WHERE token_hash = ?",
        (token_hash,),
    )


def get_share(share_id: int) -> Optional[sqlite3.Row]:
    return db.fetchone(
        "SELECT id, item_id, created_at, created_by, revoked_at FROM shares WHERE id = ?",
        (share_id,),
    )


def list_shares(item_id: str, created_by: Optional[int] = None) -> list[sqlite3.Row]:
    if created_by is None:
        return db.fetchall(
            "SELECT id, created_at, created_by, revoked_at FROM shares WHERE item_id = ? ORDER BY id ASC",
            (item_id,),
        )
    return db.fetchall(
        "SELECT id, created_at, created_by, revoked_at FROM shares WHERE item_id = ? AND created_by = ? ORDER BY id ASC",
        (item_id, created_by),
    )


def revoke_share(share_id: int, revoked_by: int) -> bool:
    row = db.fetchone(
        "SELECT id,item_id,created_by,token_hash,revoked_at FROM shares WHERE id=?", (share_id,)
    )
    if row is None or row["revoked_at"] or int(row["created_by"]) != int(revoked_by):
        return False
    now = datetime.now(timezone.utc).isoformat()
    if db.workspace_mode() == "private":
        with db.identity_context() as conn:
            indexed = conn.execute(
                "SELECT owner_id,revoked_at FROM public_shares WHERE token_hash=?",
                (row["token_hash"],),
            ).fetchone()
            if (
                indexed is None
                or indexed["revoked_at"]
                or int(indexed["owner_id"]) != int(revoked_by)
            ):
                return False
            conn.execute(
                "UPDATE public_shares SET revoked_at=?,revoked_by=? WHERE token_hash=? AND revoked_at IS NULL",
                (now, revoked_by, row["token_hash"]),
            )
    db.execute(
        "UPDATE shares SET revoked_at = ?, revoked_by = ? WHERE id = ? AND revoked_at IS NULL",
        (now, revoked_by, share_id),
    )
    return True


# ---------------------------------------------------------------------------
# Aliases (migration table, additive)
# ---------------------------------------------------------------------------


def add_alias(item_id: str, alias_kind: str, alias_value: str) -> int:
    """Insert a cross-portal alias so the same asset can be re-discovered.

    ``alias_kind`` is a free-form label (``"url"``, ``"reference"``,
    ``"boe"``, ``"cadastral_reference"``); ``alias_value`` is the
    canonical form produced by :func:`auctionradar.cli.canonical_url` (for
    URLs) or by :func:`auctionradar.cli.identities` for the rest.
    """
    if not item_id or not alias_kind or not alias_value:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    cursor = db.execute(
        "INSERT OR IGNORE INTO item_aliases(item_id, alias_kind, alias_value, created_at) "
        "VALUES (?, ?, ?, ?)",
        (item_id, alias_kind, alias_value, now),
    )
    return int(cursor.lastrowid or 0)


def lookup_alias(alias_kind: str, alias_value: str) -> Optional[sqlite3.Row]:
    if not alias_kind or not alias_value:
        return None
    return db.fetchone(
        "SELECT item_id FROM item_aliases WHERE alias_kind = ? AND alias_value = ? ORDER BY id ASC LIMIT 1",
        (alias_kind, alias_value),
    )


def aliases_for_item(item_id: str) -> list[sqlite3.Row]:
    return db.fetchall(
        "SELECT alias_kind, alias_value FROM item_aliases WHERE item_id = ? ORDER BY id",
        (item_id,),
    )


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def create_run(*, run_id: str, created_by: int, sources: list[str]) -> int:
    now = datetime.now(timezone.utc).isoformat()
    summary = json.dumps({"sources": sources, "started": now}, ensure_ascii=False)
    cursor = db.execute(
        "INSERT INTO runs(run_id, status, started_at, summary_json, created_by) VALUES (?, 'queued', ?, ?, ?)",
        (run_id, now, summary, created_by),
    )
    return int(cursor.lastrowid)


def create_run_silent(*, run_id: str, sources: list[str], created_by: Optional[int] = None) -> int:
    """Insert a run without tying it to a specific user (timer mode).

    Used by :mod:`auctionradar.web.runner` when nothing is foreground — the
    timer is the implicit actor. ``created_by`` defaults to ``NULL``.
    """
    now = datetime.now(timezone.utc).isoformat()
    summary = json.dumps({"sources": sources, "started": now}, ensure_ascii=False)
    cursor = db.execute(
        "INSERT OR IGNORE INTO runs(run_id, status, started_at, summary_json, created_by) VALUES (?, 'queued', ?, ?, ?)",
        (run_id, now, summary, created_by),
    )
    return int(cursor.lastrowid or 0)


def update_run_sources(run_id: str, sources: list[str]) -> None:
    summary_row = db.fetchone("SELECT summary_json FROM runs WHERE run_id = ?", (run_id,))
    payload: dict = {}
    if summary_row and summary_row["summary_json"]:
        try:
            payload = json.loads(summary_row["summary_json"])
        except json.JSONDecodeError:
            payload = {}
    payload["sources"] = sources
    db.execute(
        "UPDATE runs SET summary_json = ? WHERE run_id = ?",
        (json.dumps(payload, ensure_ascii=False, default=str), run_id),
    )


def set_run_artifact_path(run_id: str, path: Optional[str]) -> None:
    """Persist where the CLI artefact landed so post-mortem debugging
    can find the JSON without scanning the history directory.
    """
    if not run_id:
        return
    db.execute(
        "UPDATE runs SET summary_json = json_set(COALESCE(summary_json, '{}'), '$.artifact_path', ?) WHERE run_id = ?",
        (path, run_id),
    )


def get_run_artifact_path(run_id: str) -> Optional[str]:
    row = db.fetchone(
        "SELECT json_extract(summary_json, '$.artifact_path') AS path FROM runs WHERE run_id = ?",
        (run_id,),
    )
    if not row:
        return None
    value = row["path"]
    return value if isinstance(value, str) and value else None


def mark_run_started(run_id: str, pid: Optional[int] = None) -> None:
    db.execute(
        "UPDATE runs SET status = 'running', pid = ? WHERE run_id = ? AND status IN ('queued','running')",
        (pid, run_id),
    )


def mark_run_heartbeat(run_id: str) -> None:
    """Touch an extra column to record that the runner is still alive.

    The runner calls this periodically so an external observer (the
    API endpoint ``/api/runs`` and the timer entry-point) can detect a
    stalled or orphaned run even when the stored PID is no longer
    meaningful (for example, the runner process was killed hard and
    its PID got reused).
    """
    db.execute(
        "UPDATE runs SET status = 'running', pid = COALESCE(pid, NULL) WHERE run_id = ?",
        (run_id,),
    )


def finalize_run(
    run_id: str,
    *,
    status: str,
    summary: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    db.execute(
        "UPDATE runs SET status = ?, finished_at = ?, summary_json = ?, error = ? WHERE run_id = ?",
        (
            status,
            datetime.now(timezone.utc).isoformat(),
            json.dumps(summary, ensure_ascii=False, default=str) if summary else None,
            error,
            run_id,
        ),
    )


def list_runs() -> list[sqlite3.Row]:
    return db.fetchall("SELECT * FROM runs ORDER BY id DESC LIMIT 200")


def get_run(run_id: str) -> Optional[sqlite3.Row]:
    return db.fetchone("SELECT * FROM runs WHERE run_id = ?", (run_id,))


def get_latest_run_started_at() -> Optional[str]:
    row = db.fetchone("SELECT finished_at, started_at FROM runs ORDER BY id DESC LIMIT 1")
    if row is None:
        return None
    return row["finished_at"] or row["started_at"]


def get_last_data_update():
    value = get_setting_raw("catalog_last_updated_at")
    if isinstance(value, str):
        return value
    row = db.fetchone(
        "SELECT MAX(finished_at) stamp FROM runs WHERE status IN ('success','partial') AND COALESCE(json_extract(summary_json,'$.ingest.ingested'),0)>0"
    )
    if row and row["stamp"]:
        return row["stamp"]
    row = db.fetchone("SELECT MAX(last_seen) stamp FROM items")
    return row["stamp"] if row else None


def is_any_run_active() -> bool:
    recover_stuck_runs(threshold_seconds=120)
    return is_any_run_actively_progressing()


def active_running_runs() -> list[sqlite3.Row]:
    """Return the rows the views actually want to surface as ``running``.

    The naïve ``status IN ('queued','running')`` query counts rows
    whose runner process is long dead as still-active, which blocks
    the timer and confuses the UI. This helper filters out rows whose
    PID is no longer alive (or has never been recorded).
    """
    rows = db.fetchall("SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY id DESC")
    out = []
    for row in rows:
        pid = row["pid"]
        if pid is not None and not pid_alive(pid):
            continue
        out.append(row)
    return out


def is_any_run_actively_progressing() -> bool:
    """Like :func:`is_any_run_active`, but ignores rows whose PID is dead."""
    return bool(active_running_runs())


def pid_alive(pid: Optional[int]) -> bool:
    """Best-effort check — returns ``False`` for ``None`` or on any error.

    On POSIX the canonical check is ``kill(pid, 0)``; on macOS
    ``os.path.exists(f"/proc/{pid}")`` is unreliable but ``kill -0``
    works there too. We wrap everything in a wide except so the UI
    cannot crash on weird inputs.
    """
    if pid is None or pid <= 0:
        return False
    try:
        import os as _os

        _os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


def stale_running_runs(*, threshold_seconds: int = 7200) -> list[sqlite3.Row]:
    """Return runs that look stuck in ``queued``/``running``.

    We mark a row as stuck when:
    * its status is ``queued`` or ``running``;
    * more than ``threshold_seconds`` have passed since ``started_at``;
    * either no PID is recorded or the PID is no longer alive.

    The runner refuses to start a new run while a stale one exists; the
    API surfaces them so an operator can mark them ``cancelled``.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=threshold_seconds)).isoformat()
    rows = db.fetchall(
        "SELECT * FROM runs WHERE status IN ('queued','running') AND started_at < ? ORDER BY id DESC",
        (cutoff,),
    )
    stuck = []
    for row in rows:
        pid = row["pid"]
        if pid is None or not pid_alive(pid):
            stuck.append(row)
    return stuck


def recover_stuck_runs(*, threshold_seconds: int = 7200) -> int:
    """Mark stale ``queued``/``running`` rows as ``cancelled``.

    Returns the number of rows updated. Safe to call repeatedly; only
    records older than ``threshold_seconds`` are touched.
    """
    rows = stale_running_runs(threshold_seconds=threshold_seconds)
    if not rows:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for row in rows:
        db.execute(
            "UPDATE runs SET status = 'cancelled', finished_at = ?, error = ? WHERE id = ?",
            (now, "recovered as stale after timeout", row["id"]),
        )
        count += 1
    return count


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def set_setting(key: str, value: Any, updated_by: Optional[int]) -> None:
    payload = json.dumps(value, ensure_ascii=False, default=str)
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO settings(key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (key, payload, now, updated_by),
    )


def get_setting_raw(key: str) -> Optional[Any]:
    row = db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return None
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return None


def all_settings() -> dict:
    rows = db.fetchall("SELECT key, value, updated_at, updated_by FROM settings")
    out = {}
    for row in rows:
        try:
            out[row["key"]] = {
                "value": json.loads(row["value"]),
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            }
        except json.JSONDecodeError:
            out[row["key"]] = {
                "value": None,
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
            }
    return out


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def audit_global(
    actor_id: Optional[int],
    actor_username: Optional[str],
    action: str,
    target: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    """Audit authentication and global identity actions centrally."""
    with db.identity_context():
        db.execute(
            "INSERT INTO audit_log(occurred_at, actor_id, actor_username, action, target, details) VALUES (?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                actor_id,
                actor_username,
                action,
                target,
                details,
            ),
        )


def audit(
    actor_id: Optional[int],
    actor_username: Optional[str],
    action: str,
    target: Optional[str] = None,
    details: Optional[str] = None,
) -> None:
    db.execute(
        "INSERT INTO audit_log(occurred_at, actor_id, actor_username, action, target, details) VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.now(timezone.utc).isoformat(),
            actor_id,
            actor_username,
            action,
            target,
            details,
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(row: Optional[sqlite3.Row]) -> dict:
    if row is None:
        return {}
    keys = set(row.keys()) if hasattr(row, "keys") else set()

    def _flag(name: str) -> bool:
        return bool(row[name]) if name in keys else False

    def _value(name: str):
        return row[name] if name in keys else None

    return {
        "changed_since_review": _flag("changed_since_review"),
        "price_changed_since_review": _flag("price_changed_since_review"),
        "availability_changed_since_review": _flag("availability_changed_since_review"),
        "posession_changed_since_review": _flag("posession_changed_since_review"),
        "title_changed_since_review": _flag("title_changed_since_review"),
        "right_sold_changed_since_review": _flag("right_sold_changed_since_review"),
        "exact_price_changed_since_review": _flag("exact_price_changed_since_review"),
        "latest_review_id": _value("latest_review_id"),
    }
