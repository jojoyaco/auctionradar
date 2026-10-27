"""Secret-free exchange between SQLite and an isolated BOE reader.

Preparation writes identifiers and public URLs only. Import accepts images and
a sanitized manifest. This module never opens the personal certificate."""

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from . import catalog, db, models, private_media


def now():
    return datetime.now(timezone.utc).isoformat()


def public_asset_url(value):
    try:
        parsed = urlsplit(value or "")
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "subastas.boe.es"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in ("/detalleSubasta.php", "/reg/detalleSubasta.php")
    ):
        return None
    values = parse_qs(parsed.query, keep_blank_values=True)
    if len(values.get("idSub", [])) != 1 or len(values.get("idLote", [""])) != 1:
        return None
    identifier = values["idSub"][0]
    lot = (values.get("idLote") or [""])[0]
    if not re.fullmatch(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]{1,40}", identifier):
        return None
    if lot and not re.fullmatch(r"\d{1,5}", lot):
        return None
    query = {"idSub": identifier, "ver": "3"}
    if lot:
        query["idLote"] = lot
    return "https://subastas.boe.es/detalleSubasta.php?" + urlencode(query)


def prepare_requests(path, max_items=40):
    requests = []
    for row in db.fetchall(
        "SELECT id,listing_url FROM items ORDER BY CASE WHEN photo_count=0 THEN 0 ELSE 1 END,last_seen DESC,id"
    ):
        url = public_asset_url(row["listing_url"])
        if url:
            requests.append({"item_id": row["id"], "url": url})
        if len(requests) >= max_items:
            break
    payload = {"version": 1, "generated_at": now(), "requests": requests}
    path = Path(path)
    temporary = path.with_suffix(".new")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.chmod(0o640)
    temporary.replace(path)
    return {"requested": len(requests)}


def import_export(root):
    root = Path(root)
    manifest = private_media.load_manifest(root)
    if manifest is None:
        return {"ok": False, "error": "export_unavailable", "updated": 0}
    totals = {"ok": True, "updated": 0, "photos": 0, "skipped": 0, "changed": 0}
    for identifier, bucket in manifest["items"].items():
        if bucket["status"] != "ready":
            totals["skipped"] += 1
            continue
        row = models.get_item(identifier)
        if row is None:
            totals["skipped"] += 1
            continue
        data = json.loads(row["data_json"])
        expected = public_asset_url(row["listing_url"])
        photos = []
        if expected is None:
            totals["skipped"] += 1
            continue
        for entry in bucket["photos"]:
            if public_asset_url(entry["source_url"]) != expected:
                continue
            opened = private_media.open_media_file(root, entry["media_id"], entry["extension"])
            if opened is None:
                continue
            handle, size = opened
            try:
                if (
                    size != entry["size"]
                    or private_media._detect_magic(handle) != entry["content_type"]
                    or private_media._hash_handle(handle, size) != entry["sha256"]
                ):
                    continue
            finally:
                handle.close()
            photos.append(
                {
                    "media_id": entry["media_id"],
                    "url": private_media.media_url_for_item(identifier, entry["media_id"]),
                    "source_url": expected,
                    "caption": entry.get("caption"),
                    "origin": "boe_authenticated_cache",
                    "image_type": "published",
                    "verification": "authenticated_publication_cached",
                    "visual_content_verified": False,
                    "observed_at": now(),
                }
            )
        public = [
            photo
            for photo in data.get("photos", []) or []
            if isinstance(photo, dict) and photo.get("origin") != "boe_authenticated_cache"
        ]
        previous = [
            photo.get("media_id")
            for photo in data.get("photos", []) or []
            if isinstance(photo, dict) and photo.get("media_id")
        ]
        # An incomplete export must not delete photos retrieved earlier.
        if bucket["photos"] and len(photos) != len(bucket["photos"]):
            totals["skipped"] += 1
            continue
        if not photos and previous:
            totals["skipped"] += 1
            continue
        data["photos"] = public + photos
        data["photos_total_extracted"] = len(data["photos"])
        data["enrichment_limitations"] = [
            lim
            for lim in data.get("enrichment_limitations", []) or []
            if not isinstance(lim, dict) or lim.get("reason") != "boe_gallery_behind_authentication"
        ]
        _, _, _, count = catalog.derived_fields(data, row["asset_type"])
        with db.transaction() as connection:
            changed = connection.execute(
                "UPDATE items SET data_json=?,photo_count=? WHERE id=? AND data_json=?",
                (json.dumps(data, ensure_ascii=False), count, identifier, row["data_json"]),
            ).rowcount
        totals["updated"] += changed
        totals["photos"] += len(photos) if changed else 0
        if changed and previous != [photo["media_id"] for photo in photos]:
            totals["changed"] += 1
    if totals["changed"]:
        models.set_setting("catalog_last_updated_at", now(), updated_by=None)
    return totals


def trigger_after_search(data_dir):
    """Signal a pending request by file; do not invoke privileged service controls."""
    path = Path(data_dir) / "boe-photo-refresh.request"
    path.write_text(now(), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "import"])
    parser.add_argument("--input", default="/var/lib/auctionradar-boe-input/requests.json")
    parser.add_argument("--export-root", default="/var/lib/auctionradar-boe-export")
    parser.add_argument("--evaluate", action="store_true")
    args = parser.parse_args(argv)
    db.init_db(os.environ.get("AUCTIONRADAR_DATA_DIR", "/var/lib/auctionradar"))
    result = (
        prepare_requests(args.input)
        if args.action == "prepare"
        else import_export(args.export_root)
    )
    print(json.dumps(result), flush=True)
    if args.evaluate and result.get("changed"):
        import subprocess
        import sys

        subprocess.run([sys.executable, "-m", "auctionradar.web.search_runner"], check=True)
    return 0 if result.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
