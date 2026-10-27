"""Security primitives: hashing, login decorator, CSRF, headers.

Security controls:

* Password storage using ``werkzeug.security.generate_password_hash``
  with the ``scrypt`` method (the project default for Werkzeug 3.x).
* ``CSRFProtect`` enabled globally; CSRF tokens are issued and validated
  for **every** state-changing request including login.
* Secure session cookies (HttpOnly, configurable SameSite/secure).
* Server-side session revocation via a ``session_version`` column on
  ``users``. When a user is deactivated or changes their password the
  version is bumped; older sessions stop matching and are rejected on
  the next request.
* Login rate limiting backed by the shared SQLite database (workers
  writing concurrently is fine because SQLite serialises writes).
* Defence-in-depth security headers: ``X-Content-Type-Options``,
  ``X-Frame-Options``, ``Referrer-Policy``, restrictive ``CSP``.

External network calls do not happen here. ``werkzeug.security`` uses
``hashlib.scrypt`` from the Python standard library.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Callable, Optional

from flask import (
    Flask,
    g,
    jsonify,
    request,
    session,
)
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.security import check_password_hash, generate_password_hash

from . import db
from .config import Config

csrf = CSRFProtect()


class PrivateLinkLogFilter(logging.Filter):
    """Prevent Flask error messages from exposing link tokens."""

    def filter(self, record):
        text = record.getMessage()
        record.msg = re.sub(r"(/(?:share|api/public)/)[^\s/?\"\']+", r"\1[redacted]", text)
        record.args = ()
        return True


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------


def hash_password(plain: str) -> tuple[str, str]:
    """Return ``(hash, salt)`` using Werkzeug's scrypt implementation.

    We split salt out so we can display them to the manager CLI without
    leaking via the hashing format string variants.
    """
    if not isinstance(plain, str) or not plain:
        raise ValueError("password must be a non-empty string")
    method = "scrypt"
    salt = secrets.token_urlsafe(16)
    digest = generate_password_hash(plain, method=method, salt_length=16)
    return digest, salt


def verify_password(plain: str, stored_hash: str) -> bool:
    if not plain or not stored_hash:
        return False
    try:
        return check_password_hash(stored_hash, plain)
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# User session management
# ---------------------------------------------------------------------------


SESSIONS = {}


def _load_current_user() -> Optional[dict]:
    """Resolve ``session['_user_id']`` against the DB and bump the row.

    Returns ``None`` when the user is missing, inactive, or has changed
    their password since the session was issued.
    """
    user_id = session.get("_user_id")
    session_version = session.get("_user_v")
    if not isinstance(user_id, int):
        return None
    with db.identity_context():
        row = db.fetchone(
            "SELECT id, username, is_admin, active, session_version FROM users WHERE id = ?",
            (user_id,),
        )
    if row is None:
        return None
    if not row["active"]:
        return None
    if session_version != row["session_version"]:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "is_admin": bool(row["is_admin"]),
        "session_version": row["session_version"],
        "is_workspace_owner": db.workspace_mode() == "private",
    }


def load_current_user() -> Optional[dict]:
    """Public hook used by :func:`flask.context_processor`-style helpers."""
    cached = getattr(g, "_current_user_cache", None)
    if cached is not None:
        return cached
    user = _load_current_user()
    g._current_user_cache = user
    return user


def login_user(user: dict) -> None:
    """Mark a Flask session as logged in.

    Stores both the user id and the current ``session_version`` so that
    deactivating the account or rotating the password invalidates every
    outstanding session.
    """
    session.clear()
    session["_user_id"] = int(user["id"])
    session["_user_v"] = int(user["session_version"])
    session["_login_at"] = datetime.now(timezone.utc).isoformat()
    session.permanent = True


def logout_user() -> None:
    session.clear()


# ---------------------------------------------------------------------------
# Decorators
# ---------------------------------------------------------------------------


def login_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        user = load_current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "auth_required"}), 401
            return _redirect_login()
        return view(*args, **kwargs)

    return wrapper


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(view)
    @login_required
    def wrapper(*args: Any, **kwargs: Any):
        user = load_current_user()
        if not user or not user["is_admin"]:
            return jsonify({"error": "forbidden"}), 403
        return view(*args, **kwargs)

    return wrapper


def workspace_owner_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Allow a logged-in private workspace owner, or a legacy admin."""

    @wraps(view)
    def wrapper(*args: Any, **kwargs: Any):
        user = load_current_user()
        if not user:
            if request.path.startswith("/api/"):
                return jsonify({"error": "auth_required"}), 401
            return _redirect_login()
        if db.workspace_mode() != "private" and not user["is_admin"]:
            return jsonify({"error": "forbidden"}), 403
        return view(*args, **kwargs)

    return wrapper


def _redirect_login() -> Any:
    from flask import redirect, url_for

    response = redirect(url_for("webapp.login"))
    response.status_code = 303
    return response


# ---------------------------------------------------------------------------
# Login rate limiting
# ---------------------------------------------------------------------------


def _client_identifier() -> str:
    # Gunicorn listens only on loopback, and nginx overwrites X-Real-IP.
    # Never trust a client-supplied X-Forwarded-For header.
    if request.remote_addr in {"127.0.0.1", "::1"}:
        real_ip = request.headers.get("X-Real-IP", "")
        if real_ip:
            import ipaddress

            try:
                return str(ipaddress.ip_address(real_ip))
            except ValueError:
                pass
    return request.remote_addr or "unknown"


def login_rate_limited(cfg: Config) -> bool:
    """Return ``True`` if the client is currently locked out."""
    identifier = _client_identifier()
    cutoff = datetime.now(timezone.utc).timestamp() - cfg.LOGIN_RATELIMIT_WINDOW_SECONDS
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with db.identity_context():
        row = db.fetchone(
            "SELECT COUNT(*) AS c FROM login_attempts WHERE ip = ? AND occurred_at >= ? AND success = 0",
            (identifier, cutoff_iso),
        )
    failures = int(row["c"]) if row else 0
    return failures >= cfg.LOGIN_RATELIMIT_MAX_ATTEMPTS


def record_login_attempt(username: str, success: bool) -> None:
    with db.identity_context():
        db.execute(
            "INSERT INTO login_attempts(username, ip, success, occurred_at) VALUES (?, ?, ?, ?)",
            (
                username or "",
                _client_identifier(),
                1 if success else 0,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def registration_rate_limited(cfg: Config) -> bool:
    identifier = _client_identifier()
    cutoff = datetime.now(timezone.utc).timestamp() - cfg.REGISTRATION_RATELIMIT_WINDOW_SECONDS
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    with db.identity_context():
        row = db.fetchone(
            "SELECT COUNT(*) AS c FROM registration_attempts WHERE ip=? AND occurred_at>=?",
            (identifier, cutoff_iso),
        )
    return bool(row and int(row["c"]) >= cfg.REGISTRATION_RATELIMIT_MAX_ATTEMPTS)


def consume_registration_attempt(cfg: Config) -> bool:
    """Atomically rate-limit and account for a signup attempt by client IP."""
    identifier = _client_identifier()
    cutoff = datetime.now(timezone.utc).timestamp() - cfg.REGISTRATION_RATELIMIT_WINDOW_SECONDS
    cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
    stamp = datetime.now(timezone.utc).isoformat()
    with db.identity_context():
        with db.transaction() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM registration_attempts WHERE ip=? AND occurred_at>=?",
                (identifier, cutoff_iso),
            ).fetchone()
            limited = bool(row and int(row["c"]) >= cfg.REGISTRATION_RATELIMIT_MAX_ATTEMPTS)
            if not limited:
                conn.execute(
                    "INSERT INTO registration_attempts(ip,occurred_at) VALUES(?,?)",
                    (identifier, stamp),
                )
    return limited


def record_registration_attempt() -> None:
    with db.identity_context():
        db.execute(
            "INSERT INTO registration_attempts(ip,occurred_at) VALUES(?,?)",
            (_client_identifier(), datetime.now(timezone.utc).isoformat()),
        )


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


_DEFAULT_CSP = (
    "default-src 'self'; "
    "img-src 'self' https: http:; "
    "style-src 'self'; "
    "script-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "base-uri 'self'"
)


def apply_security_headers(response, cfg: Config):
    if not request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    # Flask-WTF checks Referer on HTTPS POST requests. Keep it only within
    # the same origin; token links never send a Referer.
    private_link = request.path.startswith(("/share/", "/api/public/"))
    response.headers.setdefault("Referrer-Policy", "no-referrer" if private_link else "same-origin")
    csp = _DEFAULT_CSP
    if private_link:
        from .urls import PUBLIC_SHARE_IMAGE_ORIGINS

        csp = csp.replace(
            "img-src 'self' https: http:;",
            "img-src 'self' " + " ".join(PUBLIC_SHARE_IMAGE_ORIGINS) + ";",
        )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    return response


# ---------------------------------------------------------------------------
# Extension wiring
# ---------------------------------------------------------------------------


def init_security(app: Flask) -> None:
    cfg: Config = app.config["WEBAPP_CONFIG"]

    @app.before_request
    def _require_private_https():
        if (
            cfg.WORKSPACE_MODE == "private"
            and cfg.ENVIRONMENT in {"production", "prod"}
            and not request.is_secure
            and request.path != "/healthz"
            and not request.path.startswith("/static/")
        ):
            return jsonify({"error": "https_required"}), 426
        return None

    if not any(isinstance(f, PrivateLinkLogFilter) for f in app.logger.filters):
        app.logger.addFilter(PrivateLinkLogFilter())

    csrf.init_app(app)

    app.config.setdefault("WTF_CSRF_TIME_LIMIT", None)
    app.config.setdefault("WTF_CSRF_SSL_STRICT", False)

    @app.context_processor
    def _inject_context():
        return {
            "csrf_token": generate_csrf,
            "current_user": load_current_user(),
        }

    @app.after_request
    def _apply_headers(response):
        return apply_security_headers(response, cfg)

    @app.before_request
    def _validate_trusted_hosts():
        hosts = list(cfg.TRUSTED_HOSTS or [])
        if not hosts or app.config.get("TESTING"):
            return None
        from flask import abort

        host = (request.host or "").split(":", 1)[0]
        if host not in hosts:
            abort(400)
        return None

    # Custom CSRF failure handler so JSON callers receive a sensible
    # payload instead of an HTML form error.
    from flask_wtf.csrf import CSRFError

    @app.errorhandler(CSRFError)
    def _csrf_error(error):
        return jsonify({"error": "csrf_invalid", "detail": str(error.description)}), 400


def current_user_dict() -> Optional[dict]:
    return load_current_user()
