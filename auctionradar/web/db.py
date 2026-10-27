"""SQLite schema and request-scoped storage helpers.

The application stores its database under ``$AUCTIONRADAR_DATA_DIR`` (default:
``instance/`` in the working directory). Private mode routes account data to
individual workspaces and keeps account identity in a separate database.

Design choices:

* One connection per process via :func:`get_connection`. SQLite is set
  in WAL mode so gunicorn workers can read concurrently; writes are
  serialised by the engine itself.
* All writes use parameterised SQL. There are no string concatenations
  or ``%`` interpolations.
* Schema is created in :func:`init_db`; the migration is idempotent so
  it can run at every process start.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from threading import RLock
from typing import Iterator, Optional, Union

PathLike = Union[str, Path]

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_password_change TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE,
    ip TEXT NOT NULL,
    success INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time
    ON login_attempts(username, occurred_at);

CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    asset_type TEXT NOT NULL,
    category TEXT,
    price_amount REAL,
    price_type TEXT,
    photo_count INTEGER NOT NULL DEFAULT 0,
    source_id TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    data_json TEXT NOT NULL,
    listing_url TEXT NOT NULL,
    posession_changed_since_review INTEGER NOT NULL DEFAULT 0,
    price_changed_since_review INTEGER NOT NULL DEFAULT 0,
    availability_changed_since_review INTEGER NOT NULL DEFAULT 0,
    title_changed_since_review INTEGER NOT NULL DEFAULT 0,
    right_sold_changed_since_review INTEGER NOT NULL DEFAULT 0,
    exact_price_changed_since_review INTEGER NOT NULL DEFAULT 0,
    changed_since_review INTEGER NOT NULL DEFAULT 0,
    latest_review_id INTEGER,
    raw_fingerprint TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_asset_status ON items(asset_type);
CREATE INDEX IF NOT EXISTS idx_items_first_seen ON items(first_seen);
CREATE INDEX IF NOT EXISTS idx_items_changed ON items(changed_since_review);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('pending','ok','ko')),
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    raw_snapshot_json TEXT NOT NULL,
    acknowledged_fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_item ON reviews(item_id);

CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_shares_item ON shares(item_id);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('queued','running','success','partial','error','cancelled')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    pid INTEGER,
    summary_json TEXT,
    error TEXT,
    created_by INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor_id INTEGER,
    actor_username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

CREATE TABLE IF NOT EXISTS item_aliases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    alias_kind TEXT NOT NULL,
    alias_value TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_item_aliases_item ON item_aliases(item_id);
CREATE INDEX IF NOT EXISTS idx_item_aliases_lookup
    ON item_aliases(alias_kind, alias_value);

CREATE INDEX IF NOT EXISTS idx_runs_status_started
    ON runs(status, started_at);

CREATE TABLE IF NOT EXISTS saved_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    criteria_json TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    ai_enabled INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    notifications_enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_checked_at TEXT,
    ai_status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    eval_pid INTEGER,
    evaluation_counts_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_searches_owner ON saved_searches(owner_id,active);
CREATE TABLE IF NOT EXISTS search_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    search_id INTEGER NOT NULL REFERENCES saved_searches(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    first_matched TEXT NOT NULL,
    last_matched TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    evaluation_fingerprint TEXT,
    assessment_json TEXT,
    verdict TEXT NOT NULL DEFAULT 'needs_review',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(search_id,item_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_search ON search_matches(search_id,active,verdict);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL REFERENCES users(id),
    search_id INTEGER NOT NULL REFERENCES saved_searches(id),
    item_id TEXT NOT NULL REFERENCES items(id),
    kind TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    read_at TEXT,
    UNIQUE(owner_id,search_id,item_id,kind,fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_notifications_owner ON notifications(owner_id,read_at);
CREATE INDEX IF NOT EXISTS idx_notifications_owner_search ON notifications(owner_id,search_id,read_at);
"""


def _db_path(data_dir: PathLike) -> Path:
    path = Path(data_dir) / "app.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


_connection: Optional[sqlite3.Connection] = None
_connection_data_dir: Optional[Path] = None
_default_data_dir: Optional[Path] = None
_default_workspace_mode = "legacy"
_default_identity_path: Optional[Path] = None
_active_data_dir: ContextVar[Optional[Path]] = ContextVar("webapp_data_dir", default=None)
_active_connection: ContextVar[Optional[sqlite3.Connection]] = ContextVar(
    "webapp_connection", default=None
)
_active_identity: ContextVar[bool] = ContextVar("webapp_identity_connection", default=False)
_connection_lock = RLock()
_initialized_data_dirs: set[Path] = set()
_initialized_identity_paths: set[Path] = set()

_IDENTITY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_password_change TEXT NOT NULL,
    session_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(active);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL COLLATE NOCASE,
    ip TEXT NOT NULL,
    success INTEGER NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_username_time
    ON login_attempts(username, occurred_at);
CREATE INDEX IF NOT EXISTS idx_login_attempts_ip_time
    ON login_attempts(ip, occurred_at);
CREATE TABLE IF NOT EXISTS registration_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ip TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_registration_attempts_ip_time
    ON registration_attempts(ip, occurred_at);
CREATE TABLE IF NOT EXISTS public_shares (
    token_hash TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT,
    revoked_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_public_shares_owner ON public_shares(owner_id, revoked_at);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor_id INTEGER,
    actor_username TEXT,
    action TEXT NOT NULL,
    target TEXT,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_identity_audit_action ON audit_log(action);
"""


_MIGRATION_SQL = [
    (
        "saved_searches",
        "ALTER TABLE saved_searches ADD COLUMN evaluation_counts_json TEXT NOT NULL DEFAULT '{}'",
        "evaluation_counts_json",
    ),
    (
        "saved_searches",
        "ALTER TABLE saved_searches ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
        "archived",
    ),
    ("items", "ALTER TABLE items ADD COLUMN category TEXT", "category"),
    ("items", "ALTER TABLE items ADD COLUMN price_amount REAL", "price_amount"),
    ("items", "ALTER TABLE items ADD COLUMN price_type TEXT", "price_type"),
    ("items", "ALTER TABLE items ADD COLUMN photo_count INTEGER NOT NULL DEFAULT 0", "photo_count"),
    # Add columns to an items table created by an older schema. Each
    # statement is checked via ``PRAGMA table_info`` first so it stays
    # idempotent. ``ALTER TABLE`` does not support ``IF NOT EXISTS``
    # in SQLite prior to 3.35.
    (
        "items",
        "ALTER TABLE items ADD COLUMN title_changed_since_review INTEGER NOT NULL DEFAULT 0",
        "title_changed_since_review",
    ),
    (
        "items",
        "ALTER TABLE items ADD COLUMN right_sold_changed_since_review INTEGER NOT NULL DEFAULT 0",
        "right_sold_changed_since_review",
    ),
    (
        "items",
        "ALTER TABLE items ADD COLUMN exact_price_changed_since_review INTEGER NOT NULL DEFAULT 0",
        "exact_price_changed_since_review",
    ),
]


def _apply_migrations(conn: sqlite3.Connection) -> None:
    """Run additive migrations on ``conn``.

    The webapp intentionally avoids hard version numbers: a fresh
    install already gets every column via :data:`_SCHEMA_SQL`. This
    helper upgrades older DBs that predate the latest additions.
    Failures are swallowed so a malformed older DB doesn't break the
    start-up sequence — failing closed via the runner would be
    worse than an unrecoverable schema.
    """
    for table, stmt, column in _MIGRATION_SQL:
        try:
            existing = conn.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.DatabaseError:
            return
        names = {row[1] for row in existing}
        if column in names:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # Adding the column is additive — if it fails, leave the
            # rest alone and let the operator investigate.
            pass


def _path_for_connection(conn: Optional[sqlite3.Connection]) -> Optional[Path]:
    if conn is None:
        return None
    try:
        row = _execute_raw(conn, "PRAGMA database_list").fetchone()
    except sqlite3.ProgrammingError:
        return None
    if not row or not row[2]:
        return None
    return Path(row[2])


def _data_dir_for_connection() -> Optional[Path]:
    """Return the directory backing the compatibility/default connection."""
    return _connection_data_dir


def _execute_raw(conn: sqlite3.Connection, sql: str):
    """Compatibility shim — some old Python builds split API."""
    return conn.execute(sql)


def _register_functions(conn: sqlite3.Connection) -> None:
    from .catalog import fold_text

    conn.create_function("radar_fold", 1, fold_text, deterministic=True)


def _open_connection(db_path: Path, *, initialize: bool = True) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        db_path.chmod(0o600)
    except OSError:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    if initialize:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_SCHEMA_SQL)
        _apply_migrations(conn)
        import json

        from .catalog import derived_fields

        for row in conn.execute(
            "SELECT id,asset_type,data_json FROM items WHERE category IS NULL"
        ).fetchall():
            try:
                derived = derived_fields(json.loads(row["data_json"]), row["asset_type"])
            except (ValueError, TypeError):
                derived = ("unknown", None, None, 0)
            conn.execute(
                "UPDATE items SET category=?,price_amount=?,price_type=?,photo_count=? WHERE id=?",
                (*derived, row["id"]),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_items_category_price ON items(category,price_amount)"
        )
    _register_functions(conn)
    return conn


def _open_identity_connection(
    identity_path: Path, *, initialize: bool = True
) -> sqlite3.Connection:
    identity_path = identity_path.expanduser().resolve()
    identity_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    conn = sqlite3.connect(
        str(identity_path),
        isolation_level=None,
        timeout=30.0,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        identity_path.chmod(0o600)
    except OSError:
        pass
    conn.execute("PRAGMA foreign_keys = ON")
    if initialize:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(_IDENTITY_SCHEMA_SQL)
    return conn


def configure_runtime(workspace_mode: str, identity_path: Optional[PathLike] = None) -> None:
    """Set non-request defaults; each request still selects its own DB context."""
    global _default_workspace_mode, _default_identity_path
    if workspace_mode not in {"legacy", "private"}:
        raise RuntimeError("AUCTIONRADAR_WORKSPACE_MODE must be 'legacy' or 'private'")
    resolved = Path(identity_path).expanduser().resolve() if identity_path else None
    if workspace_mode == "private" and resolved is None:
        raise RuntimeError("private workspace mode requires an identity database path")
    _default_workspace_mode = workspace_mode
    _default_identity_path = resolved


def workspace_mode() -> str:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            cfg = current_app.config.get("WEBAPP_CONFIG")
            if cfg is not None:
                return cfg.WORKSPACE_MODE
    except (ImportError, RuntimeError):
        pass
    return _default_workspace_mode


def _identity_path() -> Path:
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            cfg = current_app.config.get("WEBAPP_CONFIG")
            if cfg is not None and cfg.IDENTITY_DB_PATH:
                return Path(cfg.IDENTITY_DB_PATH).expanduser().resolve()
    except (ImportError, RuntimeError):
        pass
    if _default_identity_path is None:
        raise RuntimeError("private workspace mode has no identity database path")
    return _default_identity_path


def init_identity_db(identity_path: PathLike) -> Path:
    """Initialize the central authentication and opaque share-index database."""
    global _default_identity_path
    path = Path(identity_path).expanduser().resolve()
    with _connection_lock:
        if path not in _initialized_identity_paths:
            conn = _open_identity_connection(path)
            conn.close()
            _initialized_identity_paths.add(path)
    _default_identity_path = path
    return path


def _ensure_data_dir_initialized(data_dir: PathLike) -> Path:
    path = Path(data_dir).expanduser().resolve()
    with _connection_lock:
        if path not in _initialized_data_dirs:
            conn = _open_connection(_db_path(path))
            conn.close()
            _initialized_data_dirs.add(path)
    return path


def init_db(data_dir: PathLike) -> Path:
    """Initialize a workspace and make it the compatibility default.

    Request handlers use :func:`data_dir_context` instead of changing this
    process default, so simultaneous users never redirect each other's SQL.
    """
    global _connection, _connection_data_dir, _default_data_dir
    path = Path(data_dir).expanduser().resolve()
    if _active_data_dir.get() is not None:
        _ensure_data_dir_initialized(path)
        return _db_path(path)
    with _connection_lock:
        if _connection is not None and _connection_data_dir != path:
            try:
                _connection.close()
            except sqlite3.ProgrammingError:
                pass
            _connection = None
            _connection_data_dir = None
        if path not in _initialized_data_dirs:
            conn = _open_connection(_db_path(path))
            _initialized_data_dirs.add(path)
            _connection = conn
            _connection_data_dir = path
        elif _connection is None:
            _connection = _open_connection(_db_path(path), initialize=False)
            _connection_data_dir = path
        _default_data_dir = path
        return _db_path(path)


@contextmanager
def data_dir_context(data_dir: PathLike) -> Iterator[sqlite3.Connection]:
    """Select one workspace connection for this request/context only."""
    path = _ensure_data_dir_initialized(data_dir)
    conn = _open_connection(_db_path(path), initialize=False)
    dir_token = _active_data_dir.set(path)
    conn_token = _active_connection.set(conn)
    identity_token = _active_identity.set(False)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass
        _active_identity.reset(identity_token)
        _active_connection.reset(conn_token)
        _active_data_dir.reset(dir_token)


@contextmanager
def identity_context() -> Iterator[sqlite3.Connection]:
    """Route identity/auth operations to the central database in private mode.

    Legacy mode intentionally keeps the historical single-database behavior.
    The context is nested-safe and its selection is ContextVar-local, not a
    mutable process-wide connection swap.
    """
    if workspace_mode() != "private":
        yield get_connection()
        return
    if _active_identity.get():
        yield get_connection()
        return
    path = _identity_path()
    with _connection_lock:
        initialize = path not in _initialized_identity_paths
        conn = _open_identity_connection(path, initialize=initialize)
        if initialize:
            _initialized_identity_paths.add(path)
    token = _active_connection.set(conn)
    identity_token = _active_identity.set(True)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass
        _active_identity.reset(identity_token)
        _active_connection.reset(token)


def get_connection() -> sqlite3.Connection:
    """Return the context-selected connection or the legacy default."""
    conn = _active_connection.get()
    if conn is not None:
        return conn
    data_dir = _active_data_dir.get()
    if data_dir is not None:
        conn = _open_connection(_db_path(_ensure_data_dir_initialized(data_dir)), initialize=False)
        _active_connection.set(conn)
        return conn
    if _connection is None:
        raise RuntimeError(
            "Database has not been initialised. Call auctionradar.web.db.init_db() first."
        )
    return _connection


def current_data_dir() -> Path:
    """Return the active workspace root, never a process-global routing hint."""
    selected = _active_data_dir.get()
    if selected is not None:
        return selected
    conn = _active_connection.get() or _connection
    actual = _path_for_connection(conn)
    if actual is None:
        if _default_data_dir is None:
            raise RuntimeError("Database has not been initialised")
        return _default_data_dir
    return actual.parent


def close_connection() -> None:
    """Close default and current-context connections (test/CLI helper)."""
    global _connection, _connection_data_dir
    conn = _active_connection.get()
    if conn is not None:
        try:
            conn.close()
        except sqlite3.ProgrammingError:
            pass
        _active_connection.set(None)
    if _connection is not None:
        try:
            _connection.close()
        except sqlite3.ProgrammingError:
            pass
    _connection = None
    _connection_data_dir = None


def reset_for_tests(data_dir: PathLike) -> Path:
    """Wipe one test workspace and recreate its schema."""
    global _connection, _connection_data_dir, _default_data_dir
    path = Path(data_dir).expanduser().resolve()
    if _connection_data_dir == path:
        close_connection()
    db_path = _db_path(path)
    if db_path.exists():
        db_path.unlink()
    db_path.with_suffix(".sqlite3-wal").unlink(missing_ok=True)
    db_path.with_suffix(".sqlite3-shm").unlink(missing_ok=True)
    _initialized_data_dirs.discard(path)
    _default_data_dir = path
    return init_db(path)


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Context manager for read/write blocks.

    Using ``sqlite3`` with the default isolation level set in
    :func:`init_db` means a manual ``BEGIN`` is rarely needed; we still
    expose the helper for atomic multi-statement sequences.
    """
    conn = get_connection()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def fetchone(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    return get_connection().execute(query, params).fetchone()


def fetchall(query: str, params: tuple = ()) -> list[sqlite3.Row]:
    return get_connection().execute(query, params).fetchall()


def execute(query: str, params: tuple = ()) -> sqlite3.Cursor:
    return get_connection().execute(query, params)
