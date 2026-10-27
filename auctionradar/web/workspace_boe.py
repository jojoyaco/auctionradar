"""Workspace-scoped BOE mTLS reader and private photo export helper.

Callers must pass the authenticated account's own workspace directory. The
identity is decrypted only long enough to create an in-memory TLS context;
authenticated photos and the sanitized manifest stay below that workspace's
``private-media/boe-authenticated`` directory and are not public-share data.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import ssl
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

import auctionradar.connectors.boe.client as boe_authenticated
from auctionradar.connectors.boe.client import ERR_CODES, BoeAccessError, BoeClient

from . import credential_store

MAX_REQUESTS = 40
MAX_REFRESH_ITEMS = 10
MAX_REFRESH_SECONDS = 120
MAX_PHOTOS_PER_ITEM = 4
BOE_REQUEST_TIMEOUT = 8.0
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_PHOTO_BYTES = 12 * 1024 * 1024
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
ITEM_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


class WorkspaceBoeError(Exception):
    """Safe worker error exposing only a stable code."""

    __slots__ = ("code",)

    def __init__(self, code: str = "boe_reader_failed") -> None:
        self.code = (
            code
            if code in set(ERR_CODES) | {"certificate_disabled", "credential_store_unavailable"}
            else "boe_reader_failed"
        )
        super().__init__(self.code)


def _private_workspace(workspace: os.PathLike[str] | str) -> Path:
    candidate = Path(workspace)
    try:
        if candidate.is_symlink() or not candidate.is_dir():
            raise WorkspaceBoeError()
        root = candidate.resolve(strict=True)
        if root.stat().st_uid != os.geteuid():
            raise WorkspaceBoeError()
        return root
    except (OSError, RuntimeError):
        raise WorkspaceBoeError() from None


def _secure_directory(path: Path) -> None:
    try:
        if path.is_symlink():
            raise WorkspaceBoeError()
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise WorkspaceBoeError()
        os.chmod(path, 0o700)
    except (OSError, RuntimeError):
        raise WorkspaceBoeError() from None


def _build_tls_context(identity: credential_store.BoeIdentity) -> ssl.SSLContext:
    """Load a parsed identity through a transient memfd, never PEM disk."""
    pem = bytearray(credential_store.pkcs12_tls_pem(identity.p12, identity.passphrase))
    descriptor = None
    try:
        if not hasattr(os, "memfd_create"):
            raise WorkspaceBoeError()
        descriptor = os.memfd_create("auctionradar-boe-identity", getattr(os, "MFD_CLOEXEC", 0))
        with os.fdopen(os.dup(descriptor), "wb") as stream:
            stream.write(pem)
            stream.flush()
        os.lseek(descriptor, 0, os.SEEK_SET)
        context = ssl.create_default_context()
        context.load_cert_chain(f"/proc/self/fd/{descriptor}")
        return context
    except (OSError, ValueError, ssl.SSLError, WorkspaceBoeError):
        raise WorkspaceBoeError() from None
    finally:
        pem[:] = b"\x00" * len(pem)
        if descriptor is not None:
            os.close(descriptor)


def create_boe_client(
    workspace: os.PathLike[str] | str,
    *,
    deadline: Optional[float] = None,
    timeout: float = BOE_REQUEST_TIMEOUT,
) -> BoeClient:
    """Return an mTLS client only when this workspace explicitly enabled it."""
    root = _private_workspace(workspace)
    identity = None
    try:
        identity = credential_store.load_identity(root, require_enabled=True)
        context = _build_tls_context(identity)
        client = BoeClient(context, timeout=min(BOE_REQUEST_TIMEOUT, max(0.1, float(timeout))))
        if deadline is not None:

            def bounded_transport(url, authenticated, max_bytes, referer):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("BOE refresh budget exhausted")
                return boe_authenticated._default_transport(
                    url,
                    authenticated,
                    max_bytes,
                    referer,
                    jar=client._jar,
                    auth_ctx=client._auth_ctx,
                    plain_ctx=client._plain_ctx,
                    timeout=min(client._timeout, remaining),
                )

            client._transport = bounded_transport
        return client
    except credential_store.CredentialDisabled:
        raise WorkspaceBoeError("certificate_disabled") from None
    except credential_store.CredentialStoreError as error:
        raise WorkspaceBoeError(error.code) from None
    except WorkspaceBoeError:
        raise
    except Exception:
        raise WorkspaceBoeError() from None
    finally:
        if identity is not None:
            identity.p12 = b""
            identity.passphrase = b""


def _atomic_private_write(path: Path, data: bytes) -> None:
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12))
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read_manifest(path: Path) -> dict:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    except FileNotFoundError:
        return {"version": 1, "items": {}}
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not 0 < info.st_size <= MAX_MANIFEST_BYTES
        ):
            raise WorkspaceBoeError()
        value = json.loads(os.read(descriptor, MAX_MANIFEST_BYTES + 1))
        if not isinstance(value, dict) or not isinstance(value.get("items"), dict):
            return {"version": 1, "items": {}}
        return value
    except (OSError, ValueError, TypeError):
        return {"version": 1, "items": {}}
    finally:
        os.close(descriptor)


def collect_workspace_photos(
    workspace: os.PathLike[str] | str,
    requests: list[dict],
    *,
    client_factory: Optional[Callable[[Path], Any]] = None,
    max_items: int = MAX_REQUESTS,
    budget_seconds: int = MAX_REFRESH_SECONDS,
    max_photos_per_item: int = MAX_PHOTOS_PER_ITEM,
) -> dict:
    """Read requested BOE assets using this account's cert and private export.

    ``client_factory`` is a test seam; production callers should omit it. The
    input must be assembled from authenticated private item data, not a public
    share request. Returned data contains counts and stable BOE error codes
    only; it never includes file paths, certificate bytes, or passphrases.
    """
    root = _private_workspace(workspace)
    if not isinstance(requests, list) or len(requests) > 200:
        raise WorkspaceBoeError("invalid_argument")
    max_items = min(MAX_REQUESTS, max(0, int(max_items)))
    budget_seconds = min(MAX_REFRESH_SECONDS, max(1, int(budget_seconds)))
    max_photos_per_item = min(50, max(1, int(max_photos_per_item)))
    _secure_directory(root / "private-media")
    export_root = root / "private-media" / "boe-authenticated"
    media_dir = export_root / "media"
    _secure_directory(export_root)
    _secure_directory(media_dir)
    manifest_path = export_root / "manifest.json"
    manifest = _read_manifest(manifest_path)
    manifest["version"] = 1
    results = {
        "ok": True,
        "requested": 0,
        "ready": 0,
        "photos": 0,
        "errors": 0,
        "error_codes": {},
        "partial": False,
    }
    if len(requests) > max_items:
        results["partial"] = True

    started = time.monotonic()
    deadline = started + budget_seconds
    try:
        client = (
            client_factory(root) if client_factory else create_boe_client(root, deadline=deadline)
        )
        client.login()
    except (BoeAccessError, WorkspaceBoeError) as error:
        code = (
            error.code
            if error.code in ERR_CODES or error.code == "certificate_disabled"
            else "boe_reader_failed"
        )
        results["ok"] = False
        results["errors"] = 1
        results["partial"] = True
        results["error_codes"][code] = 1
        return results
    except Exception:
        results["ok"] = False
        results["errors"] = 1
        results["partial"] = True
        results["error_codes"]["boe_reader_failed"] = 1
        return results

    if time.monotonic() >= deadline:
        results["partial"] = True
        results["errors"] = 1
        results["error_codes"]["budget_exhausted"] = 1
    for request_data in requests[:max_items]:
        if time.monotonic() >= deadline:
            results["partial"] = True
            break
        if (
            not isinstance(request_data, dict)
            or not isinstance(request_data.get("item_id"), str)
            or not ITEM_ID_RE.fullmatch(request_data["item_id"])
            or not isinstance(request_data.get("url"), str)
        ):
            results["errors"] += 1
            results["partial"] = True
            results["error_codes"]["invalid_argument"] = (
                results["error_codes"].get("invalid_argument", 0) + 1
            )
            continue
        canonical_url = boe_authenticated.canonical_asset_page_url(request_data["url"])
        if not canonical_url:
            results["errors"] += 1
            results["partial"] = True
            results["error_codes"]["invalid_argument"] = (
                results["error_codes"].get("invalid_argument", 0) + 1
            )
            continue

        item_id = request_data["item_id"]
        results["requested"] += 1
        photos = []
        try:
            for photo in client.iter_asset_photos(canonical_url, max_photos=max_photos_per_item):
                data = photo.get("data")
                mime = photo.get("content_type")
                if (
                    mime not in EXTENSIONS
                    or not isinstance(data, bytes)
                    or not 0 < len(data) <= MAX_PHOTO_BYTES
                ):
                    raise ValueError("invalid_photo")
                digest = hashlib.sha256(data).hexdigest()
                media_id = hashlib.sha256((item_id + ":" + digest).encode()).hexdigest()[:32]
                filename = media_id + "." + EXTENSIONS[mime]
                _atomic_private_write(media_dir / filename, data)
                photos.append(
                    {
                        "media_id": media_id,
                        "file": "media/" + filename,
                        "content_type": mime,
                        "size": len(data),
                        "sha256": digest,
                        "caption": photo.get("caption", "")[:500]
                        if isinstance(photo.get("caption"), str)
                        else None,
                        "source_url": canonical_url,
                    }
                )
            manifest["items"][item_id] = {
                "status": "ready",
                "photos": photos,
                "observed_at": datetime.now(timezone.utc).isoformat(),
            }
            results["ready"] += 1
            results["photos"] += len(photos)
        except (BoeAccessError, OSError, ValueError, TypeError, KeyError) as error:
            results["errors"] += 1
            results["partial"] = True
            code = (
                error.code
                if isinstance(error, BoeAccessError) and error.code in ERR_CODES
                else "read_failed"
            )
            results["error_codes"][code] = results["error_codes"].get(code, 0) + 1
            if item_id not in manifest["items"]:
                manifest["items"][item_id] = {"status": "error", "photos": []}
            manifest["items"][item_id]["last_error"] = code

    manifest["generated_at"] = datetime.now(timezone.utc).isoformat()
    encoded = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise WorkspaceBoeError("invalid_argument")
    _atomic_private_write(manifest_path, encoded)
    return results


def refresh_if_configured(workspace: os.PathLike[str] | str) -> dict:
    """Refresh BOE photos only when this private workspace owns the active DB."""
    summary = {
        "ok": True,
        "configured": False,
        "enabled": False,
        "requested": 0,
        "ready": 0,
        "photos": 0,
        "errors": 0,
        "updated": 0,
        "partial": False,
    }
    request_file = None
    try:
        from . import boe_sync, db

        if db.workspace_mode() != "private":
            return {**summary, "skipped": "not_private_workspace"}
        root = _private_workspace(workspace)
        active_root = Path(db.current_data_dir()).resolve(strict=True)
        if active_root != root:
            return {**summary, "skipped": "workspace_mismatch"}

        identity = credential_store.identity_status(root)
        summary.update(
            configured=bool(identity.get("configured")), enabled=bool(identity.get("enabled"))
        )
        if not summary["configured"] or not summary["enabled"]:
            return {**summary, "skipped": "certificate_not_enabled"}

        _secure_directory(root / "private-media")
        export_root = root / "private-media" / "boe-authenticated"
        _secure_directory(export_root)
        request_file = export_root / (".refresh-" + secrets.token_hex(12) + ".json")
        boe_sync.prepare_requests(request_file, max_items=MAX_REFRESH_ITEMS)
        prepared = json.loads(request_file.read_text(encoding="utf-8"))
        requests = prepared.get("requests") if isinstance(prepared, dict) else None
        if not isinstance(requests, list) or len(requests) > MAX_REFRESH_ITEMS:
            raise WorkspaceBoeError("invalid_argument")
        if not requests:
            return {**summary, "skipped": "no_boe_items"}

        deadline = time.monotonic() + MAX_REFRESH_SECONDS
        collected = collect_workspace_photos(
            root,
            requests,
            client_factory=lambda own_root: create_boe_client(own_root, deadline=deadline),
            max_items=MAX_REFRESH_ITEMS,
            budget_seconds=MAX_REFRESH_SECONDS,
            max_photos_per_item=MAX_PHOTOS_PER_ITEM,
        )
        summary.update(
            ok=bool(collected.get("ok")),
            requested=int(collected.get("requested", 0)),
            ready=int(collected.get("ready", 0)),
            photos=int(collected.get("photos", 0)),
            errors=int(collected.get("errors", 0)),
            partial=bool(collected.get("partial")),
        )
        if summary["ready"]:
            imported = boe_sync.import_export(export_root)
            summary["updated"] = (
                int(imported.get("updated", 0)) if isinstance(imported, dict) else 0
            )
            if not isinstance(imported, dict) or not imported.get("ok", True):
                summary.update(ok=False, partial=True)
        return summary
    except Exception:
        # This is an optional post-ingest enhancement. Keep the public run
        # successful and return only fixed, non-sensitive status metadata.
        return {**summary, "ok": False, "partial": True, "error": "refresh_failed"}
    finally:
        if request_file is not None:
            try:
                request_file.unlink(missing_ok=True)
                request_file.with_suffix(".new").unlink(missing_ok=True)
            except OSError:
                pass
