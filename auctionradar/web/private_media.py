"""BOE-authenticated private-media serving subsystem.

The workspace reader authenticates against ``subastas.boe.es`` using the
account's optional encrypted certificate. An isolated external reader can also
provide a sanitized export. Neither path stores credentials in source control.
Exports contain only the following media artifacts:

* ``manifest.json`` — UTF-8 JSON describing each ``web item_id`` and the
  photos already retrieved through the authorised BOE session.
* ``media/<uuid32hex>.<jpg|jpeg|png|webp|gif>`` — flat media cache with
  restricted permissions; ownership depends on the configured reader.

This module only consumes the cache. It does not fetch BOE URLs, hold
certificate material, or accept arbitrary paths. Every file served is
selected from the manifest by UUID, validated against the database record
and streamed with a closed descriptor.

Endpoints
---------

* ``GET /api/items/<item_id>/media/<media_id>`` — authenticated users.
* ``GET /api/public/<token>/media/<media_id>`` — anonymous viewers with a
  non-revoked share token (the item is resolved *only* from the share).

Both endpoints reject cross-item access, traversal, MIME-mismatched files,
oversized blobs and any payload that is not referenced by the manifest and
the stored ``data_json.photos`` list. Response headers are
``Cache-Control: private, no-store`` and ``X-Content-Type-Options: nosniff``
so revocation propagates immediately.

Configuration
-------------

* ``app.config['BOE_MEDIA_ROOT']`` — explicit path (test override).
* ``os.environ['AUCTIONRADAR_BOE_EXPORT_DIR']`` — runtime override.
* Fallback: ``/var/lib/auctionradar-boe-export``.

The application factory registers :data:`bp` once
during ``create_app`` and to wire ``init_security`` *before* the blueprint
is mounted so the global ``X-Content-Type-Options`` header still wins.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import stat
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    send_file,
)

from . import db, models, security, urls

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants and configuration
# ---------------------------------------------------------------------------


MAX_FILE_BYTES = 12 * 1024 * 1024  # 12 MiB cap on a single photo
MAX_MANIFEST_BYTES = 1 * 1024 * 1024  # 1 MiB cap on manifest.json
MANIFEST_VERSION = 1

DEFAULT_EXPORT_DIR = Path("/var/lib/auctionradar-boe-export")
EXPORT_DIR_ENV = "AUCTIONRADAR_BOE_EXPORT_DIR"

MEDIA_ID_RE = re.compile(r"[0-9a-f]{32}\Z")
ITEM_ID_RE = re.compile(r"[a-zA-Z0-9_-]{1,80}\Z")

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
EXT_TO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "gif": "image/gif",
}

# Magic-byte signatures checked against the first 16 bytes of every file.
# The values are stored as plain ``bytes`` constants so the comparison is a
# constant-time ``startswith``; we deliberately do not log file contents.
_MAGIC_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),  # followed by 4 bytes length + "WEBP" at offset 8
)

_WEBP_TRAILER = b"WEBP"


# ---------------------------------------------------------------------------
# Path and manifest helpers
# ---------------------------------------------------------------------------


def resolve_export_root(app: Any) -> Path:
    """Return the trusted export directory used by ``boe-reader``.

    Resolution order (first match wins):

    1. ``app.config['BOE_MEDIA_ROOT']`` — explicit override (used by tests).
    2. ``$AUCTIONRADAR_BOE_EXPORT_DIR`` — runtime override.
    3. :data:`DEFAULT_EXPORT_DIR` — fixed platform path.

    The path is never read from a request. Callers receive a fresh
    :class:`pathlib.Path` and are expected to resolve it once per request.
    """
    from . import db

    if db.workspace_mode() == "private":
        return db.current_data_dir() / "private-media" / "boe-authenticated"
    cfg_value = app.config.get("BOE_MEDIA_ROOT") if app is not None else None
    if cfg_value:
        return Path(cfg_value)
    env_value = os.environ.get(EXPORT_DIR_ENV)
    if env_value:
        return Path(env_value)
    return DEFAULT_EXPORT_DIR


def _safe_int(value: Any, *, minimum: int = 0, maximum: int = MAX_FILE_BYTES) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        if not minimum <= value <= maximum:
            return None
        return value
    if isinstance(value, str):
        try:
            number = int(value)
        except ValueError:
            return None
        if not minimum <= number <= maximum:
            return None
        return number
    return None


def _looks_like_media_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    media_id = entry.get("media_id")
    if not isinstance(media_id, str) or not MEDIA_ID_RE.match(media_id):
        return False
    file_path = entry.get("file")
    if not isinstance(file_path, str):
        return False
    if not file_path.startswith("media/"):
        return False
    if ".." in file_path.replace("\\", "/").split("/"):
        return False
    if file_path.startswith("/"):
        return False
    extension = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if extension not in ALLOWED_EXTENSIONS:
        return False
    return True


def load_manifest(root: Path) -> Optional[dict]:
    """Parse and validate ``manifest.json`` from the export root.

    Returns ``None`` (fail-closed) when the file is missing, oversized,
    malformed or carries an unexpected version. Callers must treat
    ``None`` exactly like "no media available".
    """
    manifest_path = root / "manifest.json"
    try:
        # ``O_NOFOLLOW`` blocks a symlink swap between ``stat`` and ``open``;
        # a symlinked manifest is treated as missing rather than trusted.
        fd = os.open(manifest_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    try:
        try:
            info = os.fstat(fd)
            size = info.st_size
            if not stat.S_ISREG(info.st_mode):
                return None
        except OSError:
            return None
        if size <= 0 or size > MAX_MANIFEST_BYTES:
            return None
        try:
            data = os.read(fd, size)
        except OSError:
            return None
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != MANIFEST_VERSION:
        return None
    if not isinstance(payload.get("generated_at"), str):
        return None
    items = payload.get("items")
    if not isinstance(items, dict):
        return None
    cleaned_items: dict = {}
    for item_id, entry in items.items():
        if not isinstance(item_id, str) or not ITEM_ID_RE.match(item_id):
            continue
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status not in {"ready", "login_required", "error"}:
            continue
        raw_photos = entry.get("photos")
        photos: list = []
        if isinstance(raw_photos, list):
            for raw in raw_photos:
                if not _looks_like_media_entry(raw):
                    continue
                media_id = raw["media_id"]
                file_value = raw["file"]
                # Only plain filenames under the media/ subdirectory.
                relative = file_value[len("media/") :]
                # Reject anything that is not exactly ``<uuid>.<ext>``.
                if "/" in relative or "\\" in relative:
                    continue
                stem, dot, ext = relative.rpartition(".")
                if not stem or not dot or not ext:
                    continue
                if not MEDIA_ID_RE.match(stem):
                    continue
                if ext.lower() not in ALLOWED_EXTENSIONS:
                    continue
                if stem != media_id:
                    continue
                expected_size = _safe_int(raw.get("size"))
                expected_sha = raw.get("sha256")
                if (
                    expected_size is None
                    or expected_size == 0
                    or not isinstance(expected_sha, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_sha)
                ):
                    continue
                if raw.get("content_type") != EXT_TO_MIME[ext.lower()]:
                    continue
                photos.append(
                    {
                        "media_id": media_id,
                        "file": file_value,
                        "extension": ext.lower(),
                        "size": expected_size,
                        "sha256": expected_sha,
                        "content_type": EXT_TO_MIME[ext.lower()],
                        "caption": raw.get("caption")
                        if isinstance(raw.get("caption"), str)
                        else None,
                        "source_url": public_source_url(raw.get("source_url")),
                    }
                )
        cleaned_items[item_id] = {
            "status": status,
            "photos": photos,
        }
    return {"version": MANIFEST_VERSION, "items": cleaned_items}


def _detect_magic(handle: Any) -> Optional[str]:
    try:
        head = handle.read(16)
    except OSError:
        return None
    try:
        if hasattr(handle, "seek"):
            handle.seek(0)
    except OSError:
        pass
    if not head:
        return None
    for prefix, mime in _MAGIC_SIGNATURES:
        if head.startswith(prefix):
            # WEBP needs an extra trailer check at offset 8.
            if mime == "image/webp":
                if len(head) >= 12 and head[8:12] == _WEBP_TRAILER:
                    return mime
                continue
            return mime
    return None


def _hash_handle(handle: Any, size: int) -> Optional[str]:
    """Return ``sha256`` of ``handle`` or ``None`` on read error."""
    try:
        handle.seek(0)
    except OSError:
        return None
    digest = hashlib.sha256()
    remaining = size
    chunk = 256 * 1024
    while remaining > 0:
        try:
            data = handle.read(min(chunk, remaining))
        except OSError:
            return None
        if not data:
            break
        digest.update(data)
        remaining -= len(data)
    try:
        handle.seek(0)
    except OSError:
        pass
    return digest.hexdigest()


def open_media_file(root: Path, media_id: str, extension: str) -> Optional[Tuple[Any, int]]:
    """Open a flat media file with strict ``O_NOFOLLOW`` semantics.

    Returns ``(file_obj, size)`` on success and ``None`` for any failure
    mode (missing file, symlink, traversal attempt, wrong parent, oversized
    blob). The returned object is a buffered binary file wrapping an
    ``O_NOFOLLOW`` descriptor — :func:`send_file` closes the file object
    (and therefore the underlying FD) when the response is finalised.
    """
    if not MEDIA_ID_RE.match(media_id):
        return None
    if extension.lower() not in ALLOWED_EXTENSIONS:
        return None

    if root.is_symlink():
        return None
    target = root / "media" / f"{media_id}.{extension.lower()}"
    directory_fd = None
    try:
        # Open the directory without following links, then open the relative filename;
        # this avoids resolve() hiding a link before O_NOFOLLOW can check it.
        directory_fd = os.open(root / "media", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fd = os.open(target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None
    finally:
        if directory_fd is not None:
            os.close(directory_fd)

    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None

    # Reject directories, devices, sockets, anything that is not a regular
    # file. ``S_ISREG`` semantics via ``stat.S_ISREG``.
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None

    # The resolved path of the open descriptor must still match the
    # requested canonical path; this is the second line of defence against
    # a TOCTOU swap on the underlying inode.
    try:
        real_path = os.readlink(f"/proc/self/fd/{fd}") if os.path.exists("/proc/self/fd") else None
    except OSError:
        real_path = None
    if real_path:
        try:
            real_resolved = Path(real_path).resolve()
            if real_resolved != target.resolve():
                os.close(fd)
                return None
        except OSError:
            os.close(fd)
            return None

    if st.st_size <= 0 or st.st_size > MAX_FILE_BYTES:
        os.close(fd)
        return None

    try:
        handle = os.fdopen(fd, "rb")
    except OSError:
        os.close(fd)
        return None
    return handle, st.st_size


# ---------------------------------------------------------------------------
# URL helpers used by parent views
# ---------------------------------------------------------------------------


def media_url_for_item(item_id: str, media_id: str) -> str:
    """Internal relative URL for authenticated browsers."""
    return f"/api/items/{item_id}/media/{media_id}"


def media_url_for_share(token: str, media_id: str) -> str:
    """Internal relative URL for anonymous viewers holding a valid share."""
    return f"/api/public/{quote(token, safe='')}/media/{media_id}"


def public_source_url(value):
    """Do not expose private document URLs or authentication tokens."""
    if not urls.is_safe_url(value):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "subastas.boe.es"
        or parsed.path not in ("/reg/detalleSubasta.php", "/detalleSubasta.php")
    ):
        return None
    values = parse_qs(parsed.query)
    identifier = (values.get("idSub") or [""])[0]
    lot = (values.get("idLote") or [""])[0]
    if not re.fullmatch(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]+", identifier):
        return None
    if lot and not re.fullmatch(r"\d{1,5}", lot):
        return None
    query = {"idSub": identifier, "ver": "3"}
    if lot:
        query["idLote"] = lot
    return "https://subastas.boe.es/detalleSubasta.php?" + urlencode(query)


def media_photo_payload(photo: Any, item_id: str, *, token: Optional[str] = None) -> Optional[dict]:
    """Translate a stored photo entry into a public-safe API payload.

    Returns ``None`` when the entry is malformed or ``media_id`` is not a
    32-char lowercase UUID — the caller is expected to drop such photos
    from listings, matching the behaviour of :func:`auctionradar.web.views._safe_photo`.

    The helper performs **no** filesystem access and **no** logging; it
    only inspects the structured photo object. The resulting payload
    always exposes the relative internal URL and never the raw authenticated
    BOE target.
    """
    if not isinstance(photo, dict):
        return None
    media_id = photo.get("media_id")
    if not isinstance(media_id, str) or not MEDIA_ID_RE.match(media_id):
        return None
    if not isinstance(item_id, str) or not ITEM_ID_RE.match(item_id):
        return None
    url = media_url_for_share(token, media_id) if token else media_url_for_item(item_id, media_id)
    return {
        "url": url,
        "media_id": media_id,
        "caption": photo.get("caption") if isinstance(photo.get("caption"), str) else None,
        "source_url": public_source_url(photo.get("source_url")),
        "image_type": "published",
        "origin": "boe_authenticated_cache",
    }


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------


bp = Blueprint("private_media", __name__)


def _not_found():
    return jsonify({"error": "not_found"}), 404


def _bad_request(reason: str):
    return jsonify({"error": reason}), 400


def _photo_has_media_id(data_json: Any, media_id: str) -> bool:
    if not isinstance(data_json, dict):
        return False
    photos = data_json.get("photos")
    if not isinstance(photos, list):
        return False
    for entry in photos:
        if not isinstance(entry, dict):
            continue
        candidate = entry.get("media_id")
        if isinstance(candidate, str) and candidate == media_id:
            return True
    return False


def _manifest_entry_for(manifest: Optional[dict], item_id: str, media_id: str) -> Optional[dict]:
    if not manifest:
        return None
    items = manifest.get("items") or {}
    bucket = items.get(item_id)
    if not isinstance(bucket, dict):
        return None
    if bucket.get("status") != "ready":
        return None
    for entry in bucket.get("photos") or []:
        if isinstance(entry, dict) and entry.get("media_id") == media_id:
            return entry
    return None


def _serve(handle: Any, mime: str, download_name: str) -> Response:
    response = send_file(
        handle,
        mimetype=mime,
        as_attachment=False,
        download_name=download_name,
        conditional=False,
        etag=False,
        last_modified=None,
        max_age=0,
    )
    # Defensive headers in addition to the global ones configured by
    # ``init_security`` (``X-Content-Type-Options: nosniff``,
    # ``Referrer-Policy: no-referrer`` for private-link routes).
    # ``Cache-Control`` is set here as documentation; the global
    # after_request handler in :mod:`auctionradar.web.security` enforces the
    # strict ``no-store`` value on every non-static response so
    # revocation propagates immediately. ``no-store`` is a *stronger*
    # directive than ``private, no-store`` because it forbids storage
    # in any cache (shared or private), so the resulting header still
    # satisfies the spec.
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    # Belt-and-braces FD hygiene: Werkzeug's ``FileWrapper.close()`` is
    # wired to ``response.close`` only for streamed responses consumed by
    # the WSGI server. The test client materialises the body eagerly via
    # ``response.data`` and may not propagate ``close``; ``call_on_close``
    # guarantees the underlying file descriptor is released promptly so
    # thousands of photo requests do not exhaust the process limit.
    close_callback = getattr(handle, "close", None)
    if callable(close_callback):
        response.call_on_close(close_callback)
    return response


def _resolve_and_serve(item_id: str, media_id: str) -> Any:
    """Run the full validation pipeline and return either a Response or an
    error tuple ``(json, status)``.

    The pipeline is shared between the authenticated and the public
    endpoints; the only thing that varies is how the item is obtained
    (either from the URL parameter or from a share token).
    """
    if not ITEM_ID_RE.match(item_id or ""):
        return _bad_request("invalid_item_id")
    if not MEDIA_ID_RE.match(media_id or ""):
        return _bad_request("invalid_media_id")
    row = models.get_item(item_id)
    if row is None:
        return _not_found()
    try:
        data = json.loads(row["data_json"]) if row["data_json"] else {}
    except (ValueError, TypeError):
        data = {}
    if not _photo_has_media_id(data, media_id):
        return _not_found()
    root = resolve_export_root(current_app)
    manifest = load_manifest(root)
    entry = _manifest_entry_for(manifest, item_id, media_id)
    if entry is None:
        return _not_found()
    opened = open_media_file(root, media_id, entry["extension"])
    if opened is None:
        return _not_found()
    handle, size = opened
    # ``_serve`` registers ``call_on_close`` for the success branch so
    # the file descriptor is released when the response closes. We use
    # an explicit ``finally`` block to guarantee the handle closes on
    # every error branch — a single leaked FD across thousands of
    # photo requests is enough to exhaust the worker's ulimit.
    error_response: Any = None
    try:
        magic = _detect_magic(handle)
        if magic != entry["content_type"]:
            error_response = _not_found()
        elif isinstance(entry.get("size"), int) and entry["size"] != size:
            error_response = _not_found()
        elif entry.get("sha256"):
            digest = _hash_handle(handle, size)
            if not digest or not hmac.compare_digest(digest, entry["sha256"]):
                error_response = _not_found()
    except Exception:  # pragma: no cover
        handle.close()
        return _not_found()
    finally:
        if error_response is not None:
            try:
                handle.close()
            except OSError:
                pass
    if error_response is not None:
        return error_response
    try:
        return _serve(handle, entry["content_type"], f"{media_id}.{entry['extension']}")
    except Exception:
        handle.close()
        raise


@bp.get("/api/items/<item_id>/media/<media_id>")
@security.login_required
def serve_item_media(item_id: str, media_id: str):
    return _resolve_and_serve(item_id, media_id)


@bp.get("/api/public/<token>/media/<media_id>")
def serve_public_media(token: str, media_id: str):
    # Private workspaces expose listing data through explicit readonly links,
    # but never anonymously expose the authenticated BOE media cache.
    if db.workspace_mode() == "private":
        return _not_found()
    if not token or len(token) > 256:
        return _not_found()
    if not MEDIA_ID_RE.match(media_id or ""):
        return _bad_request("invalid_media_id")
    share = models.get_share_by_token(token)
    if share is None or share["revoked_at"]:
        return _not_found()
    item_id = share["item_id"]
    if not isinstance(item_id, str) or not ITEM_ID_RE.match(item_id):
        return _not_found()
    return _resolve_and_serve(item_id, media_id)


__all__ = [
    "bp",
    "MAX_FILE_BYTES",
    "MAX_MANIFEST_BYTES",
    "MEDIA_ID_RE",
    "ITEM_ID_RE",
    "ALLOWED_EXTENSIONS",
    "resolve_export_root",
    "load_manifest",
    "open_media_file",
    "media_url_for_item",
    "media_url_for_share",
    "media_photo_payload",
]
