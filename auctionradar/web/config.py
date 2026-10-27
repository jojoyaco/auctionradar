"""Environment-based configuration for the web application.

All secrets and instance paths come from environment variables. The
runtime is required to fail closed when a secret cannot be resolved in
production. Tests inject configuration through :func:`create_app`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = Path(__file__).resolve().parents[1] / "defaults"
DEFAULT_DATA_DIR = Path.cwd() / "instance"


def _read_secret_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Secret file {path} is empty")
    return text


def _resolve_secret(
    env_value: Optional[str], file_env: Optional[str], file_path: Optional[str]
) -> Optional[str]:
    for candidate in (env_value, file_env):
        if candidate:
            return candidate
    if file_path:
        p = Path(file_path)
        if p.exists():
            return _read_secret_file(p)
    return None


@dataclass
class Config:
    SECRET_KEY: str = ""
    SECRET_FILE: Optional[str] = None
    TRUSTED_HOSTS: List[str] = field(default_factory=list)
    DATA_DIR: Path = DEFAULT_DATA_DIR
    WORKSPACE_MODE: str = "legacy"
    REGISTRATION_ENABLED: bool = False
    REGISTRATION_RATELIMIT_WINDOW_SECONDS: int = 3600
    REGISTRATION_RATELIMIT_MAX_ATTEMPTS: int = 5
    IDENTITY_DB_PATH: Path = DEFAULT_DATA_DIR / "identity" / "identity.sqlite3"
    WORKSPACES_DIR: Path = DEFAULT_DATA_DIR / "workspaces"
    LEGACY_OWNER_USERNAME: Optional[str] = None
    LEGACY_OWNER_ID: Optional[int] = None
    LEGACY_OWNER_USER_ID: Optional[int] = None
    PASSWORD_HASH_BYTES: int = 16
    LOGIN_RATELIMIT_WINDOW_SECONDS: int = 300
    LOGIN_RATELIMIT_MAX_ATTEMPTS: int = 6
    RUNNER_TIMEOUT_SECONDS: int = 3600
    RUNNER_DEFAULT_MAX_RUN_SECONDS: int = 1800
    RUNNER_DEFAULT_SOURCES: List[str] = field(default_factory=list)
    RUNNER_DEFAULT_TIMEOUT: float = 12.0
    RUNNER_DEFAULT_MAX_PAGES: int = 2
    RUNNER_DEFAULT_MAX_DETAILS: int = 6
    RUNNER_DEFAULT_MAX_DOCUMENTS: int = 1
    RUNNER_DEFAULT_WORKERS: int = 4
    RUN_LOCK_PATH: Path = DEFAULT_DATA_DIR / "run.lock"
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_HTTPONLY: bool = True
    SESSION_COOKIE_SAMESITE: str = "Lax"
    PERMANENT_SESSION_LIFETIME: int = 7 * 24 * 60 * 60  # 7 days, re-issued on login
    TESTING: bool = False
    ENVIRONMENT: str = "development"

    # Shipped defaults are read-only; workspace configuration is stored separately.
    CATALOG_PATH: Path = CONFIG_DIR / "source-catalog.json"
    PROFILE_PATH: Path = CONFIG_DIR / "search-profile.json"
    FEEDBACK_PATH: Path = CONFIG_DIR / "selection-feedback.json"
    SEEDS_PATH: Path = CONFIG_DIR / "seeds.json"


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def build_config(env: Optional[dict] = None) -> Config:
    """Build a :class:`Config` from ``env`` (defaults to :data:`os.environ`)."""
    env = dict(env if env is not None else os.environ)
    cfg = Config()

    cfg.SECRET_FILE = env.get("AUCTIONRADAR_SECRET_FILE")
    secret = _resolve_secret(
        env.get("AUCTIONRADAR_SECRET_KEY"),
        env.get("AUCTIONRADAR_SECRET_FILE_CONTENTS"),
        cfg.SECRET_FILE,
    )
    if not secret:
        environment = env.get("AUCTIONRADAR_ENV", env.get("FLASK_ENV", "development")).lower()
        is_prod = environment in {"production", "prod"} or _truthy(
            env.get("AUCTIONRADAR_PRODUCTION")
        )
        if is_prod:
            raise RuntimeError(
                "AUCTIONRADAR_SECRET_KEY (or AUCTIONRADAR_SECRET_FILE) is required in production. "
                "The webapp fails closed when no secret is provided."
            )
        # Ephemeral secret acceptable outside production; sessions cannot
        # survive a restart anyway which is exactly what we want for dev.
        import secrets as _secrets

        secret = _secrets.token_urlsafe(48)
    cfg.SECRET_KEY = secret

    if env.get("AUCTIONRADAR_TRUSTED_HOSTS"):
        cfg.TRUSTED_HOSTS = [
            h.strip() for h in env["AUCTIONRADAR_TRUSTED_HOSTS"].split(",") if h.strip()
        ]

    cfg.DATA_DIR = Path(env.get("AUCTIONRADAR_DATA_DIR") or DEFAULT_DATA_DIR)
    cfg.RUN_LOCK_PATH = cfg.DATA_DIR / "run.lock"
    cfg.WORKSPACE_MODE = env.get("AUCTIONRADAR_WORKSPACE_MODE", "legacy").strip().lower()
    if cfg.WORKSPACE_MODE not in {"legacy", "private"}:
        raise RuntimeError("AUCTIONRADAR_WORKSPACE_MODE must be 'legacy' or 'private'")
    cfg.REGISTRATION_ENABLED = _truthy(env.get("AUCTIONRADAR_REGISTRATION_ENABLED"))
    if cfg.REGISTRATION_ENABLED and cfg.WORKSPACE_MODE != "private":
        raise RuntimeError(
            "AUCTIONRADAR_REGISTRATION_ENABLED requires AUCTIONRADAR_WORKSPACE_MODE=private"
        )
    cfg.REGISTRATION_RATELIMIT_WINDOW_SECONDS = int(
        env.get("AUCTIONRADAR_REGISTRATION_RATE_WINDOW", "3600")
    )
    cfg.REGISTRATION_RATELIMIT_MAX_ATTEMPTS = int(
        env.get("AUCTIONRADAR_REGISTRATION_RATE_MAX", "5")
    )
    if cfg.REGISTRATION_RATELIMIT_WINDOW_SECONDS < 1 or cfg.REGISTRATION_RATELIMIT_MAX_ATTEMPTS < 1:
        raise RuntimeError("registration rate limits must be positive integers")
    cfg.IDENTITY_DB_PATH = Path(
        env.get("AUCTIONRADAR_IDENTITY_DB_PATH") or cfg.DATA_DIR / "identity" / "identity.sqlite3"
    )
    cfg.WORKSPACES_DIR = Path(env.get("AUCTIONRADAR_WORKSPACES_DIR") or cfg.DATA_DIR / "workspaces")
    cfg.LEGACY_OWNER_USERNAME = (
        env.get("AUCTIONRADAR_LEGACY_OWNER_USERNAME") or ""
    ).strip() or None
    legacy_owner_id = env.get("AUCTIONRADAR_LEGACY_OWNER_ID")
    try:
        cfg.LEGACY_OWNER_ID = int(legacy_owner_id) if legacy_owner_id else None
    except ValueError as exc:
        raise RuntimeError("AUCTIONRADAR_LEGACY_OWNER_ID must be a positive integer") from exc
    if cfg.LEGACY_OWNER_ID is not None and cfg.LEGACY_OWNER_ID < 1:
        raise RuntimeError("AUCTIONRADAR_LEGACY_OWNER_ID must be a positive integer")
    if cfg.LEGACY_OWNER_USERNAME and cfg.LEGACY_OWNER_ID is not None:
        raise RuntimeError(
            "Configure only one of AUCTIONRADAR_LEGACY_OWNER_USERNAME or AUCTIONRADAR_LEGACY_OWNER_ID"
        )

    cfg.PASSWORD_HASH_BYTES = int(env.get("AUCTIONRADAR_PASSWORD_HASH_BYTES", "16"))
    cfg.LOGIN_RATELIMIT_WINDOW_SECONDS = int(env.get("AUCTIONRADAR_LOGIN_RATE_WINDOW", "300"))
    cfg.LOGIN_RATELIMIT_MAX_ATTEMPTS = int(env.get("AUCTIONRADAR_LOGIN_RATE_MAX", "6"))

    cfg.RUNNER_TIMEOUT_SECONDS = int(env.get("AUCTIONRADAR_RUNNER_TIMEOUT", "3600"))
    cfg.RUNNER_DEFAULT_MAX_RUN_SECONDS = int(env.get("AUCTIONRADAR_RUNNER_BUDGET", "1800"))

    sources_env = env.get("AUCTIONRADAR_RUNNER_SOURCES")
    if sources_env:
        cfg.RUNNER_DEFAULT_SOURCES = [s.strip() for s in sources_env.split(",") if s.strip()]

    cfg.RUNNER_DEFAULT_TIMEOUT = float(env.get("AUCTIONRADAR_RUNNER_TIMEOUT_PER_REQUEST", "12"))
    cfg.RUNNER_DEFAULT_MAX_PAGES = int(env.get("AUCTIONRADAR_RUNNER_MAX_PAGES", "2"))
    cfg.RUNNER_DEFAULT_MAX_DETAILS = int(env.get("AUCTIONRADAR_RUNNER_MAX_DETAILS", "6"))
    cfg.RUNNER_DEFAULT_MAX_DOCUMENTS = int(env.get("AUCTIONRADAR_RUNNER_MAX_DOCUMENTS", "1"))
    cfg.RUNNER_DEFAULT_WORKERS = int(env.get("AUCTIONRADAR_RUNNER_WORKERS", "4"))

    cfg.SESSION_COOKIE_SECURE = _truthy(env.get("AUCTIONRADAR_SESSION_SECURE"))
    cfg.SESSION_COOKIE_HTTPONLY = _truthy(env.get("AUCTIONRADAR_SESSION_HTTPONLY", "true"))
    samesite = env.get("AUCTIONRADAR_SESSION_SAMESITE", "Lax")
    if samesite not in {"Lax", "Strict", "None"}:
        samesite = "Lax"
    cfg.SESSION_COOKIE_SAMESITE = samesite

    cfg.PERMANENT_SESSION_LIFETIME = int(
        env.get("AUCTIONRADAR_SESSION_LIFETIME", str(cfg.PERMANENT_SESSION_LIFETIME))
    )

    environment = env.get("AUCTIONRADAR_ENV", env.get("FLASK_ENV", "development")).lower()
    cfg.ENVIRONMENT = environment
    cfg.TESTING = _truthy(env.get("AUCTIONRADAR_TESTING"))
    if (
        cfg.ENVIRONMENT in {"production", "prod"}
        and cfg.WORKSPACE_MODE == "private"
        and cfg.REGISTRATION_ENABLED
        and not cfg.SESSION_COOKIE_SECURE
    ):
        raise RuntimeError("production registration requires Secure session cookies and HTTPS")

    # Allow tests to override catalog/profile/feedback paths so we can
    # point at fixtures without touching the real CLI data.
    if env.get("AUCTIONRADAR_CATALOG_PATH"):
        cfg.CATALOG_PATH = Path(env["AUCTIONRADAR_CATALOG_PATH"])
    if env.get("AUCTIONRADAR_PROFILE_PATH"):
        cfg.PROFILE_PATH = Path(env["AUCTIONRADAR_PROFILE_PATH"])
    if env.get("AUCTIONRADAR_FEEDBACK_PATH"):
        cfg.FEEDBACK_PATH = Path(env["AUCTIONRADAR_FEEDBACK_PATH"])
    if env.get("AUCTIONRADAR_SEEDS_PATH"):
        cfg.SEEDS_PATH = Path(env["AUCTIONRADAR_SEEDS_PATH"])

    return cfg
