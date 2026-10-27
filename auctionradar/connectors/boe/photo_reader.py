"""Standalone BOE reader: systemd credentials to photos and a secret-free manifest.

This module does not import Flask or open SQLite. Shared output contains only
BOE photos and public listing URLs, never cookies or authenticated documents."""

import argparse
import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path

from auctionradar.connectors.boe.client import (
    ERR_CODES,
    BoeAccessError,
    BoeClient,
    load_client_context,
)

MAX_MANIFEST = 1024 * 1024
EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            raise ValueError("invalid_input")
        content = os.read(descriptor, limit + 1)
        if len(content) > limit:
            raise ValueError("invalid_input")
        return json.loads(content)
    finally:
        os.close(descriptor)


def atomic_write(path, data):
    temporary = path.with_name("." + path.name + "." + os.urandom(8).hex())
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        # A restrictive service umask must not remove the reader/web group's
        # intended access to the sanitised export (never credential material).
        os.fchmod(descriptor, 0o640)
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def collect(client, requests_path, export_root, *, max_items=40, budget_seconds=1400):
    """Publish atomically; transient failures preserve the last confirmed gallery."""
    payload = read_json(Path(requests_path), 256 * 1024)
    if (
        not isinstance(payload, dict)
        or payload.get("version") != 1
        or not isinstance(payload.get("requests"), list)
    ):
        raise ValueError("invalid_input")
    root = Path(export_root)
    if root.is_symlink() or not root.is_dir():
        raise ValueError("invalid_export")
    media = root / "media"
    if media.is_symlink():
        raise ValueError("invalid_export")
    media.mkdir(mode=0o2750, exist_ok=True)
    try:
        manifest = read_json(root / "manifest.json", MAX_MANIFEST)
    except (OSError, ValueError):
        manifest = {}
    # This directory is exclusive to the reader: existing records come only
    # from a previous run of this same process, not from web requests.
    if (
        not isinstance(manifest, dict)
        or manifest.get("version") != 1
        or not isinstance(manifest.get("items"), dict)
    ):
        manifest = {"version": 1, "items": {}}
    results = {
        "ok": True,
        "requested": 0,
        "ready": 0,
        "photos": 0,
        "errors": 0,
        "error_codes": {},
        "partial": False,
    }
    started = time.monotonic()
    client.login()
    for request in payload["requests"][:max_items]:
        if time.monotonic() - started >= budget_seconds:
            results["partial"] = True
            break
        if (
            not isinstance(request, dict)
            or not isinstance(request.get("item_id"), str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", request["item_id"])
            or not isinstance(request.get("url"), str)
        ):
            results["errors"] += 1
            continue
        item_id = request["item_id"]
        results["requested"] += 1
        photos = []
        try:
            for photograph in client.iter_asset_photos(request["url"], max_photos=50):
                data = photograph["data"]
                mime = photograph["content_type"]
                if (
                    mime not in EXTENSIONS
                    or not isinstance(data, bytes)
                    or not 0 < len(data) <= 12 * 1024 * 1024
                ):
                    raise ValueError("invalid_image")
                digest = hashlib.sha256(data).hexdigest()
                media_id = hashlib.sha256((item_id + ":" + digest).encode()).hexdigest()[:32]
                name = media_id + "." + EXTENSIONS[mime]
                atomic_write(media / name, data)
                if any(photo["media_id"] == media_id for photo in photos):
                    continue
                caption = photograph.get("caption")
                photos.append(
                    {
                        "media_id": media_id,
                        "file": "media/" + name,
                        "content_type": mime,
                        "size": len(data),
                        "sha256": digest,
                        "caption": caption[:500] if isinstance(caption, str) else None,
                        "source_url": photograph["source_url"],
                    }
                )
                time.sleep(0.15)
            if photos or not manifest["items"].get(item_id, {}).get("photos"):
                manifest["items"][item_id] = {
                    "status": "ready",
                    "photos": photos,
                    "observed_at": timestamp(),
                }
            results["ready"] += 1
            results["photos"] += len(photos)
        except (BoeAccessError, OSError, ValueError, KeyError) as error:
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
        # Do not write HTML, private URLs, or exception details to files or logs.
        time.sleep(0.3)
    if len(payload["requests"]) > max_items:
        results["partial"] = True
    manifest["generated_at"] = timestamp()
    encoded = json.dumps(manifest, ensure_ascii=False).encode()
    if len(encoded) > MAX_MANIFEST:
        raise ValueError("manifest_too_large")
    atomic_write(root / "manifest.json", encoded)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="/var/lib/auctionradar-boe-input/requests.json")
    parser.add_argument("--export-root", default="/var/lib/auctionradar-boe-export")
    args = parser.parse_args(argv)
    try:
        context = load_client_context(Path(os.environ["CREDENTIALS_DIRECTORY"]))
        result = collect(BoeClient(context), args.input, args.export_root)
        print(json.dumps(result), flush=True)
        return 0
    except Exception:
        # Incluye errores SSL/URLs con query privada: deliberadamente opacos.
        print(json.dumps({"ok": False, "error": "boe_reader_failed"}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
