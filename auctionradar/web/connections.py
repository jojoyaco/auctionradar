"""Private, per-workspace source management and hardened public transport.

The blueprint is intentionally not registered here; the workspace/core
integration registers it only for private-mode installations. Every request
resolves its workspace through ``db.current_data_dir()`` and never falls back
to the repository's canonical catalogue.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import stat
import uuid
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_wtf.csrf import validate_csrf

from auctionradar.assets import ASSET_TYPES as SHARED_ASSET_TYPES

from . import db, security
from .credential_store import (
    CredentialDisabled,
    CredentialNotFound,
    CredentialStoreError,
    CredentialStoreUnavailable,
    InvalidCredential,
)

bp = Blueprint("connections", __name__)

SOURCE_ID_RE = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
ASSET_TYPES = frozenset(SHARED_ASSET_TYPES) - {"unknown"}
MAX_SOURCES = 100
MAX_URL_LENGTH = 2048
MAX_SOURCE_BODY = 8 * 1024 * 1024
SENSITIVE_QUERY_KEY = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|auth(?:orization)?|credential|cookie|session|api.?key|signature|jwt)",
    re.I,
)
SENSITIVE_BODY_KEY = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|auth(?:orization)?|credential|cookie|session|api.?key|signature|jwt)",
    re.I,
)
PRIVATE_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan", ".test")


class SourceValidationError(ValueError):
    def __init__(self, code: str = "invalid_source") -> None:
        self.code = code
        super().__init__(code)


class WorkspaceUnavailable(RuntimeError):
    pass


def _private_mode() -> bool:
    config = current_app.config.get("WEBAPP_CONFIG")
    return getattr(config, "WORKSPACE_MODE", None) == "private"


def _workspace_root() -> Path:
    if not _private_mode():
        raise WorkspaceUnavailable()
    resolver = getattr(db, "current_data_dir", None)
    if not callable(resolver):
        raise WorkspaceUnavailable()
    try:
        candidate = Path(resolver())
        if candidate.is_symlink() or not candidate.is_dir():
            raise WorkspaceUnavailable()
        root = candidate.resolve(strict=True)
        if root.stat().st_uid != os.geteuid():
            raise WorkspaceUnavailable()
        return root
    except (OSError, RuntimeError, TypeError, ValueError):
        raise WorkspaceUnavailable() from None


def workspace_source_paths(workspace: os.PathLike[str] | str) -> dict[str, Path]:
    """Stable core-config paths; ``current`` switches catalog+seeds together."""
    candidate = Path(workspace)
    if candidate.is_symlink() or not candidate.is_dir():
        raise WorkspaceUnavailable()
    root = candidate.resolve(strict=True)
    current = root / "config" / "current"
    return {
        "CATALOG_PATH": current / "source-catalog.json",
        "SEEDS_PATH": current / "seeds.json",
    }


def _safe_directory(path: Path, *, create: bool) -> None:
    try:
        if path.is_symlink():
            raise WorkspaceUnavailable()
        if create:
            path.mkdir(mode=0o700, exist_ok=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise WorkspaceUnavailable()
        os.chmod(path, 0o700)
    except (OSError, RuntimeError):
        raise WorkspaceUnavailable() from None


def _config_dir(root: Path, *, create: bool) -> Path:
    directory = root / "config"
    _safe_directory(directory, create=create)
    return directory


def _atomic_file(path: Path, content: bytes) -> None:
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12))
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_json(path: Path, value: Any) -> None:
    _atomic_file(path, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


@contextmanager
def _source_write_lock(root: Path):
    config = _config_dir(root, create=True)
    lock_path = config / ".connections.lock"
    descriptor = None
    try:
        descriptor = os.open(
            lock_path,
            os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise WorkspaceUnavailable()
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except (OSError, RuntimeError):
        if descriptor is not None:
            os.close(descriptor)
        raise WorkspaceUnavailable() from None
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_json(path: Path, *, limit: int = 1024 * 1024) -> dict:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not 0 < info.st_size <= limit
        ):
            raise WorkspaceUnavailable()
        raw = os.read(descriptor, limit + 1)
        if len(raw) > limit:
            raise WorkspaceUnavailable()
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise WorkspaceUnavailable()
        return value
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise WorkspaceUnavailable() from None
    finally:
        os.close(descriptor)


def _generation_directory(root: Path, *, create: bool) -> Path:
    config = _config_dir(root, create=create)
    generations = config / "generations"
    _safe_directory(generations, create=create)
    return generations


def _active_generation(root: Path) -> Optional[Path]:
    config = _config_dir(root, create=True)
    current = config / "current"
    try:
        if not current.is_symlink():
            if current.exists():
                raise WorkspaceUnavailable()
            return None
        target = os.readlink(current)
        if not re.fullmatch(r"generations/[0-9a-f-]{36}", target):
            raise WorkspaceUnavailable()
        generation = (config / target).resolve(strict=True)
        generation.relative_to((config / "generations").resolve(strict=True))
        if generation.stat().st_uid != os.geteuid() or generation.is_symlink():
            raise WorkspaceUnavailable()
        return generation
    except WorkspaceUnavailable:
        raise
    except (OSError, RuntimeError, ValueError):
        raise WorkspaceUnavailable() from None


def _write_source_state(root: Path, sources: list[dict], seeds: dict[str, list[str]]) -> str:
    config = _config_dir(root, create=True)
    generations = _generation_directory(root, create=True)
    revision = str(uuid.uuid4())
    generation = generations / revision
    try:
        generation.mkdir(mode=0o700)
        catalog = {
            "schema_version": "1",
            "revision": revision,
            "description": "Private workspace source catalogue; user-managed, empty by default.",
            "capability_policy": {
                "automatic_integration": False,
                "adapter_status": "user_configured",
                "api_availability": "unknown",
                "notes": "Only explicitly selected public HTML adapters run. No login, bidding, or automatic account actions.",
            },
            "sources": sources,
        }
        seeds_document = {"_revision": revision, **seeds}
        _atomic_json(generation / "source-catalog.json", catalog)
        _atomic_json(generation / "seeds.json", seeds_document)
        generation_fd = os.open(generation, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(generation_fd)
        finally:
            os.close(generation_fd)
        temporary_link = config / (".current." + revision)
        os.symlink(f"generations/{revision}", temporary_link)
        os.replace(temporary_link, config / "current")
        directory_fd = os.open(config, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, RuntimeError):
        raise WorkspaceUnavailable() from None
    return revision


def _known_adapter_ids() -> set[str]:
    try:
        import auctionradar.connectors.catalog as adapters

        return {"generic_html", "boe", "aeat", *getattr(adapters, "DETAIL_PATTERNS", {}).keys()}
    except Exception:
        return {"generic_html"}


def _read_source_state(
    root: Path, *, initialize: bool = True
) -> tuple[list[dict], dict[str, list[str]], str]:
    generation = _active_generation(root)
    if generation is None:
        if not initialize:
            return [], {}, ""
        _write_source_state(root, [], {})
        generation = _active_generation(root)
    if generation is None:
        raise WorkspaceUnavailable()
    catalog = _read_json(generation / "source-catalog.json")
    seeds_document = _read_json(generation / "seeds.json")
    revision = catalog.get("revision")
    if not isinstance(revision, str) or revision != seeds_document.get("_revision"):
        raise WorkspaceUnavailable()
    sources = catalog.get("sources")
    if not isinstance(sources, list) or len(sources) > MAX_SOURCES:
        raise WorkspaceUnavailable()
    normalized = []
    seen = set()
    for raw in sources:
        source = _normalize_source(raw, resolve=False)
        if source["id"] in seen:
            raise WorkspaceUnavailable()
        seen.add(source["id"])
        normalized.append(source)
    seeds = {}
    for source in normalized:
        entries = seeds_document.get(source["id"], [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise WorkspaceUnavailable()
        if any(not isinstance(value, str) or len(value) > MAX_URL_LENGTH for value in entries):
            raise WorkspaceUnavailable()
        seeds[source["id"]] = entries
    return normalized, seeds, revision


def _read_seeded_source_id(source_id: str) -> bool:
    return isinstance(source_id, str) and SOURCE_ID_RE.fullmatch(source_id) is not None


def _explicit_workspace_root(workspace: os.PathLike[str] | str) -> Path:
    candidate = Path(workspace)
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise WorkspaceUnavailable()
        root = candidate.resolve(strict=True)
        if root.stat().st_uid != os.geteuid():
            raise WorkspaceUnavailable()
        return root
    except (OSError, RuntimeError, TypeError, ValueError):
        raise WorkspaceUnavailable() from None


def _source_fields() -> set[str]:
    return {"id", "name", "url", "asset_types", "enabled", "adapter_id"}


def _normalize_source(value: Any, *, resolve: bool) -> dict:
    if not isinstance(value, dict) or set(value) - _source_fields():
        raise SourceValidationError()
    source_id = value.get("id")
    name = value.get("name")
    url = value.get("url")
    asset_types = value.get("asset_types")
    enabled = value.get("enabled", False)
    adapter_id = value.get("adapter_id", "generic_html")

    if (
        not isinstance(source_id, str)
        or len(source_id) > 64
        or not SOURCE_ID_RE.fullmatch(source_id)
    ):
        raise SourceValidationError("invalid_source_id")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name.strip()) > 120
        or any(ord(c) < 32 for c in name)
    ):
        raise SourceValidationError("invalid_source_name")
    if not isinstance(url, str):
        raise SourceValidationError("invalid_source_url")
    normalized_url = validate_source_url(url) if resolve else _canonical_source_url(url)
    if not isinstance(asset_types, list) or not 1 <= len(asset_types) <= len(ASSET_TYPES):
        raise SourceValidationError("invalid_asset_types")
    if any(not isinstance(kind, str) or kind not in ASSET_TYPES for kind in asset_types):
        raise SourceValidationError("invalid_asset_types")
    if len(set(asset_types)) != len(asset_types):
        raise SourceValidationError("invalid_asset_types")
    if not isinstance(enabled, bool):
        raise SourceValidationError("invalid_enabled")
    if not isinstance(adapter_id, str) or adapter_id not in _known_adapter_ids():
        raise SourceValidationError("unsupported_adapter")

    return {
        "id": source_id,
        "name": name.strip(),
        "url": normalized_url,
        "asset_types": list(asset_types),
        "enabled": enabled,
        "adapter_id": adapter_id,
    }


def _canonical_source_url(value: str) -> str:
    """Lexical URL validation used on disk reads; network checks happen at fetch."""
    if not isinstance(value, str) or not value or len(value) > MAX_URL_LENGTH:
        raise SourceValidationError("invalid_source_url")
    try:
        from auctionradar.network import PublicNetworkError, validate_url

        parts = urlsplit(value)
        if parts.fragment or _has_secret_query(parts.query):
            raise SourceValidationError("invalid_source_url")
        canonical = validate_url(value)
        host = urlsplit(canonical).hostname
    except SourceValidationError:
        raise
    except (PublicNetworkError, ValueError, UnicodeError):
        raise SourceValidationError("invalid_source_url") from None
    if (
        not host
        or len(host) > 253
        or host.endswith(PRIVATE_HOST_SUFFIXES)
        or (":" not in host and ("." not in host or not re.fullmatch(r"[a-z0-9.-]+", host)))
    ):
        raise SourceValidationError("invalid_source_url")
    return canonical


def _has_secret_query(query: str) -> bool:
    try:
        pairs = parse_qsl(query, keep_blank_values=True, max_num_fields=80)
    except ValueError:
        return True
    return any(SENSITIVE_QUERY_KEY.search(key) for key, _ in pairs)


def validate_source_url(value: str) -> str:
    """Validate URL and every DNS answer with the shared bounded policy."""
    canonical = _canonical_source_url(value)
    from auctionradar.network import PublicNetworkError, resolve_public

    try:
        resolved, _host, _addresses = resolve_public(canonical, timeout=5.0)
    except PublicNetworkError as error:
        if str(error) in {"public_dns_unavailable", "public_dns_busy", "public_dns_timeout"}:
            raise SourceValidationError("source_dns_unavailable") from None
        raise SourceValidationError("invalid_source_url") from None
    return _canonical_source_url(resolved)


def _contains_secret_key(value: Any, depth: int = 0) -> bool:
    if depth > 8:
        return True
    if isinstance(value, dict):
        return any(
            not isinstance(key, str)
            or SENSITIVE_BODY_KEY.search(key)
            or _contains_secret_key(item, depth + 1)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret_key(item, depth + 1) for item in value)
    return False


def safe_source_fetch(url: str, context: Optional[dict] = None) -> dict:
    """Use the shared bounded transport and adapter evidence/redaction path."""
    import time

    started = time.monotonic()
    context = context if isinstance(context, dict) else {}
    result = {
        "url": None,
        "final_url": None,
        "http_status": None,
        "content_type": None,
        "body": "",
        "body_bytes": b"",
        "error": None,
        "raw_path": None,
        "sha256": None,
        "max_body_bytes": MAX_SOURCE_BODY,
        "truncated": False,
    }
    try:
        canonical = _canonical_source_url(url)
        result["url"] = canonical
        payload = context.get("request_json")
        if payload is not None and (not isinstance(payload, dict) or _contains_secret_key(payload)):
            raise SourceValidationError("request_rejected")
        timeout_value = context.get("timeout", 12)
        try:
            timeout = min(30.0, max(0.2, float(timeout_value)))
        except (TypeError, ValueError):
            timeout = 12.0
        adapter_context = dict(context)
        adapter_context.update(public_network_only=True, timeout=timeout)
        import auctionradar.connectors.catalog as adapters

        result.update(adapters.fetch(canonical, adapter_context))
    except SourceValidationError as error:
        result["error"] = error.code
    except (OSError, ValueError, TypeError):
        result["error"] = "request_failed"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def _load_source_state_for_request() -> tuple[Path, list[dict], dict[str, list[str]], str]:
    root = _workspace_root()
    sources, seeds, revision = _read_source_state(root)
    return root, sources, seeds, revision


def _json_request() -> Optional[dict]:
    if not request.is_json:
        return None
    try:
        payload = request.get_json(silent=False)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            token = request.headers.get("X-CSRFToken") or request.form.get("csrf_token")
            if not token:
                return jsonify({"error": "csrf_missing"}), 400
            try:
                validate_csrf(token)
            except Exception:
                return jsonify({"error": "csrf_invalid"}), 400
        return view(*args, **kwargs)

    return wrapped


def _error_response(error: Exception):
    if isinstance(error, SourceValidationError):
        return jsonify({"error": error.code}), 400
    if isinstance(error, WorkspaceUnavailable):
        return jsonify({"error": "workspace_unavailable"}), 503
    if isinstance(error, CredentialNotFound):
        return jsonify({"error": error.code}), 404
    if isinstance(error, CredentialDisabled):
        return jsonify({"error": error.code}), 409
    if isinstance(error, InvalidCredential):
        return jsonify({"error": "invalid_certificate"}), 400
    if isinstance(error, CredentialStoreUnavailable):
        return jsonify({"error": "credential_store_unavailable"}), 503
    if isinstance(error, CredentialStoreError):
        return jsonify({"error": "credential_store_unavailable"}), 503
    return jsonify({"error": "workspace_unavailable"}), 503


def _certificate_transport_error():
    """Require transport and cookie protections for production cert actions."""
    config = current_app.config.get("WEBAPP_CONFIG")
    environment = str(getattr(config, "ENVIRONMENT", "development")).lower()
    if environment not in {"production", "prod"}:
        return None
    if not request.is_secure:
        return jsonify({"error": "https_required"}), 400
    if current_app.config.get("SESSION_COOKIE_SECURE") is not True:
        return jsonify({"error": "secure_cookie_required"}), 503
    return None


@bp.get("/connections")
@security.login_required
def connections_page():
    if not _private_mode():
        abort(404)
    user = security.load_current_user() or {}
    try:
        _load_source_state_for_request()
    except WorkspaceUnavailable:
        return _error_response(WorkspaceUnavailable())
    return render_template("connections.html", user=user)


@bp.get("/api/connections/sources")
@security.login_required
def api_sources_get():
    try:
        _root, sources, _seeds, revision = _load_source_state_for_request()
        return jsonify({"sources": sources, "revision": revision})
    except Exception as error:
        return _error_response(error)


@bp.post("/api/connections/sources")
@security.login_required
@_csrf_required
def api_sources_create():
    payload = _json_request()
    if payload is None:
        return jsonify({"error": "json_required"}), 400
    try:
        root = _workspace_root()
        source = _normalize_source(payload, resolve=True)
        with _source_write_lock(root):
            sources, seeds, _revision = _read_source_state(root)
            if len(sources) >= MAX_SOURCES:
                return jsonify({"error": "source_limit_reached"}), 400
            if any(existing["id"] == source["id"] for existing in sources):
                return jsonify({"error": "source_exists"}), 409
            sources.append(source)
            seeds[source["id"]] = []
            revision = _write_source_state(root, sources, seeds)
        return jsonify({"source": source, "revision": revision}), 201
    except Exception as error:
        return _error_response(error)


@bp.patch("/api/connections/sources/<source_id>")
@security.login_required
@_csrf_required
def api_sources_update(source_id: str):
    payload = _json_request()
    if payload is None or not _read_seeded_source_id(source_id) or not payload:
        return jsonify({"error": "invalid_source"}), 400
    if set(payload) - (_source_fields() - {"id"}):
        return jsonify({"error": "invalid_source"}), 400
    try:
        root = _workspace_root()
        with _source_write_lock(root):
            sources, seeds, _revision = _read_source_state(root)
            index = next((i for i, source in enumerate(sources) if source["id"] == source_id), None)
            if index is None:
                return jsonify({"error": "source_not_found"}), 404
            updated = _normalize_source(
                {**sources[index], **payload, "id": source_id}, resolve="url" in payload
            )
            sources[index] = updated
            revision = _write_source_state(root, sources, seeds)
        return jsonify({"source": updated, "revision": revision})
    except Exception as error:
        return _error_response(error)


@bp.delete("/api/connections/sources/<source_id>")
@security.login_required
@_csrf_required
def api_sources_delete(source_id: str):
    if not _read_seeded_source_id(source_id):
        return jsonify({"error": "invalid_source"}), 400
    try:
        root = _workspace_root()
        with _source_write_lock(root):
            sources, seeds, _revision = _read_source_state(root)
            remaining = [source for source in sources if source["id"] != source_id]
            if len(remaining) == len(sources):
                return jsonify({"error": "source_not_found"}), 404
            seeds.pop(source_id, None)
            revision = _write_source_state(root, remaining, seeds)
        return jsonify({"deleted": source_id, "revision": revision})
    except Exception as error:
        return _error_response(error)


@bp.get("/api/connections/certificate")
@security.login_required
def api_certificate_status():
    from . import credential_store

    transport_error = _certificate_transport_error()
    if transport_error is not None:
        return transport_error
    try:
        return jsonify(credential_store.identity_status(_workspace_root()))
    except Exception as error:
        return _error_response(error)


@bp.post("/api/connections/certificate")
@security.login_required
@_csrf_required
def api_certificate_upload():
    from . import credential_store

    transport_error = _certificate_transport_error()
    if transport_error is not None:
        return transport_error
    uploaded = request.files.get("certificate")
    consent = request.form.get("consent")
    passphrase = request.form.get("passphrase")
    if uploaded is None or consent != "yes" or not isinstance(passphrase, str):
        return jsonify({"error": "certificate_and_consent_required"}), 400
    try:
        passphrase_bytes = passphrase.encode("utf-8")
        p12 = uploaded.stream.read(credential_store.MAX_P12_BYTES + 1)
        if len(p12) > credential_store.MAX_P12_BYTES:
            raise InvalidCredential()
        status = credential_store.save_identity(
            _workspace_root(), p12, passphrase_bytes, enabled=True
        )
        return jsonify(status), 201
    except Exception as error:
        return _error_response(error)


@bp.patch("/api/connections/certificate")
@security.login_required
@_csrf_required
def api_certificate_toggle():
    from . import credential_store

    transport_error = _certificate_transport_error()
    if transport_error is not None:
        return transport_error
    payload = _json_request()
    if not isinstance(payload, dict) or set(payload) != {"enabled", "consent"}:
        return jsonify({"error": "invalid_request"}), 400
    if not isinstance(payload["enabled"], bool) or payload["consent"] is not payload["enabled"]:
        return jsonify({"error": "explicit_consent_required"}), 400
    try:
        return jsonify(credential_store.set_enabled(_workspace_root(), payload["enabled"]))
    except Exception as error:
        return _error_response(error)


@bp.delete("/api/connections/certificate")
@security.login_required
@_csrf_required
def api_certificate_delete():
    from . import credential_store

    transport_error = _certificate_transport_error()
    if transport_error is not None:
        return transport_error
    try:
        credential_store.delete_identity(_workspace_root())
        return jsonify({"deleted": True})
    except Exception as error:
        return _error_response(error)


def build_adapter_source(source: dict) -> dict:
    """Translate a private entry into the legacy generic-adapter contract."""
    runtime = dict(source)
    adapter_id = runtime.pop("adapter_id", "generic_html")
    runtime["id"] = source["id"] if adapter_id == "generic_html" else adapter_id
    runtime["private_source_id"] = source["id"]
    return runtime


def _restore_private_identity(result: dict, source_id: str, runtime_id: str) -> dict:
    for item in result.get("items", []) if isinstance(result, dict) else []:
        if not isinstance(item, dict):
            continue
        original_id = item.get("id")
        if isinstance(original_id, str) and original_id.startswith(runtime_id + "-"):
            item["id"] = source_id + original_id[len(runtime_id) :]
        item["source_id"] = source_id
        source_ids = item.get("source_ids")
        if isinstance(source_ids, list):
            item["source_ids"] = [
                source_id if item_id == runtime_id else item_id for item_id in source_ids
            ]
        else:
            item["source_ids"] = [source_id]
    return result


def discover_private_source(
    workspace: os.PathLike[str] | str,
    source_id: str,
    context: Optional[dict] = None,
) -> dict:
    """Concrete worker hook: run the existing adapter using pinned public I/O.

    The caller must obtain ``workspace`` from the authenticated account's
    workspace resolver; this helper never chooses or falls back to a global
    data directory. It intentionally provides public discovery only.
    """
    root = _explicit_workspace_root(workspace)
    sources, seeds, _revision = _read_source_state(root)
    source = next((entry for entry in sources if entry["id"] == source_id), None)
    if source is None:
        raise SourceValidationError("source_not_found")
    if not source["enabled"]:
        raise SourceValidationError("source_disabled")
    import auctionradar.connectors.catalog as adapters

    runtime_source = build_adapter_source(source)
    runtime_id = runtime_source["id"]
    adapter_context = dict(context or {})
    adapter_context["fetcher"] = safe_source_fetch
    adapter_context["seeds"] = {runtime_id: list(seeds.get(source_id, []))}
    result = adapters.discover(runtime_source, adapter_context)
    return _restore_private_identity(result, source_id, runtime_id)
