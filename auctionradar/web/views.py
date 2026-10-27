"""HTML pages and API endpoints for auction workspaces and read-only sharing."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from auctionradar.assets import ASSET_TYPES

from . import ai_filter, catalog, db, ingest, models, searches, security, urls
from .security import (
    admin_required,
    login_rate_limited,
    login_required,
    login_user,
    logout_user,
    record_login_attempt,
)

bp = Blueprint("webapp", __name__)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HTML pages and API helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _webapp_config():
    from .workspaces import current_config

    return current_config()


def _canonical_url(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return urls.sanitise(value)


def _safe_photo(photo: Any, item_id=None, share_token=None) -> Optional[dict]:
    if not isinstance(photo, dict):
        return None
    if photo.get("media_id") is not None:
        from .private_media import media_photo_payload

        private = media_photo_payload(photo, item_id, token=share_token)
        if private:
            private["url"] = request.host_url.rstrip("/") + private["url"]
        return private
    url = photo.get("url")
    if not urls.is_safe_url(url):
        return None
    return {
        "url": url,
        "caption": photo.get("caption"),
        "source_url": urls.sanitise(photo.get("source_url")),
        "image_type": photo.get("image_type"),
        "origin": photo.get("origin"),
    }


def _safe_document(doc: Any) -> Optional[dict]:
    if not isinstance(doc, dict):
        return None
    url = doc.get("url")
    if not urls.is_safe_url(url):
        return None
    return {
        "url": url,
        "title": doc.get("title"),
        "read_status": doc.get("read_status"),
        "source_url": urls.sanitise(doc.get("source_url")),
    }


def _review_payload(review: Optional[dict]) -> dict:
    if not review:
        return {"status": "pending", "notes": "", "username": None, "updated_at": None}
    return {
        "status": review["status"],
        "notes": review.get("notes") or "",
        "username": review.get("username"),
        "updated_at": review.get("created_at"),
    }


def _read_json_body() -> Optional[dict]:
    if not request.is_json:
        return None
    try:
        payload = request.get_json(force=False, silent=False)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _ensure_csrf_header() -> bool:
    """API requests rely on the same token: ``X-CSRFToken`` or form field."""
    token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    if not token:
        # Browsers send the session cookie; Flask-WTF needs the field for
        # ``CSRFProtect`` to validate. Returning False prompts 400.
        return False
    return True


# ---------------------------------------------------------------------------
# Custom decorator bridging HTML form CSRF and JSON CSRF
# ---------------------------------------------------------------------------


from functools import wraps


def csrf_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        # CSRFProtect processes non-JSON requests automatically. JSON
        # callers MUST send X-CSRFToken matching the value served at
        # ``/api/me``. We accept either to keep the same token working
        # in fetch + form flows.
        return view(*args, **kwargs)

    return wrapper


# ---------------------------------------------------------------------------
# HTML endpoints
# ---------------------------------------------------------------------------


@bp.get("/login")
def login():
    if security.load_current_user():
        return redirect(url_for("webapp.app_root"))
    error = request.args.get("error") or ""
    return render_template(
        "login.html", error=error, registration_enabled=_webapp_config().REGISTRATION_ENABLED
    )


@bp.post("/login")
@csrf_required
def login_submit():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    cfg = _webapp_config()
    if len(username) > 120 or len(password) > 1024:
        return render_template(
            "login.html",
            error="Credenciales no válidas.",
            registration_enabled=cfg.REGISTRATION_ENABLED,
        ), 401

    if login_rate_limited(cfg):
        record_login_attempt(username, success=False)
        return render_template(
            "login.html",
            error="Demasiados intentos. Vuelve a intentarlo en unos minutos.",
            registration_enabled=cfg.REGISTRATION_ENABLED,
        ), 429

    row = models.get_user_by_username(username) if username else None
    if not row or not security.verify_password(password, row["password_hash"]) or not row["active"]:
        record_login_attempt(username, success=False)
        return render_template(
            "login.html",
            error="Credenciales no válidas.",
            registration_enabled=cfg.REGISTRATION_ENABLED,
        ), 401

    record_login_attempt(username, success=True)
    login_user(
        {
            "id": int(row["id"]),
            "username": row["username"],
            "is_admin": bool(row["is_admin"]),
            "session_version": int(row["session_version"]),
        }
    )
    models.audit_global(row["id"], row["username"], "login", target="session")
    return redirect(url_for("webapp.app_root"))


@bp.get("/register")
def register():
    cfg = _webapp_config()
    if not cfg.REGISTRATION_ENABLED or db.workspace_mode() != "private":
        return jsonify({"error": "not_found"}), 404
    if cfg.ENVIRONMENT in {"production", "prod"} and not request.is_secure:
        return jsonify({"error": "https_required"}), 426
    if security.load_current_user():
        return redirect(url_for("webapp.app_root"))
    return render_template("register.html", error=None)


def _register_account(username: Any, password: Any):
    cfg = _webapp_config()
    if not cfg.REGISTRATION_ENABLED or db.workspace_mode() != "private":
        return None, ("not_found", 404)
    if cfg.ENVIRONMENT in {"production", "prod"} and (
        not cfg.SESSION_COOKIE_SECURE or not request.is_secure
    ):
        return None, ("https_required", 426)

    limited = security.consume_registration_attempt(cfg)
    if limited:
        return None, ("registration_unavailable", 429)

    if not isinstance(username, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.-]{2,31}", username.strip()
    ):
        return None, ("invalid_username", 400)
    username = username.strip()
    if not isinstance(password, str) or not 12 <= len(password) <= 128 or "\x00" in password:
        return None, ("invalid_password", 400)

    # Neutral failure detail; case-insensitive uniqueness is also enforced by
    # SQLite for simultaneous signups. Username availability is not a secret.
    if models.get_user_by_username(username) is not None:
        return None, ("registration_unavailable", 409)
    digest, salt = security.hash_password(password)
    try:
        user_id = models.create_user(username, digest, salt, is_admin=False)
    except sqlite3.IntegrityError:
        return None, ("registration_unavailable", 409)
    row = models.get_user_by_id(user_id)
    if row is None:
        return None, ("registration_unavailable", 500)
    user = dict(row)
    from .workspaces import prepare_workspace_user

    prepare_workspace_user(user)
    security.login_user(user)
    models.audit_global(user_id, username, "registration", target="account")
    return user, None


@bp.post("/register")
def register_submit():
    cfg = _webapp_config()
    if not cfg.REGISTRATION_ENABLED or db.workspace_mode() != "private":
        return jsonify({"error": "not_found"}), 404
    if security.load_current_user():
        return redirect(url_for("webapp.app_root"), code=303)
    user, failure = _register_account(request.form.get("username"), request.form.get("password"))
    if failure:
        code, status = failure
        messages = {
            "invalid_username": "El usuario debe tener entre 3 y 32 caracteres: letras, números, punto, guion o guion bajo.",
            "invalid_password": "La contraseña debe tener entre 12 y 128 caracteres.",
            "registration_unavailable": "No se pudo crear la cuenta con esos datos.",
        }
        return render_template(
            "register.html",
            error=messages.get(code, "No se pudo crear la cuenta. Vuelve a intentarlo."),
        ), status
    return redirect(url_for("webapp.app_root"), code=303)


@bp.post("/api/register")
def api_register():
    if security.load_current_user():
        return jsonify({"error": "already_authenticated"}), 403
    if not request.is_json:
        return jsonify({"error": "json_required"}), 400
    payload = _read_json_body() or {}
    user, failure = _register_account(payload.get("username"), payload.get("password"))
    if failure:
        code, status = failure
        if code == "invalid_username":
            return jsonify({"error": code}), status
        if code == "invalid_password":
            return jsonify({"error": code}), status
        return jsonify({"error": code}), status
    return jsonify(
        {"user": {"id": int(user["id"]), "username": user["username"], "is_admin": False}}
    ), 201


@bp.post("/logout")
@csrf_required
def logout():
    user = security.load_current_user()
    if user:
        logout_user()
        models.audit_global(user["id"], user["username"], "logout", target="session")
    return redirect(url_for("webapp.login"))


@bp.get("/")
@login_required
def app_root():
    user = security.load_current_user() or {"username": "?"}
    return render_template("app.html", user=user, current_user=user)


@bp.get("/share/<token>")
def share_page(token: str):
    if not token or len(token) > 256:
        return jsonify({"error": "invalid_token"}), 404
    share = models.get_share_by_token(token)
    if share is None or share["revoked_at"]:
        return jsonify({"error": "share_not_found"}), 404
    return render_template("share.html", token=token)


# ---------------------------------------------------------------------------
# Custom decorator bridging HTML form CSRF and JSON CSRF
# ---------------------------------------------------------------------------


def _enforce_csrf_for_api():
    """Validate CSRF for any non-GET API request without relying on Flask-WTF.

    ``Flask-WTF`` validates only ``request.form`` and JSON only when the
    ``WTF_CSRF_HEADERS`` includes ``X-CSRFToken`` and the JSON body contains
    ``csrf_token`` (post 1.0 we keep things explicit to avoid accidents).
    Since the contract requires the front-end to send ``X-CSRFToken``,
    we validate it manually here using the same helper.
    """
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return None
    token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
    if not token:
        return jsonify({"error": "csrf_missing"}), 400
    from flask_wtf.csrf import validate_csrf

    try:
        validate_csrf(token)
    except Exception:
        return jsonify({"error": "csrf_invalid"}), 400
    return None


@bp.before_request
def _csrf_api_gate():
    if not request.path.startswith("/api/"):
        return None
    err = _enforce_csrf_for_api()
    if err is not None:
        return err
    return None


# ---------------------------------------------------------------------------
# Public + authed API
# ---------------------------------------------------------------------------


@bp.get("/api/me")
def api_me():
    user = security.load_current_user()
    if not user:
        return jsonify({"error": "auth_required"}), 401
    cfg = _webapp_config()
    last_run = models.get_latest_run_started_at()
    return jsonify(
        {
            "user": {
                "id": user["id"],
                "username": user["username"],
                "is_admin": user["is_admin"],
                "is_workspace_owner": user.get("is_workspace_owner", False),
            },
            "schedule": _schedule_info(),
            "schedule_note": (
                "Búsquedas manuales. La programación automática no está habilitada."
                if not _schedule_info()["enabled"]
                else "Inicio de búsqueda de lunes a viernes a las 07:00, Europe/Madrid; incluye festivos. Los resultados aparecen al finalizar."
            ),
            "session": {
                "lifetime_seconds": cfg.PERMANENT_SESSION_LIFETIME,
                "cookie_secure": cfg.SESSION_COOKIE_SECURE,
                "cookie_httponly": cfg.SESSION_COOKIE_HTTPONLY,
                "cookie_samesite": cfg.SESSION_COOKIE_SAMESITE,
            },
            "last_run_at": last_run,
            "last_updated_at": models.get_last_data_update(),
            "branding": {"name": "AuctionRadar"},
            "ai": ai_filter.public_status(),
        }
    )


def _schedule_info():
    cfg = _webapp_config()
    from .workspaces import current_workspace_user_id

    if cfg.WORKSPACE_MODE == "private" and current_workspace_user_id() != cfg.LEGACY_OWNER_USER_ID:
        return {"enabled": False, "timezone": "Europe/Madrid", "description": "Búsquedas manuales"}
    return {"enabled": True, "timezone": "Europe/Madrid", "description": "Lunes a viernes, 07:00"}


@bp.get("/api/public/<token>")
def api_public(token: str):
    if not token or len(token) > 256:
        return jsonify({"error": "invalid_token"}), 404
    share = models.get_share_by_token(token)
    if share is None or share["revoked_at"]:
        return jsonify({"error": "share_not_found"}), 404

    item = models.get_item(share["item_id"])
    if item is None:
        return jsonify({"error": "item_not_found"}), 404
    return jsonify({"item": _public_item_payload(item, token)})


def _public_item_payload(row: Any, share_token=None) -> dict:
    data = json.loads(row["data_json"])
    listing = _safe_listing_payload(data, item_id=row["id"], share_token=share_token, public=True)
    payload = {
        **listing,
        "id": row["id"],
        "title": data.get("title"),
        "asset_type": row["asset_type"],
        "category": row["category"]
        if "category" in row.keys()
        else catalog.derived_fields(data, row["asset_type"])[0],
        "url": urls.sanitise(row["listing_url"]),
        "data": listing,
    }
    if db.workspace_mode() != "private":
        payload.update(
            {
                "source_id": row["source_id"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            }
        )
    else:
        private_fields = _private_field_names(data)
        if private_fields:
            blocked = private_fields
            if {"listing_url", "discovery_url"} & blocked:
                blocked.add("url")
            payload = _remove_marked_public_fields(payload, blocked)
    return payload


def _safe_listing_payload(data: dict, item_id=None, share_token=None, *, public=False) -> dict:
    """Return the *public* slice of a stored listing.

    Review notes, raw published text, internal config and raw evidence
    are excluded. The wrapper exposes what an external link viewer can
    safely read.
    """
    allowed = {
        "category",
        "length_m",
        "beam_m",
        "engine_hours",
        "engine_power",
        "description",
        "detail_text",
        "asset_type",
        "title",
        "url",
        "discovery_url",
        "listing_url",
        "record_type",
        "reference",
        "lot_reference",
        "cadastral_reference",
        "location",
        "built_area_m2",
        "usable_area_m2",
        "bedrooms",
        "bathrooms",
        "construction_year",
        "floor",
        "elevator",
        "make",
        "model",
        "year",
        "mileage_km",
        "fuel",
        "length_class",
        "height_class",
        "currency",
        "prices",
        "exact_price",
        "price_type",
        "all_in_cost_estimate",
        "renovation_cost_estimate",
        "physical_condition",
        "renovation_scope",
        "tenancies",
        "viewing",
        "full_ownership_percentage",
        "charges",
        "fees",
        "transmission",
        "documentation",
        "mechanical_condition",
        "itv",
        "vat_status",
        "availability",
        "possession",
        "right_sold",
        "selection",
    }
    out = {key: data.get(key) for key in allowed if key in data}
    private_public = public and db.workspace_mode() == "private"
    if private_public:
        # Explicitly marked authenticated/certificate-derived values must
        # never be promoted into an anonymous link, even if their field name
        # overlaps the ordinary listing allowlist.
        for field in _private_field_names(data):
            out.pop(field, None)
        if (
            data.get("authenticated_content")
            or data.get("boe_authenticated")
            or data.get("certificate_derived")
        ):
            for field in (
                "description",
                "detail_text",
                "documentation",
                "tenancies",
                "charges",
                "fees",
                "viewing",
            ):
                out.pop(field, None)
        out = _remove_private_public_fields(out)
        # Selection is computed from this workspace's private profile/feedback,
        # not a published property of the auction itself.
        out.pop("selection", None)
    for key in ("url", "discovery_url", "listing_url"):
        if key in out:
            out[key] = urls.sanitise(out[key])
    photos_raw = data.get("photos") or []
    if private_public:
        if "photos" in _private_field_names(data):
            photos_raw = []
        photos_raw = [
            p
            for p in photos_raw
            if not (
                isinstance(p, dict)
                and (
                    p.get("media_id") is not None
                    or any(
                        part in str(p.get("origin", "")).lower()
                        for part in ("authenticated", "certificate", "private")
                    )
                    or p.get("certificate_derived")
                    or p.get("authenticated_content")
                    or p.get("private")
                )
            )
        ]
    out["photos"] = [p for p in (_safe_photo(p, item_id, share_token) for p in photos_raw) if p]
    out["photo_notice"] = None
    if not private_public and data.get("source_id") in ("boe", "aeat"):
        for limitation in data.get("enrichment_limitations") or []:
            if (
                not isinstance(limitation, dict)
                or limitation.get("reason") != "boe_gallery_behind_authentication"
            ):
                continue
            link = urls.sanitise(limitation.get("url"))
            if (
                link
                and urlsplit(link).scheme == "https"
                and urlsplit(link).hostname == "subastas.boe.es"
            ):
                out["photo_notice"] = {"code": "boe_login_required", "source_url": link}
                break
    if any(photo.get("origin") == "boe_authenticated_cache" for photo in out["photos"]):
        out["photo_notice"] = None
    docs_raw = [] if private_public else (data.get("documents") or [])
    out["documents"] = [d for d in (_safe_document(d) for d in docs_raw) if d]
    return out


def _private_field_names(data):
    return {
        field
        for marker in ("private_fields", "authenticated_fields", "certificate_fields")
        for field in (data.get(marker) if isinstance(data.get(marker), list) else [])
        if isinstance(field, str)
    }


def _remove_private_public_fields(value):
    """Recursively drop credential, certificate, authenticated and private fields."""
    blocked = (
        "private",
        "authenticated",
        "certificate",
        "credential",
        "media_id",
        "personal_data",
        "password",
        "secret",
        "token",
        "authorization",
        "cookie",
    )
    excluded = {"notes", "review", "reviews", "user_id", "owner_id", "users"}
    if isinstance(value, dict):
        return {
            key: _remove_private_public_fields(item)
            for key, item in value.items()
            if isinstance(key, str)
            and key.lower() not in excluded
            and not any(fragment in key.lower() for fragment in blocked)
        }
    if isinstance(value, list):
        return [_remove_private_public_fields(item) for item in value]
    return value


def _remove_marked_public_fields(value, blocked):
    """Remove fields explicitly marked private at every public payload level."""
    if isinstance(value, dict):
        return {
            key: _remove_marked_public_fields(item, blocked)
            for key, item in value.items()
            if key not in blocked
        }
    if isinstance(value, list):
        return [_remove_marked_public_fields(item, blocked) for item in value]
    return value


# ---------------------------------------------------------------------------
# Items (authenticated)
# ---------------------------------------------------------------------------


def _parse_status_filter(value: Optional[str]) -> Optional[str]:
    if value in (None, "", "all"):
        return None
    if value in ("pending", "ok", "ko", "changed"):
        return value
    return None


def _counts(rows: list, reviews_for_rows: dict) -> dict:
    """Compute the ``{pending, ok, ko, changed}`` summary for a row batch."""
    counts = {"pending": 0, "ok": 0, "ko": 0, "changed": 0}
    for row in rows:
        review = reviews_for_rows.get(row["id"])
        status = review["status"] if review else "pending"
        if status in counts:
            counts[status] += 1
        if row["changed_since_review"]:
            counts["changed"] += 1
    return counts


def _item_payload(row: Any, review: Optional[dict]) -> dict:
    data = json.loads(row["data_json"])
    payload = {
        **_safe_listing_payload(data, item_id=row["id"]),
        "id": row["id"],
        "title": data.get("title"),
        "asset_type": row["asset_type"],
        "url": urls.sanitise(row["listing_url"]),
        "source_id": row["source_id"],
        "location": data.get("location"),
        "exact_price": data.get("exact_price"),
        "price_type": data.get("price_type"),
        "prices": data.get("prices"),
        "selection": data.get("selection"),
        "possession": data.get("possession"),
        "availability": data.get("availability"),
        "built_area_m2": data.get("built_area_m2"),
        "bedrooms": data.get("bedrooms"),
        "bathrooms": data.get("bathrooms"),
        "mileage_km": data.get("mileage_km"),
        "review": _review_payload(review),
        "changed_since_review": bool(row["changed_since_review"]),
        "price_changed_since_review": bool(row["price_changed_since_review"]),
        "availability_changed_since_review": bool(row["availability_changed_since_review"]),
        "posession_changed_since_review": bool(row["posession_changed_since_review"]),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "source_ids": [s for s in (row["source_ids"] or "").split(",") if s],
        "category": row["category"]
        if "category" in row.keys()
        else catalog.derived_fields(data, row["asset_type"])[0],
        "display_price": row["price_amount"]
        if "price_amount" in row.keys()
        else catalog.published_price(data)[0],
        "display_price_type": row["price_type"]
        if "price_type" in row.keys()
        else catalog.published_price(data)[1],
        "data": _safe_listing_payload(data, item_id=row["id"]),
        "photos": [p for p in (_safe_photo(p, row["id"]) for p in (data.get("photos") or [])) if p],
        "documents": [d for d in (_safe_document(d) for d in (data.get("documents") or [])) if d],
    }
    return payload


def _pagination(page: int, pages: int) -> dict:
    return {"page": page, "pages": pages, "limit": _PAGE_SIZE_DEFAULT}


_PAGE_SIZE_DEFAULT = 25


@bp.get("/api/items")
@login_required
def api_items(search_id_override=None):
    asset = request.args.get("asset")
    if asset == "all":
        asset = None
    if asset is not None and asset not in ASSET_TYPES:
        return jsonify({"error": "invalid_asset"}), 400
    category = request.args.get("category") or None
    if category == "all":
        category = None
    if category is not None and category not in catalog.CATEGORY_IDS:
        return jsonify({"error": "invalid_category"}), 400
    sort = request.args.get("sort", "updated")
    if sort not in catalog.SORTS:
        return jsonify({"error": "invalid_sort"}), 400
    prices = {}
    for key in ("price_min", "price_max"):
        raw = request.args.get(key)
        prices[key] = catalog.amount(raw) if raw not in (None, "") else None
        if raw not in (None, "") and prices[key] is None:
            return jsonify({"error": "invalid_price_range"}), 400
    if (
        prices["price_min"] is not None
        and prices["price_max"] is not None
        and prices["price_min"] > prices["price_max"]
    ):
        return jsonify({"error": "invalid_price_range"}), 400
    search_id = search_id_override or request.args.get("search_id") or None
    if search_id is not None:
        try:
            search_id = int(search_id)
        except ValueError:
            return jsonify({"error": "invalid_search"}), 400
        if searches.get_search(search_id, security.load_current_user()["id"]) is None:
            return jsonify({"error": "not_found"}), 404
    ai = request.args.get("ai", "all")
    if ai not in ("all", "shortlist", "keep", "reject", "needs_review"):
        return jsonify({"error": "invalid_ai_filter"}), 400
    status = _parse_status_filter(request.args.get("status"))
    query_text = (request.args.get("q") or "").strip() or None
    source_id = (request.args.get("source") or "").strip() or None
    changed = request.args.get("changed") in ("1", "true", "yes")
    try:
        page = max(1, int(request.args.get("page") or "1"))
    except ValueError:
        page = 1

    limit = _PAGE_SIZE_DEFAULT
    offset = (page - 1) * limit
    if "offset" in request.args:
        try:
            offset = int(request.args["offset"])
            if not 0 <= offset <= 1_000_000:
                raise ValueError
        except ValueError:
            return jsonify({"error": "invalid_offset"}), 400
        page = offset // limit + 1
    options = {
        "category": category,
        "sort": sort,
        **prices,
        "location": request.args.get("location") or None,
        "has_photos": request.args.get("has_photos") in ("1", "true"),
        "include_unknown_price": request.args.get("include_unknown_price") in ("1", "true"),
        "search_id": search_id,
        "ai": ai,
    }

    rows, total = models.iter_items(
        asset=asset,
        status=status,
        query_text=query_text,
        source_id=source_id,
        changed_only=changed,
        limit=limit,
        offset=offset,
        **options,
    )

    review_map = {r["id"]: models.latest_review_for_item(r["id"]) for r in rows}
    counts = _counts(rows, review_map)

    # When the user filters by ``status`` we still want total counts to
    # reflect the selected asset (not the current page slice) so the UI
    # can render badges correctly. We issue a cheap aggregate.
    aggregate_rows, _ = models.iter_items(
        asset=asset,
        status=None,
        query_text=query_text,
        source_id=source_id,
        changed_only=False,
        limit=10_000,
        offset=0,
        **options,
    )
    aggregate_review_map = {r["id"]: models.latest_review_for_item(r["id"]) for r in aggregate_rows}
    aggregate_counts = _counts(aggregate_rows, aggregate_review_map)

    pages = max(1, (total + limit - 1) // limit)
    result_items = [_item_payload(r, review_map.get(r["id"])) for r in rows]
    if search_id is not None:
        for item in result_items:
            item["ai"] = searches.assessment(search_id, item["id"])
    return jsonify(
        {
            "items": result_items,
            "total": total,
            "page": page,
            "pages": pages,
            "offset": offset,
            "next_offset": offset + len(rows),
            "has_more": offset + len(rows) < total,
            "counts": aggregate_counts,
            "page_counts": counts,
            "last_run": models.get_last_data_update(),
            "last_updated_at": models.get_last_data_update(),
            "sort": sort,
        }
    )


@bp.get("/api/items/<item_id>")
@login_required
def api_item(item_id: str):
    row = models.get_item(item_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    history = models.list_reviews(item_id)
    user = security.load_current_user()
    shares = [
        {
            "id": int(s["id"]),
            "created_at": s["created_at"],
            "created_by": int(s["created_by"]) if s["created_by"] else None,
            "revoked": bool(s["revoked_at"]),
            "revoked_at": s["revoked_at"],
            "url": None,  # The random token is shown only when the link is created.
        }
        for s in models.list_shares(item_id, created_by=user["id"])
    ]
    review = models.latest_review_for_item(item_id)
    item_payload = _item_payload(row, review)
    search_id = request.args.get("search_id")
    if search_id:
        try:
            search_id = int(search_id)
        except ValueError:
            return jsonify({"error": "invalid_search"}), 400
        if searches.get_search(search_id, security.load_current_user()["id"]) is None:
            return jsonify({"error": "not_found"}), 404
        item_payload["ai"] = searches.assessment(search_id, item_id)
    return jsonify(
        {
            "item": item_payload,
            "history": history,
            "shares": shares,
        }
    )


@bp.post("/api/items/<item_id>/review")
@login_required
def api_review_item(item_id: str):
    payload = _read_json_body() or {}
    if "preserve_notes" in payload and not isinstance(payload["preserve_notes"], bool):
        return jsonify({"error": "invalid_preserve_notes"}), 400
    status = payload.get("status")
    if not isinstance(status, str) or status not in {"pending", "ok", "ko"}:
        return jsonify({"error": "invalid_status"}), 400
    notes = payload.get("notes") or ""
    if not isinstance(notes, str):
        return jsonify({"error": "invalid_notes"}), 400
    if len(notes) > 4000:
        return jsonify({"error": "notes_too_long"}), 400

    row = models.get_item(item_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404

    user = security.load_current_user()
    data = json.loads(row["data_json"])
    snapshot = ingest.review_snapshot(data)
    review_id = models.add_review(
        item_id=item_id,
        user_id=user["id"],
        status=status,
        notes=notes,
        snapshot=snapshot,
        preserve_notes=payload.get("preserve_notes", False),
    )
    models.audit(
        user["id"],
        user["username"],
        "review",
        target=item_id,
        details=str({"status": status, "review_id": review_id}),
    )

    fresh = models.get_item(item_id)
    review = models.latest_review_for_item(item_id)
    return jsonify({"item": _item_payload(fresh, review)})


# ---------------------------------------------------------------------------
# Sharing
# ---------------------------------------------------------------------------


def _share_token_string() -> str:
    """Return a fresh, opaque, URL-safe token (32 bytes from ``secrets``)."""
    return secrets.token_urlsafe(32)


@bp.post("/api/items/<item_id>/share")
@login_required
def api_share(item_id: str):
    row = models.get_item(item_id)
    if row is None:
        return jsonify({"error": "not_found"}), 404
    user = security.load_current_user()
    # The token stored in the DB is the hashed form of the opaque token.
    # Returning the opaque token in the URL gives the user a revocable
    # secret without exposing the hashed column.
    opaque = _share_token_string()
    share_id = models.create_share(item_id, opaque, user["id"])
    models.audit(
        user["id"],
        user["username"],
        "share_create",
        target=item_id,
        details=str({"share_id": share_id}),
    )
    # Note: the URL deliberately does not contain the share id — clients
    # can resolve it via /api/items/<id>; the opaque token is enough.
    public_path = f"/share/{opaque}"
    return jsonify({"id": share_id, "url": public_path, "opaque": opaque, "share_id": share_id})


@bp.post("/api/shares/<int:share_id>/revoke")
@login_required
def api_revoke_share(share_id: int):
    user = security.load_current_user()
    ok = models.revoke_share(share_id, user["id"])
    if not ok:
        return jsonify({"error": "not_found_or_revoked"}), 404
    models.audit(user["id"], user["username"], "share_revoke", target=str(share_id))
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _read_catalog(config=None) -> dict:
    cfg = config or _webapp_config()
    try:
        with open(cfg.CATALOG_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {"sources": []}


def _build_settings_response(catalog: dict, stored: dict) -> dict:
    cfg = _webapp_config()
    sources_index = {s.get("id"): s for s in catalog.get("sources", []) if isinstance(s, dict)}
    sources_payload = []
    for sid, source in sources_index.items():
        sources_payload.append(
            {
                "id": sid,
                "name": source.get("name"),
                "enabled": bool(source.get("enabled", True)),
                "asset_types": source.get("asset_types", []),
            }
        )

    try:
        profile = json.loads(Path(cfg.PROFILE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        profile = {}
    housing = stored.get("housing") or {}
    cli = stored.get("cli") or {}
    canonical_housing = profile.get("housing") or {}
    preferences = [
        r.get("name", "") if isinstance(r, dict) else str(r)
        for r in canonical_housing.get("preferred_regions", [])
    ]
    settings = {
        "housing_budget": housing.get(
            "budget_max_all_in", canonical_housing.get("budget", {}).get("max_all_in")
        ),
        "preferred_regions": housing.get("preferred_regions", preferences),
        "regions": stored.get("regions") or [],
        "vehicle_budget": (stored.get("vehicles") or {}).get("max_vehicle_price"),
        "vehicle_max_mileage": (stored.get("vehicles") or {}).get("max_mileage_km"),
        "sources": {
            sid: {
                "enabled": ((stored.get("sources") or {}).get(sid) or {}).get(
                    "enabled", bool(source.get("enabled", True))
                )
            }
            for sid, source in sources_index.items()
        },
        "timeout": cli.get("timeout")
        if cli.get("timeout") is not None
        else cfg.RUNNER_DEFAULT_TIMEOUT,
        "max_pages": cli.get("max_pages")
        if cli.get("max_pages") is not None
        else cfg.RUNNER_DEFAULT_MAX_PAGES,
        "max_details": cli.get("max_details")
        if cli.get("max_details") is not None
        else cfg.RUNNER_DEFAULT_MAX_DETAILS,
        "max_documents": cli.get("max_documents")
        if cli.get("max_documents") is not None
        else cfg.RUNNER_DEFAULT_MAX_DOCUMENTS,
        "workers": cli.get("workers")
        if cli.get("workers") is not None
        else cfg.RUNNER_DEFAULT_WORKERS,
        "max_run_seconds": cli.get("max_run_seconds")
        if cli.get("max_run_seconds") is not None
        else cfg.RUNNER_DEFAULT_MAX_RUN_SECONDS,
    }
    regions_catalog = [
        {"code": region.get("code"), "name": region.get("name")} for region in _regions_catalog()
    ]
    return {
        "settings": settings,
        "sources": sources_payload,
        "regions_catalog": regions_catalog,
        "ai": ai_filter.public_status(),
        "schedule": _schedule_info(),
        "limits": {
            "default_max_run_seconds": cfg.RUNNER_DEFAULT_MAX_RUN_SECONDS,
            "default_timeout": cfg.RUNNER_DEFAULT_TIMEOUT,
            "default_max_pages": cfg.RUNNER_DEFAULT_MAX_PAGES,
            "default_max_details": cfg.RUNNER_DEFAULT_MAX_DETAILS,
            "default_max_documents": cfg.RUNNER_DEFAULT_MAX_DOCUMENTS,
            "default_workers": cfg.RUNNER_DEFAULT_WORKERS,
        },
    }


@bp.get("/api/settings")
@security.workspace_owner_required
def api_settings_get():
    catalog = _read_catalog()
    raw = models.get_setting_raw("settings") or {}
    return jsonify(_build_settings_response(catalog, raw))


@bp.get("/api/sources")
@login_required
def api_sources_get():
    recent = models.list_runs()
    health = {s["source_id"]: s for s in _serialise_run(recent[0])["sources"]} if recent else {}
    return jsonify(
        {
            "sources": [
                {
                    "id": s.get("id"),
                    "name": s.get("name"),
                    "enabled": bool(s.get("enabled", True)),
                    "asset_types": s.get("asset_types", []),
                    "health": health.get(s["id"], {}).get("health"),
                    "last_status": health.get(s["id"], {}).get("status"),
                    "last_items_count": health.get(s["id"], {}).get("items_count"),
                    "last_depth": health.get(s["id"], {}).get("depth"),
                }
                for s in _read_catalog().get("sources", [])
            ]
        }
    )


@bp.put("/api/settings")
@security.workspace_owner_required
def api_settings_put():
    payload = _read_json_body() or {}
    settings_in = payload.get("settings")
    if not isinstance(settings_in, dict):
        return jsonify({"error": "settings_required"}), 400
    catalog = _read_catalog()
    current = _build_settings_response(catalog, models.get_setting_raw("settings") or {})[
        "settings"
    ]
    settings_in = {**current, **settings_in}
    errors = []
    new_settings = {
        "housing": {
            "budget_max_all_in": _as_number(
                settings_in.get("housing_budget"), errors, name="housing_budget"
            ),
            "preferred_regions": _as_list_of_str(
                settings_in.get("preferred_regions"), errors, name="preferred_regions"
            ),
        },
        "vehicles": {
            "max_vehicle_price": _as_number(
                settings_in.get("vehicle_budget"), errors, name="vehicle_budget"
            ),
            "max_mileage_km": _as_number(
                settings_in.get("vehicle_max_mileage"),
                errors,
                name="vehicle_max_mileage",
                integer=True,
            ),
        },
        "regions": _as_list_of_str(settings_in.get("regions"), errors, name="regions"),
        "cli": {
            "timeout": _as_number(settings_in.get("timeout"), errors, name="timeout"),
            "max_pages": _as_int(settings_in.get("max_pages"), errors, name="max_pages"),
            "max_details": _as_int(settings_in.get("max_details"), errors, name="max_details"),
            "max_documents": _as_int(
                settings_in.get("max_documents"), errors, name="max_documents"
            ),
            "workers": _as_int(settings_in.get("workers"), errors, name="workers"),
            "max_run_seconds": _as_int(
                settings_in.get("max_run_seconds"), errors, name="max_run_seconds"
            ),
        },
        "sources": {},
    }

    catalog = _read_catalog()
    catalog_ids = {s.get("id") for s in catalog.get("sources", []) if isinstance(s, dict)}
    sources_in = settings_in.get("sources")
    if isinstance(sources_in, dict):
        for sid, state in sources_in.items():
            if sid not in catalog_ids:
                errors.append(f"unknown_source:{sid}")
                continue
            if not isinstance(state, dict):
                errors.append(f"invalid_source:{sid}")
                continue
            if not isinstance(state.get("enabled"), bool):
                errors.append(f"invalid_source_state:{sid}")
                continue
            new_settings["sources"][sid] = {"enabled": state["enabled"]}
    else:
        errors.append("invalid:sources")

    bounds = {
        "timeout": (1, 60),
        "max_pages": (1, 20),
        "max_details": (1, 100),
        "max_documents": (0, 5),
        "workers": (1, 12),
        "max_run_seconds": (60, 3600),
    }
    for key, (low, high) in bounds.items():
        value = new_settings["cli"][key]
        if value is None or not low <= value <= high:
            errors.append(f"out_of_bounds:{key}")
    for key in ("housing_budget", "vehicle_budget", "vehicle_max_mileage"):
        value = settings_in.get(key)
        if value not in (None, ""):
            parsed = _as_number(value, errors, name=key)
            if parsed is not None and not 0 < parsed <= 1_000_000_000:
                errors.append(f"out_of_bounds:{key}")
    if catalog_ids and not any(st.get("enabled") for st in new_settings["sources"].values()):
        errors.append("at_least_one_source_required")

    if errors:
        return jsonify({"error": "validation_failed", "details": errors}), 400

    user = security.load_current_user()
    models.set_setting("settings", new_settings, updated_by=user["id"])
    models.audit(user["id"], user["username"], "settings_update", target="settings")
    return jsonify(_build_settings_response(catalog, new_settings))


def _as_number(value, errors, *, name, integer=False):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        errors.append(f"invalid:{name}")
        return None
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            errors.append(f"invalid:{name}")
            return None
        if integer and not float(value).is_integer():
            errors.append(f"invalid:{name}")
            return None
        return value
    if isinstance(value, str):
        try:
            number = float(value)
            if not math.isfinite(number):
                errors.append(f"invalid:{name}")
                return None
            if integer and not number.is_integer():
                errors.append(f"invalid:{name}")
                return None
            return number
        except ValueError:
            errors.append(f"invalid:{name}")
            return None
    errors.append(f"invalid:{name}")
    return None


def _as_int(value, errors, *, name):
    if value in (None, ""):
        return None
    number = _as_number(value, errors, name=name, integer=True)
    return int(number) if number is not None else None


def _as_list_of_str(value, errors, *, name):
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"invalid:{name}")
        return []
    out = []
    for item in value:
        if not isinstance(item, str):
            errors.append(f"invalid:{name}")
            return []
        item = item.strip()
        if not item or len(item) > 100 or len(value) > 52:
            errors.append(f"invalid:{name}")
            return []
        if item not in out:
            out.append(item)
    return out


# Pull ``regions_catalog`` from the canonical profile if available.
def _regions_catalog() -> list:
    # Initial preferences are not the catalog of all regions.
    return [
        {"code": code, "name": name}
        for code, name in (
            ("ES-AN", "Andalucía"),
            ("ES-AR", "Aragón"),
            ("ES-AS", "Asturias"),
            ("ES-IB", "Illes Balears"),
            ("ES-CN", "Canarias"),
            ("ES-CB", "Cantabria"),
            ("ES-CL", "Castilla y León"),
            ("ES-CM", "Castilla-La Mancha"),
            ("ES-CT", "Cataluña"),
            ("ES-VC", "Comunitat Valenciana"),
            ("ES-EX", "Extremadura"),
            ("ES-GA", "Galicia"),
            ("ES-MD", "Comunidad de Madrid"),
            ("ES-MC", "Región de Murcia"),
            ("ES-NC", "Navarra"),
            ("ES-PV", "País Vasco"),
            ("ES-RI", "La Rioja"),
            ("ES-CE", "Ceuta"),
            ("ES-ML", "Melilla"),
        )
    ]


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@bp.get("/api/users")
@admin_required
def api_users_get():
    rows = models.list_users()
    payload = [
        {
            "id": int(r["id"]),
            "username": r["username"],
            "is_admin": bool(r["is_admin"]),
            "active": bool(r["active"]),
            "created_at": r["created_at"],
            "last_password_change": r["last_password_change"],
        }
        for r in rows
    ]
    return jsonify({"users": payload})


@bp.post("/api/users")
@admin_required
def api_users_create():
    payload = _read_json_body() or {}
    username = payload.get("username")
    if not isinstance(username, str):
        return jsonify({"error": "invalid_username"}), 400
    username = username.strip()
    password = payload.get("password")
    is_admin = payload.get("is_admin", False)
    if not isinstance(is_admin, bool):
        return jsonify({"error": "invalid_role"}), 400
    if not username or len(username) > 120:
        return jsonify({"error": "invalid_username"}), 400
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        return jsonify({"error": "password_too_short"}), 400
    if models.get_user_by_username(username) is not None:
        return jsonify({"error": "username_taken"}), 409
    digest, salt = security.hash_password(password)
    user_id = models.create_user(username, digest, salt, is_admin=is_admin)
    actor = security.load_current_user()
    models.audit_global(actor["id"], actor["username"], "user_create", target=username)
    return jsonify({"id": user_id, "username": username, "is_admin": is_admin}), 201


@bp.patch("/api/users/<int:user_id>")
@admin_required
def api_users_patch(user_id: int):
    payload = _read_json_body() or {}
    if "active" in payload and not isinstance(payload["active"], bool):
        return jsonify({"error": "invalid_active"}), 400
    if "password" in payload and (
        not isinstance(payload["password"], str) or not 12 <= len(payload["password"]) <= 1024
    ):
        return jsonify({"error": "password_too_short"}), 400
    actor = security.load_current_user()
    target = models.get_user_by_id(user_id)
    if target is None:
        return jsonify({"error": "not_found"}), 404
    if "active" in payload:
        if not isinstance(payload["active"], bool):
            return jsonify({"error": "invalid_active"}), 400
        new_state = payload["active"]
        if not new_state and target["is_admin"] and target["active"]:
            if models.count_active_admins() <= 1:
                return jsonify({"error": "cannot_deactivate_last_admin"}), 400
        if target["id"] == actor["id"] and target["is_admin"]:
            if not new_state:
                return jsonify({"error": "cannot_deactivate_self"}), 400
        if new_state:
            db.execute("UPDATE users SET active = 1 WHERE id = ?", (user_id,))
        else:
            models.deactivate_user(user_id)
        models.audit_global(
            actor["id"],
            actor["username"],
            "user_toggle",
            target=target["username"],
            details=str({"active": new_state}),
        )
    if "password" in payload:
        new_password = payload["password"]
        if not isinstance(new_password, str) or not 12 <= len(new_password) <= 1024:
            return jsonify({"error": "password_too_short"}), 400
        digest, salt = security.hash_password(new_password)
        models.set_password(user_id, digest, salt)
        models.audit_global(
            actor["id"], actor["username"], "user_password_reset", target=target["username"]
        )
    fresh = models.get_user_by_id(user_id)
    return jsonify(
        {
            "id": int(fresh["id"]),
            "username": fresh["username"],
            "is_admin": bool(fresh["is_admin"]),
            "active": bool(fresh["active"]),
        }
    )


@bp.post("/api/password")
@login_required
def api_password_change():
    payload = _read_json_body() or {}
    current_password = payload.get("current_password") or ""
    new_password = payload.get("new_password") or ""
    if not isinstance(current_password, str):
        return jsonify({"error": "invalid_current_password"}), 400
    if not isinstance(new_password, str) or not 12 <= len(new_password) <= 1024:
        return jsonify({"error": "password_too_short"}), 400
    user = security.load_current_user()
    row = models.get_user_by_id(user["id"])
    if row is None or not security.verify_password(current_password, row["password_hash"]):
        return jsonify({"error": "invalid_current_password"}), 401
    digest, salt = security.hash_password(new_password)
    models.change_password(user["id"], digest, salt)
    # Bumping session_version invalidates *this* session too; force re-login.
    logout_user()
    models.audit_global(user["id"], user["username"], "password_change", target="self")
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def _serialise_run(row: Any) -> dict:
    from .run_presentation import duration_seconds, present_summary

    summary = None
    if row["summary_json"]:
        try:
            summary = json.loads(row["summary_json"])
        except json.JSONDecodeError:
            summary = {"raw": row["summary_json"]}
    summary = present_summary(
        summary, row["status"], {s["id"]: s.get("name") for s in _read_catalog().get("sources", [])}
    )
    return {
        "id": int(row["id"]),
        "run_id": row["run_id"],
        "status": row["status"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "summary": summary,
        "sources": (summary or {}).get("sources"),
        "error": row["error"],
        "pid": row["pid"],
        "duration_seconds": duration_seconds(row["started_at"], row["finished_at"]),
        "health": summary.get("health"),
        "common_limitations": summary.get("common_limitations", []),
    }


@bp.get("/api/runs")
@login_required
def api_runs_get():
    rows = models.list_runs()
    running = models.is_any_run_active()
    return jsonify(
        {
            "runs": [_serialise_run(r) for r in rows],
            "running": running,
        }
    )


@bp.post("/api/runs")
@security.workspace_owner_required
def api_runs_post():
    cfg = _webapp_config()
    raw_settings = models.get_setting_raw("settings") or {}
    cli_overlay = raw_settings.get("cli") or {}
    sources = cfg.RUNNER_DEFAULT_SOURCES
    sources_setting = raw_settings.get("sources") or {}
    enabled_sources = (
        [sid for sid, st in sources_setting.items() if st.get("enabled", True)]
        if sources_setting
        else sources
    )
    if not enabled_sources:
        enabled_sources = [
            s.get("id") for s in _read_catalog(cfg).get("sources", []) if s.get("enabled", True)
        ]
    if cfg.WORKSPACE_MODE == "private":
        catalog_sources = _read_catalog(cfg).get("sources", [])
        enabled_sources = [
            source["id"]
            for source in catalog_sources
            if (sources_setting.get(source["id"]) or {}).get(
                "enabled", source.get("enabled", False)
            )
        ]
        if not enabled_sources:
            return jsonify({"error": "no_sources_configured"}), 400

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(4)
    user = security.load_current_user()
    with db.transaction():
        if models.is_any_run_active():
            return jsonify({"error": "run_already_active"}), 409
        models.create_run(run_id=run_id, created_by=user["id"], sources=list(enabled_sources))

    # Detach via subprocess.Popen — no shell, no user input.
    from .config import PROJECT_ROOT as _PROJECT_ROOT

    env = {
        "AUCTIONRADAR_DATA_DIR": str(cfg.DATA_DIR),
        "AUCTIONRADAR_CATALOG_PATH": str(cfg.CATALOG_PATH),
        "AUCTIONRADAR_PROFILE_PATH": str(cfg.PROFILE_PATH),
        "AUCTIONRADAR_FEEDBACK_PATH": str(cfg.FEEDBACK_PATH),
        "AUCTIONRADAR_SEEDS_PATH": str(cfg.SEEDS_PATH),
        "PYTHONPATH": str(_PROJECT_ROOT),
        "AUCTIONRADAR_RUN_ID": run_id,
        "AUCTIONRADAR_OUTPUT_DIR": str(Path(cfg.DATA_DIR) / "runs"),
        "AUCTIONRADAR_HISTORY_DIR": str(Path(cfg.DATA_DIR) / "runs"),
        "AUCTIONRADAR_PROFILE_OVERLAY": json.dumps(raw_settings or {}),
    }
    # Carry through trusted AUCTIONRADAR_* settings (no shell injection — every value
    # comes from the validated settings overlay, never from request data).
    for k, v in (
        (
            "AUCTIONRADAR_RUNNER_TIMEOUT",
            str(cli_overlay.get("timeout") or cfg.RUNNER_DEFAULT_TIMEOUT),
        ),
        (
            "AUCTIONRADAR_RUNNER_MAX_PAGES",
            str(cli_overlay.get("max_pages") or cfg.RUNNER_DEFAULT_MAX_PAGES),
        ),
        (
            "AUCTIONRADAR_RUNNER_MAX_DETAILS",
            str(cli_overlay.get("max_details") or cfg.RUNNER_DEFAULT_MAX_DETAILS),
        ),
        (
            "AUCTIONRADAR_RUNNER_MAX_DOCUMENTS",
            str(cli_overlay.get("max_documents") or cfg.RUNNER_DEFAULT_MAX_DOCUMENTS),
        ),
        (
            "AUCTIONRADAR_RUNNER_WORKERS",
            str(cli_overlay.get("workers") or cfg.RUNNER_DEFAULT_WORKERS),
        ),
        (
            "AUCTIONRADAR_RUNNER_BUDGET",
            str(cli_overlay.get("max_run_seconds") or cfg.RUNNER_DEFAULT_MAX_RUN_SECONDS),
        ),
        ("AUCTIONRADAR_RUNNER_SOURCES", ",".join(enabled_sources)),
    ):
        env[k] = v
    for k, v in os.environ.items():
        env.setdefault(k, v)
    from .worker_environment import for_workspace

    env = for_workspace(cfg, environ=env)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "auctionradar.web.runner", "--run-id", run_id],
            cwd=str(_PROJECT_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        models.mark_run_started(run_id, pid=proc.pid)
    except OSError:
        models.finalize_run(
            run_id, status="error", error="No se pudo iniciar el proceso de búsqueda."
        )
        return jsonify({"error": "runner_unavailable"}), 503
    return jsonify({"run_id": run_id, "pid": proc.pid}), 202
