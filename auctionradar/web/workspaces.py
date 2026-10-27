"""Private per-user storage and request-scoped workspace routing."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from copy import copy
from pathlib import Path
from typing import Optional

from flask import Flask, current_app, g, request

from . import db


def initialize(config) -> None:
    """Initialize storage without copying shared configuration into accounts."""
    db.configure_runtime(config.WORKSPACE_MODE, config.IDENTITY_DB_PATH)
    db.init_db(config.DATA_DIR)
    if config.WORKSPACE_MODE != "private":
        return

    legacy_db = (Path(config.DATA_DIR).expanduser().resolve() / "app.sqlite3").resolve()
    identity_db = Path(config.IDENTITY_DB_PATH).expanduser().resolve()
    if legacy_db == identity_db:
        raise RuntimeError("identity database must be separate from the legacy workspace database")
    if config.WORKSPACES_DIR is None:
        raise RuntimeError("private workspace mode requires a workspace directory")
    config.WORKSPACES_DIR = Path(config.WORKSPACES_DIR).expanduser().resolve()
    config.IDENTITY_DB_PATH = identity_db
    db.configure_runtime(config.WORKSPACE_MODE, identity_db)
    data_root = Path(config.DATA_DIR).expanduser().resolve()
    if config.WORKSPACES_DIR == data_root:
        raise RuntimeError("workspace directory must be separate from the home workspace")
    if identity_db.is_relative_to(config.WORKSPACES_DIR):
        raise RuntimeError("identity database must not be inside a user workspace")
    if identity_db.parent == data_root:
        raise RuntimeError("identity database requires a dedicated private directory")
    identity_db.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    identity_db.parent.chmod(0o700)
    config.WORKSPACES_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.WORKSPACES_DIR.chmod(0o700)
    db.init_identity_db(identity_db)

    owner = _migrate_legacy_owner(config, legacy_db)
    if owner is None:
        if _legacy_workspace_has_private_data(legacy_db):
            raise RuntimeError(
                "private mode requires AUCTIONRADAR_LEGACY_OWNER_USERNAME or AUCTIONRADAR_LEGACY_OWNER_ID "
                "to assign the existing home workspace"
            )
        if config.LEGACY_OWNER_USERNAME or config.LEGACY_OWNER_ID is not None:
            raise RuntimeError(
                "configured legacy workspace owner was not found in the home database"
            )
        config.LEGACY_OWNER_USER_ID = None
    else:
        config.LEGACY_OWNER_USER_ID = int(owner["id"])
        _migrate_owner_shares(legacy_db, config.LEGACY_OWNER_USER_ID)


def _migrate_owner_shares(legacy_db: Path, owner_id: int) -> None:
    """Preserve only the home owner's existing revocable share links."""
    try:
        source = sqlite3.connect(str(legacy_db), timeout=5)
        source.row_factory = sqlite3.Row
        try:
            tables = {
                row[0]
                for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if "shares" not in tables:
                return
            rows = source.execute(
                "SELECT token_hash,created_at,revoked_at,revoked_by FROM shares WHERE created_by=?",
                (owner_id,),
            ).fetchall()
        finally:
            source.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("cannot safely migrate legacy owner shares") from exc

    with db.identity_context() as conn:
        for share in rows:
            existing = conn.execute(
                "SELECT owner_id FROM public_shares WHERE token_hash=?",
                (share["token_hash"],),
            ).fetchone()
            if existing and int(existing["owner_id"]) != owner_id:
                raise RuntimeError("legacy share conflicts with a central share index entry")
            conn.execute(
                "INSERT OR IGNORE INTO public_shares(token_hash,owner_id,created_at,revoked_at,revoked_by) VALUES(?,?,?,?,?)",
                (
                    share["token_hash"],
                    owner_id,
                    share["created_at"],
                    share["revoked_at"],
                    share["revoked_by"],
                ),
            )


def _legacy_workspace_has_private_data(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in ("items", "reviews", "runs", "saved_searches", "settings", "shares"):
                if table in tables and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    return True
            if "users" in tables and conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return True
            return False
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("cannot safely inspect the existing home workspace") from exc


def _migrate_legacy_owner(config, legacy_db: Path):
    configured_id = config.LEGACY_OWNER_ID
    configured_username = config.LEGACY_OWNER_USERNAME
    if configured_id is None and not configured_username:
        return None
    if not legacy_db.exists():
        return None
    try:
        source = sqlite3.connect(str(legacy_db), timeout=5)
        source.row_factory = sqlite3.Row
        try:
            if configured_id is not None:
                row = source.execute(
                    "SELECT id,username,password_hash,password_salt,is_admin,active,created_at,last_password_change,session_version "
                    "FROM users WHERE id=?",
                    (configured_id,),
                ).fetchone()
            else:
                row = source.execute(
                    "SELECT id,username,password_hash,password_salt,is_admin,active,created_at,last_password_change,session_version "
                    "FROM users WHERE username=? COLLATE NOCASE",
                    (configured_username,),
                ).fetchone()
        finally:
            source.close()
    except sqlite3.DatabaseError as exc:
        raise RuntimeError("cannot safely read the configured legacy workspace owner") from exc
    if row is None:
        return None
    if not row["active"]:
        raise RuntimeError("configured legacy workspace owner is inactive")

    with db.identity_context() as conn:
        by_username = conn.execute(
            "SELECT id,username,password_hash FROM users WHERE username=? COLLATE NOCASE",
            (row["username"],),
        ).fetchone()
        by_id = conn.execute("SELECT id,username FROM users WHERE id=?", (row["id"],)).fetchone()
        if by_username:
            if int(by_username["id"]) != int(row["id"]):
                raise RuntimeError(
                    "legacy workspace owner conflicts with an existing central identity"
                )
            return by_username
        if by_id:
            raise RuntimeError(
                "legacy workspace owner ID conflicts with an existing central identity"
            )
        conn.execute(
            "INSERT INTO users(id,username,password_hash,password_salt,is_admin,active,created_at,last_password_change,session_version) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            tuple(
                row[k]
                for k in (
                    "id",
                    "username",
                    "password_hash",
                    "password_salt",
                    "is_admin",
                    "active",
                    "created_at",
                    "last_password_change",
                    "session_version",
                )
            ),
        )
        return conn.execute("SELECT id,username FROM users WHERE id=?", (row["id"],)).fetchone()


def workspace_data_dir(user_id: int, config=None) -> Path:
    """Resolve an identity to its home (legacy owner) or isolated data root."""
    cfg = config or current_app.config["WEBAPP_CONFIG"]
    identifier = int(user_id)
    if identifier < 1:
        raise ValueError("user id must be positive")
    if cfg.LEGACY_OWNER_USER_ID is not None and identifier == int(cfg.LEGACY_OWNER_USER_ID):
        return Path(cfg.DATA_DIR).expanduser().resolve()
    return Path(cfg.WORKSPACES_DIR).expanduser().resolve() / str(identifier)


def _ensure_workspace_files(data_dir: Path) -> None:
    config_dir = data_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    defaults = {
        "source-catalog.json": {"sources": []},
        "search-profile.json": {},
        "selection-feedback.json": {"records": []},
        "seeds.json": {},
    }
    for filename, value in defaults.items():
        target = config_dir / filename
        try:
            descriptor = os.open(
                target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
        except FileExistsError:
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")


def prepare_workspace_user(user: dict, config=None) -> Path:
    """Provision only the user's own empty files and FK shadow row."""
    cfg = config or current_app.config["WEBAPP_CONFIG"]
    target = workspace_data_dir(int(user["id"]), cfg)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if cfg.LEGACY_OWNER_USER_ID is None or int(user["id"]) != int(cfg.LEGACY_OWNER_USER_ID):
        target.parent.chmod(0o700)
        target.mkdir(parents=True, exist_ok=True, mode=0o700)
        target.chmod(0o700)
    _ensure_workspace_files(target)
    with db.data_dir_context(target) as conn:
        # Existing workspace FK constraints reference users locally. Keep a
        # credential-free reference row; password/session truth stays central.
        stamp = user.get("created_at") or "1970-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT OR IGNORE INTO users(id,username,password_hash,password_salt,is_admin,active,created_at,last_password_change,session_version) "
            "VALUES(?,?,?, '',0,1,?,?,1)",
            (int(user["id"]), user["username"], "workspace-reference-only", stamp, stamp),
        )
        conn.execute(
            "UPDATE users SET username=?,active=1 WHERE id=?", (user["username"], int(user["id"]))
        )
    return target


def _token_from_request() -> Optional[str]:
    values = getattr(request, "view_args", None) or {}
    if request.path.startswith(("/share/", "/api/public/")):
        token = values.get("token")
        return token if isinstance(token, str) and 0 < len(token) <= 256 else None
    return None


def _owner_for_share(token: str) -> Optional[int]:
    import hashlib

    with db.identity_context() as conn:
        row = conn.execute(
            "SELECT p.owner_id FROM public_shares p JOIN users u ON u.id=p.owner_id "
            "WHERE p.token_hash=? AND p.revoked_at IS NULL AND u.active=1",
            (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
        ).fetchone()
    return int(row["owner_id"]) if row else None


def init_app(app: Flask) -> None:
    """Install a request-scoped DB route; public links resolve via one hash lookup."""

    @app.before_request
    def _select_request_workspace():
        cfg = current_app.config["WEBAPP_CONFIG"]
        scope = db.data_dir_context(cfg.DATA_DIR)
        scope.__enter__()
        g._auctionradar_workspace_scope = scope
        g._auctionradar_workspace_user_id = None
        if cfg.WORKSPACE_MODE != "private":
            return None

        share_token = _token_from_request()
        user = None
        if share_token is not None:
            user_id = _owner_for_share(share_token)
        else:
            from .security import load_current_user

            user = load_current_user()
            user_id = int(user["id"]) if user else None
        if user_id is None:
            return None

        target = workspace_data_dir(user_id, cfg)
        if target != Path(cfg.DATA_DIR).expanduser().resolve():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.parent.chmod(0o700)
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.chmod(0o700)
            scope.__exit__(None, None, None)
            scope = db.data_dir_context(target)
            scope.__enter__()
            g._auctionradar_workspace_scope = scope
        g._auctionradar_workspace_user_id = user_id
        _ensure_workspace_files(target)
        if user is not None:
            conn = db.get_connection()
            stamp = user.get("created_at") or "1970-01-01T00:00:00+00:00"
            conn.execute(
                "INSERT OR IGNORE INTO users(id,username,password_hash,password_salt,is_admin,active,created_at,last_password_change,session_version) "
                "VALUES(?,?,?, '',0,1,?,?,1)",
                (user_id, user["username"], "workspace-reference-only", stamp, stamp),
            )
            conn.execute(
                "UPDATE users SET username=?,active=1 WHERE id=?", (user["username"], user_id)
            )
        return None

    @app.teardown_request
    def _release_request_workspace(_error=None):
        scope = getattr(g, "_auctionradar_workspace_scope", None)
        if scope is not None:
            scope.__exit__(None, None, None)
            g._auctionradar_workspace_scope = None


def current_config():
    """Return a per-request config copy with all workspace paths routed locally."""
    cfg = current_app.config["WEBAPP_CONFIG"]
    if cfg.WORKSPACE_MODE != "private":
        return cfg
    result = copy(cfg)
    root = db.current_data_dir()
    config_dir = root / "config"
    result.DATA_DIR = root
    result.RUN_LOCK_PATH = root / "run.lock"
    if cfg.LEGACY_OWNER_USER_ID is not None and root == Path(cfg.DATA_DIR).expanduser().resolve():
        # Explicit adoption keeps the owner's full existing setup, not a copy
        # silently stripped of eligibility, feedback or specialised seed data.
        return result
    generation = config_dir / "current"
    source_dir = config_dir
    if generation.is_symlink():
        target = os.readlink(generation)
        if not re.fullmatch(r"generations/[0-9a-f-]{36}", target):
            raise RuntimeError("invalid_workspace_source_generation")
        source_dir = generation.resolve(strict=True)
        if source_dir.parent != (config_dir / "generations").resolve(strict=True):
            raise RuntimeError("invalid_workspace_source_generation")
    result.CATALOG_PATH = source_dir / "source-catalog.json"
    result.PROFILE_PATH = config_dir / "search-profile.json"
    result.FEEDBACK_PATH = config_dir / "selection-feedback.json"
    result.SEEDS_PATH = source_dir / "seeds.json"
    return result


def current_workspace_user_id() -> Optional[int]:
    return getattr(g, "_auctionradar_workspace_user_id", None)
