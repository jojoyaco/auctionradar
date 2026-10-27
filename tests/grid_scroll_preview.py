"""Isolated preview launcher for the infinite-scroll grid tests.

Spawns ``tests.ui_preview.preview_app`` on ``127.0.0.1:8767`` and seeds 85
synthetic housing listings whose only distinguishing feature is a unique public
example URL and a strictly ascending price (1..85 EUR). No real photos, no
local fixture images: only the ingest pipeline and the synthetic ``data_json``
carry the price and the deterministic URL. The temporary ``DATA_DIR`` is
deleted automatically when the process exits, so no on-disk state survives.

Run from the repository root:

    .venv/bin/python tests/grid_scroll_preview.py

The browser checks file (``tests/grid_scroll_browser_checks.js``) connects to
``http://127.0.0.1:8767`` and authenticates with the fixture credentials
already created by ``ui_preview.preview_app`` (``preview-user`` /
``preview-only-2026``). No real credentials are read or written here.

Werkzeug access logs are silenced so request URLs and session cookies do not
leak into stdout. Keep this launcher the only place that boots a preview for
the infinite-scroll contract tests; production data stays untouched.
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _seed_synthetic_housing(directory: Path) -> None:
    """Populate the preview with 85 unique housing listings (prices 1..85).

    The price is the only field the sorts use to predict order. URLs come
    from ``example.org`` (RFC 2606 reserved, never resolves to a real host).
    No photos are attached, so we exercise the "no photos" empty-photo code
    path in the card builder alongside the append/merge scroll semantics.
    """
    from auctionradar.web import ingest

    stamp = "2026-10-18T07:00:00+00:00"
    base = {
        "asset_type": "housing",
        "category": "housing",
        "source_id": "aliseda",
        "observed_at": stamp,
        "selection": {"status": "pending_review", "reasons": ["possession_unknown"]},
        "availability": {"status": "available"},
        "possession": {"status": "unknown"},
        "documents": [],
        "bedrooms": 1,
        "built_area_m2": 60,
        "currency": "EUR",
    }
    items = []
    for n in range(1, 86):  # 1..85 inclusive
        url = f"https://example.org/grid-scroll-fixture/{n:03d}"
        items.append(
            {
                **base,
                "id": f"grid-scroll-{n:03d}",
                "url": url,
                "title": f"Vivienda sintética #{n:03d}",
                "location": {"municipality": "Oviedo", "province": "Asturias"},
                "prices": {"asking": n},
                "photos": [],
            }
        )
    artifact = directory / "grid-scroll.json"
    artifact.write_text(
        __import__("json").dumps({"run_id": "grid-scroll", "observed_at": stamp, "items": items}),
        encoding="utf-8",
    )
    ingest.ingest_run(directory, run_id="grid-scroll")
    # No initial reviews are seeded: every fixture row starts in ``pending``
    # so the test can mark KO and OK without colliding with prior state.


def _build_app(directory: Path, port: int):
    from tests.ui_preview import preview_app

    return preview_app(port, directory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    # Werkzeug renders full URLs and "Cookie" headers; silencing the logger
    # keeps session/cookie material out of stdout where the launcher is
    # invoked from a parent test runner.
    logging.getLogger("werkzeug").disabled = True

    # ``TemporaryDirectory`` removes the directory on context exit; we wrap
    # the whole app.run() in it so the SQLite files vanish on shutdown.
    with tempfile.TemporaryDirectory(prefix="auctionradar-grid-scroll-") as directory:
        directory_path = Path(directory)
        app = _build_app(directory_path, args.port)
        _seed_synthetic_housing(directory_path)
        app.run(
            host="127.0.0.1",
            port=args.port,
            use_reloader=False,
            debug=False,
            threaded=True,
        )


if __name__ == "__main__":
    main()
