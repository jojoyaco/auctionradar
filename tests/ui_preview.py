"""Deterministic local view for testing the UI without production credentials or data.

Run: .venv/bin/python tests/ui_preview.py --port 8765
Dedicated test user: preview-user / preview-only-2026
Always listens on loopback and stores state in a temporary directory.
"""

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def preview_app(port, directory):
    os.environ["AUCTIONRADAR_DATA_DIR"] = str(directory)
    os.environ["AUCTIONRADAR_SECRET_KEY"] = "preview-only-not-a-production-secret"
    os.environ["AUCTIONRADAR_ENV"] = "development"
    from flask import Response, jsonify

    from auctionradar.web import create_app, ingest, models
    from auctionradar.web.security import hash_password

    app = create_app(
        {
            "DATA_DIR": directory,
            "SECRET_KEY": os.environ["AUCTIONRADAR_SECRET_KEY"],
            "SESSION_COOKIE_SECURE": False,
            "TRUSTED_HOSTS": ["127.0.0.1", "localhost"],
        }
    )
    digest, salt = hash_password("preview-only-2026")
    uid = models.create_user("preview-user", digest, salt, is_admin=True)
    stamp = "2026-10-17T07:00:00+00:00"
    photos = [
        {
            "url": f"http://127.0.0.1:{port}/__preview/photo/{i}",
            "caption": f"Preview photo {i}",
            "image_type": "real",
            "source_url": "https://example.org/fixture",
        }
        for i in range(1, 4)
    ]
    base = {
        "asset_type": "housing",
        "source_id": "aliseda",
        "observed_at": stamp,
        "selection": {"status": "pending_review", "reasons": ["possession_unknown"]},
        "availability": {"status": "available"},
        "possession": {"status": "unknown"},
        "documents": [],
        "bedrooms": 2,
        "built_area_m2": 85,
        "currency": "EUR",
    }
    items = [
        {
            **base,
            "id": "preview-one",
            "url": "https://example.org/preview-one",
            "title": "Vivienda de prueba — datos originales sin traducir",
            "location": {"municipality": "Oviedo", "province": "Asturias"},
            "prices": {"asking": 28300},
            "photos": photos,
        },
        {
            **base,
            "id": "preview-two",
            "url": "https://example.org/preview-two",
            "title": "Una vivienda con un título de varias líneas para comprobar la alineación de toda la cuadrícula",
            "location": {
                "address": "Calle de la Prueba 12",
                "municipality": "Gijón",
                "province": "Asturias",
            },
            "prices": {"asking": 42500},
            "photos": photos[:2],
        },
        {
            **base,
            "id": "preview-three",
            "url": "https://example.org/preview-three",
            "title": "Vivienda sin fotografía",
            "location": None,
            "prices": {},
            "photos": [],
        },
        {
            **base,
            "id": "preview-van",
            "asset_type": "vehicles",
            "source_id": "ayvens",
            "url": "https://example.org/preview-van",
            "title": "Furgoneta de prueba sin imagen",
            "location": {"municipality": "Madrid"},
            "mileage_km": 42000,
            "prices": {"asking": 21000},
            "photos": [],
        },
        {
            **base,
            "id": "preview-boat",
            "asset_type": "boats",
            "category": "boats",
            "source_id": "boe",
            "url": "https://example.org/preview-boat",
            "title": "Lancha de prueba 5 metros",
            "facts_scope": "current_listing_entity",
            "location": {"municipality": "Gijón", "province": "Asturias"},
            "length_m": 5,
            "prices": {"asking": 12000},
            "photos": photos[:2],
            "facts": [
                {"field": "prices.asking", "value": 12000},
                {"field": "length_m", "value": 5},
            ],
        },
    ]
    artifact = Path(directory) / "preview.json"
    artifact.write_text(
        json.dumps({"run_id": "preview", "observed_at": stamp, "items": items}), encoding="utf-8"
    )
    ingest.ingest_run(directory, run_id="preview")
    item_id = ingest.stable_item_key(items[1])
    models.add_review(
        item_id=item_id,
        user_id=uid,
        status="ko",
        notes="Fixture note: no traducir / do not translate my words",
        snapshot=ingest.review_snapshot(items[1]),
    )
    models.create_run(
        run_id="preview-structured-source", created_by=uid, sources=["boe", "aliseda"]
    )
    models.finalize_run(
        "preview-structured-source",
        status="partial",
        summary={
            "ingest": {"ingested": 4, "new": 3, "updated": 1},
            "sources": [
                {
                    "source_id": "boe",
                    "status": "blocked",
                    "depth": "listing",
                    "items_count": 0,
                    "limitations": [
                        {"reason": "authentication_required"},
                        {"reason": {"message": "Document unavailable"}},
                    ],
                },
                {
                    "source_id": "aliseda",
                    "status": "partial",
                    "depth": "listing_and_details",
                    "items_count": 3,
                    "limitations": [{"reason": "max_pages_reached", "limit": 2}],
                },
            ],
        },
    )

    @app.get("/__preview/photo/<int:index>")
    def preview_photo(index):
        colors = ["#c6dcd4", "#b8c6df", "#ead8bb"]
        color = colors[(index - 1) % 3]
        svg = (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="560" viewBox="0 0 900 560">'
            f'<rect width="900" height="560" fill="{color}"/>'
            '<path d="M0 440 240 240 460 400 660 180 900 360V560H0Z" fill="#355d54" opacity=".35"/>'
            f'<text x="450" y="270" text-anchor="middle" font-family="sans-serif" font-size="48" fill="#203d35">PREVIEW {index}</text></svg>'
        )
        return Response(svg, mimetype="image/svg+xml")

    @app.before_request
    def no_external_searches_from_preview():
        from flask import request

        if request.path == "/api/runs" and request.method == "POST":
            return jsonify({"error": "preview_only"}), 409

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="auctionradar-ui-preview-") as directory:
        app = preview_app(args.port, directory)
        logging.getLogger("werkzeug").disabled = True
        app.run(host="127.0.0.1", port=args.port, use_reloader=False, debug=False, threaded=False)


if __name__ == "__main__":
    main()
