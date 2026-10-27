"""Preview of the real BOE cache integration; identity and photos are synthetic."""

import base64
import hashlib
import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.ui_preview import preview_app


def main():
    with tempfile.TemporaryDirectory(prefix="boe-media-preview-") as directory:
        app = preview_app(8766, Path(directory))
        from auctionradar.web import models
        from auctionradar.web.boe_sync import import_export

        root = Path(directory) / "boe-export"
        (root / "media").mkdir(parents=True)
        app.config["BOE_MEDIA_ROOT"] = str(root)
        # Synthetic 1×1 PNG, not a photo from a real listing.
        image = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII="
        )
        mid = "a" * 32
        (root / "media" / (mid + ".png")).write_bytes(image)
        url = "https://subastas.boe.es/detalleSubasta.php?idSub=SUB-AT-2026-SYNTHETIC&ver=3"
        item = {
            "source_id": "boe",
            "asset_type": "housing",
            "category": "housing",
            "title": "BOE — caché sintética",
            "photos": [],
            "url": url,
            "prices": {"asking": 12000},
            "location": {"municipality": "Oviedo"},
        }
        models.upsert_item(
            item_id="boe-media-fixture",
            asset_type="housing",
            source_id="boe",
            source_ids=["boe"],
            first_seen="2026-10-18T07:00:00+00:00",
            last_seen="2026-10-18T07:00:00+00:00",
            listing_url=url,
            data=item,
            raw_fingerprint="fixture",
        )
        manifest = {
            "version": 1,
            "generated_at": "2026-10-18T07:00:00+00:00",
            "items": {
                "boe-media-fixture": {
                    "status": "ready",
                    "photos": [
                        {
                            "media_id": mid,
                            "file": "media/" + mid + ".png",
                            "size": len(image),
                            "sha256": hashlib.sha256(image).hexdigest(),
                            "content_type": "image/png",
                            "caption": "Fotografía sintética",
                            "source_url": url,
                        }
                    ],
                }
            },
        }
        (root / "manifest.json").write_text(json.dumps(manifest))
        import_export(root)
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=8766, use_reloader=False, threaded=True)


if __name__ == "__main__":
    main()
