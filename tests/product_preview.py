"""Loopback-only private-product preview, using disposable synthetic data.

Never point this at an existing instance. Test-only routes below are not part
of the application or the distributable package.
"""

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def preview_app(directory):
    # Do not inherit any real operator paths, session keys or certificate keys.
    for name in list(os.environ):
        if name.startswith("AUCTIONRADAR_") or name == "CREDENTIALS_DIRECTORY":
            os.environ.pop(name, None)
    os.environ.update(
        AUCTIONRADAR_DATA_DIR=str(directory),
        AUCTIONRADAR_WORKSPACE_MODE="private",
        AUCTIONRADAR_REGISTRATION_ENABLED="true",
        AUCTIONRADAR_SESSION_SECURE="false",
        AUCTIONRADAR_ENV="development",
        AUCTIONRADAR_AI_PROVIDER="disabled",
    )
    from flask import jsonify, request

    from auctionradar.web import create_app, ingest, models, security

    app = create_app(
        {
            "TESTING": False,
            "DATA_DIR": directory,
            "SESSION_COOKIE_SECURE": False,
            "TRUSTED_HOSTS": ["127.0.0.1"],
        }
    )

    @app.post("/__preview/seed")
    @security.login_required
    def seed():
        user = security.load_current_user()
        item = {
            "id": "same-public-auction",
            "title": "Synthetic auction — " + user["username"],
            "asset_type": "housing",
            "source_id": "fixture",
            "url": "https://example.org/synthetic-auction",
            "reference": "FIXTURE",
            "prices": {"asking": 10000},
            "facts_scope": "current_listing_entity",
            "availability": {"status": "available"},
            "possession": {"status": "unknown"},
            "photos": [],
            "documents": [],
            "secret_notes": "INTERNAL-FIXTURE-NOT-SHARED",
        }
        key = ingest.stable_item_key(item)
        models.upsert_item(
            item_id=key,
            asset_type="housing",
            source_id="fixture",
            source_ids=["fixture"],
            first_seen="2026-10-26T07:00:00Z",
            last_seen="2026-10-26T07:00:00Z",
            data=item,
            listing_url=item["url"],
            raw_fingerprint=ingest.substantive_fingerprint(item),
        )
        return jsonify({"id": key})

    @app.before_request
    def no_preview_network_jobs():
        if request.path == "/api/runs" and request.method == "POST":
            return jsonify({"error": "preview_only"}), 409

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="auctionradar-product-preview-") as directory:
        app = preview_app(Path(directory))
        logging.getLogger("werkzeug").disabled = True
        app.run(host="127.0.0.1", port=args.port, threaded=True, use_reloader=False, debug=False)


if __name__ == "__main__":
    main()
