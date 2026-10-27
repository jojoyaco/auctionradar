"""AuctionRadar Flask application and private-workspace services.

create_app is the application factory. The manage module provides account and
ingestion commands; runner launches bounded searches through auctionradar.cli.
Portal integrations live in auctionradar.connectors and are invoked explicitly,
never as a side effect of importing the web application."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping, Optional

from flask import Flask

from .config import Config, build_config

__all__ = ["create_app"]


def _load_config(mapping: Optional[Mapping[str, Any]]) -> Config:
    """Translate a Flask ``test_config`` mapping into :class:`Config`."""
    base = build_config()
    if not mapping:
        return base
    for key in (
        "SECRET_KEY",
        "TRUSTED_HOSTS",
        "DATA_DIR",
        "WORKSPACE_MODE",
        "REGISTRATION_ENABLED",
        "REGISTRATION_RATELIMIT_WINDOW_SECONDS",
        "REGISTRATION_RATELIMIT_MAX_ATTEMPTS",
        "IDENTITY_DB_PATH",
        "WORKSPACES_DIR",
        "LEGACY_OWNER_USERNAME",
        "LEGACY_OWNER_ID",
        "LEGACY_OWNER_USER_ID",
        "PASSWORD_HASH_BYTES",
        "LOGIN_RATELIMIT_WINDOW_SECONDS",
        "LOGIN_RATELIMIT_MAX_ATTEMPTS",
        "RUNNER_TIMEOUT_SECONDS",
        "RUNNER_DEFAULT_MAX_RUN_SECONDS",
        "RUNNER_DEFAULT_SOURCES",
        "RUNNER_DEFAULT_TIMEOUT",
        "RUNNER_DEFAULT_MAX_PAGES",
        "RUNNER_DEFAULT_MAX_DETAILS",
        "RUNNER_DEFAULT_MAX_DOCUMENTS",
        "RUNNER_DEFAULT_WORKERS",
        "SESSION_COOKIE_SECURE",
        "SESSION_COOKIE_HTTPONLY",
        "SESSION_COOKIE_SAMESITE",
        "PERMANENT_SESSION_LIFETIME",
        "RUN_LOCK_PATH",
        "TESTING",
        "ENVIRONMENT",
    ):
        if key in mapping:
            setattr(base, key, mapping[key])  # type: ignore[arg-type]
    if "DATA_DIR" in mapping:
        from pathlib import Path

        base.DATA_DIR = Path(mapping["DATA_DIR"])
        if "IDENTITY_DB_PATH" not in mapping:
            base.IDENTITY_DB_PATH = base.DATA_DIR / "identity" / "identity.sqlite3"
        if "WORKSPACES_DIR" not in mapping:
            base.WORKSPACES_DIR = base.DATA_DIR / "workspaces"
    return base


def create_app(test_config: Optional[Mapping[str, Any]] = None) -> Flask:
    """Application factory.

    Parameters
    ----------
    test_config:
        Optional mapping used by the test suite to override defaults.
        Unknown keys are ignored on purpose so tests can pass partial
        configuration safely.
    """
    config = _load_config(test_config)
    if (
        config.WORKSPACE_MODE == "private"
        and config.REGISTRATION_ENABLED
        and config.ENVIRONMENT in {"production", "prod"}
        and not config.SESSION_COOKIE_SECURE
    ):
        raise RuntimeError("production registration requires Secure session cookies and HTTPS")
    data_dir = Path(config.DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)

    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    if os.environ.get("AUCTIONRADAR_TRUST_LOCAL_PROXY", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        from .proxy import LoopbackProxyScheme

        app.wsgi_app = LoopbackProxyScheme(app.wsgi_app)
    app.config["WEBAPP_CONFIG"] = config
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["SESSION_COOKIE_SECURE"] = config.SESSION_COOKIE_SECURE
    app.config["SESSION_COOKIE_HTTPONLY"] = config.SESSION_COOKIE_HTTPONLY
    app.config["SESSION_COOKIE_SAMESITE"] = config.SESSION_COOKIE_SAMESITE
    app.config["PERMANENT_SESSION_LIFETIME"] = config.PERMANENT_SESSION_LIFETIME
    app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024  # 1 MiB; forms are tiny
    app.config["JSON_SORT_KEYS"] = False
    app.config["TESTING"] = bool(config.TESTING)
    app.config["TRUSTED_HOSTS"] = list(config.TRUSTED_HOSTS) or None
    app.config["SESSION_REFRESH_EACH_REQUEST"] = False
    app.config["WTF_CSRF_TIME_LIMIT"] = None

    # Initialize the legacy/home DB or central identity + routed workspaces.
    from . import workspaces

    workspaces.initialize(config)

    # Wire extensions (CSRF, login, security headers, blueprints).
    from .security import init_security

    init_security(app)
    workspaces.init_app(app)

    from .views import bp as api_bp

    app.register_blueprint(api_bp)
    from .search_views import bp as search_bp

    app.register_blueprint(search_bp)
    from .private_media import bp as private_media_bp

    app.register_blueprint(private_media_bp)
    from .connections import bp as connections_bp

    app.register_blueprint(connections_bp)

    @app.before_request
    def _protect_adopted_home_sources():
        from flask import jsonify, request

        if not request.path.startswith(("/connections", "/api/connections/")):
            return None
        cfg = app.config["WEBAPP_CONFIG"]
        if (
            cfg.WORKSPACE_MODE == "private"
            and cfg.LEGACY_OWNER_USER_ID is not None
            and workspaces.current_workspace_user_id() == cfg.LEGACY_OWNER_USER_ID
        ):
            # The old complete catalogue has a richer operator-maintained
            # contract. An empty product-source editor must not replace it.
            return jsonify({"error": "legacy_sources_managed_separately"}), 409
        return None

    # Use Flask 3.x built-in host header validation.
    if app.config["TRUSTED_HOSTS"]:
        app.config["SERVER_NAME"] = None  # keep route builders usable in tests

    @app.errorhandler(404)
    def _not_found(_):
        from flask import jsonify

        return jsonify({"error": "not_found"}), 404

    @app.errorhandler(405)
    def _method_not_allowed(_):
        from flask import jsonify

        return jsonify({"error": "method_not_allowed"}), 405

    @app.errorhandler(500)
    def _server_error(_):
        # Never leak the original exception message — log it server-side only.
        from flask import jsonify

        app.logger.exception("internal error")
        return jsonify({"error": "internal_error"}), 500

    # Friendly health endpoint useful for gunicorn / proxy probes.
    @app.get("/healthz")
    def _healthz():
        from flask import jsonify

        return jsonify({"status": "ok"})

    return app
