"""Loopback-only AuctionRadar demo preview with fictional, deterministic data.

This launcher is test-only. It creates no real source catalog, starts no search
or AI workers, and serves original procedural SVG illustrations from this app.
Never point it at an existing instance or workspace.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SOURCE_ID = "synthetic-demo"
RUN_ID = "synthetic-demo-fixtures"
OBSERVED_AT = "2026-10-28T08:00:00+00:00"
ARTWORK_SCENES = (
    "courtyard",
    "facade",
    "floorplan",
    "terrace",
    "coast",
    "studio",
    "van",
)


def configure_preview_environment(data_dir: Path) -> None:
    """Drop inherited operator configuration before importing the application."""
    for name in tuple(os.environ):
        if name.startswith("AUCTIONRADAR_") or name == "CREDENTIALS_DIRECTORY":
            os.environ.pop(name, None)
    os.environ.update(
        AUCTIONRADAR_DATA_DIR=str(data_dir),
        AUCTIONRADAR_WORKSPACE_MODE="private",
        AUCTIONRADAR_REGISTRATION_ENABLED="true",
        AUCTIONRADAR_SESSION_SECURE="false",
        AUCTIONRADAR_ENV="development",
        AUCTIONRADAR_AI_PROVIDER="disabled",
    )


def illustration_svg(scene: str) -> str:
    """Return one original vector illustration, with no remote resources."""
    if scene not in ARTWORK_SCENES:
        raise KeyError(scene)

    palettes = {
        "courtyard": ("#dce9e6", "#f4e8d4", "#d5a77f", "#a96f54", "#507c76", "#f5c76f"),
        "facade": ("#dce7f1", "#f5eee0", "#d9c7ae", "#a99178", "#52758b", "#e8b967"),
        "floorplan": ("#e8e5d8", "#f7f4ea", "#d7d0bd", "#a9a18e", "#668477", "#d99c66"),
        "terrace": ("#dae8e9", "#f3e5d2", "#c9a88b", "#94755f", "#668b83", "#eebc68"),
        "coast": ("#d9e9ec", "#f6e8d5", "#e1c19a", "#b78b65", "#548a91", "#f1c56e"),
        "studio": ("#e1e8e7", "#f4ead8", "#c8b6a1", "#887663", "#547d76", "#e9b968"),
        "van": ("#dce8e6", "#f5ead8", "#e8e0cf", "#b8aa91", "#557e78", "#e6b764"),
    }
    sky, horizon, facade, shadow, accent, sun = palettes[scene]
    if scene == "floorplan":
        subject = (
            '<rect x="257" y="142" width="686" height="430" rx="5" fill="#f8f5eb" '
            'stroke="#49645d" stroke-width="12"/>'
            '<path d="M257 344h240v-202M497 344h222v228M719 142v202h224M497 344v228" '
            'fill="none" stroke="#49645d" stroke-width="10"/>'
            '<path d="M275 360h68m-68 15h54M516 160v58m15-58v42M738 360h62m80 0h45" '
            'stroke="#92aa9a" stroke-width="8" stroke-linecap="round"/>'
            '<rect x="296" y="184" width="144" height="105" rx="18" fill="#e4d6bd"/>'
            '<rect x="542" y="382" width="139" height="152" rx="18" fill="#ead9c0"/>'
            '<circle cx="829" cy="230" r="34" fill="#e8b968" opacity=".8"/>'
        )
    elif scene == "van":
        subject = (
            '<path d="M226 439h565l82 88v88H226z" fill="#f4f0e6" stroke="#536e68" stroke-width="10"/>'
            '<path d="M650 449h122l71 75H650z" fill="#8bb1b4" stroke="#536e68" stroke-width="8"/>'
            '<path d="M265 474h345v109H265z" fill="#d7e1d8"/>'
            '<path d="M294 502h278M294 525h190" stroke="#83a295" stroke-width="9" stroke-linecap="round"/>'
            '<circle cx="365" cy="616" r="49" fill="#314b48"/><circle cx="365" cy="616" r="21" fill="#e9e2d2"/>'
            '<circle cx="758" cy="616" r="49" fill="#314b48"/><circle cx="758" cy="616" r="21" fill="#e9e2d2"/>'
            '<path d="M187 660h710" stroke="#718a7d" stroke-width="7" stroke-linecap="round"/>'
        )
    else:
        roof = (
            '<path d="M286 327 600 124l314 203" fill="none" stroke="#4c6660" '
            'stroke-width="24" stroke-linejoin="round"/>'
            if scene in {"courtyard", "coast"}
            else '<path d="M252 321h696" fill="none" stroke="#4c6660" stroke-width="20"/>'
        )
        facade_x = 316 if scene == "courtyard" else 276
        facade_y = 316 if scene == "courtyard" else 323
        facade_w = 568 if scene == "courtyard" else 648
        facade_h = 276 if scene == "courtyard" else 269
        windows = []
        for row, y in enumerate((365, 453)):
            for column, x in enumerate(
                (facade_x + 62, facade_x + 205, facade_x + 348, facade_x + 491)
            ):
                width = 86 if scene in {"facade", "studio"} else 72
                windows.append(
                    f'<rect x="{x}" y="{y}" width="{width}" height="61" rx="4" '
                    f'fill="#97b8b8" stroke="#fff3df" stroke-width="8"/>'
                )
                windows.append(
                    f'<path d="M{x + width / 2:g} {y + 4}v53" stroke="#f8f0df" stroke-width="5"/>'
                )
        sea = (
            '<path d="M0 595q95-28 190 0t190 0 190 0 190 0 190 0 190 0 190 0v165H0z" fill="#78aeb1"/>'
            '<path d="M0 650q95-20 190 0t190 0 190 0 190 0 190 0 190 0" fill="none" stroke="#d7e7df" stroke-width="9" opacity=".8"/>'
            if scene == "coast"
            else '<path d="M0 592q220-26 440 0t440 0 440 0v168H0z" fill="#97aa87"/>'
        )
        extra = (
            '<path d="M390 592v-78q0-58 57-58t57 58v78" fill="#c88c68" stroke="#76594c" stroke-width="8"/>'
            '<path d="M420 592v-70a27 27 0 0 1 54 0v70" fill="#f4d895"/>'
            if scene == "courtyard"
            else '<path d="M300 591h600" stroke="#fff3df" stroke-width="7" opacity=".65"/>'
        )
        subject = (
            f'{sea}<ellipse cx="600" cy="610" rx="490" ry="32" fill="#415d55" opacity=".16"/>'
            f'<rect x="{facade_x}" y="{facade_y}" width="{facade_w}" height="{facade_h}" '
            f'rx="4" fill="{facade}" stroke="#4c6660" stroke-width="8"/>'
            f'<path d="M{facade_x + facade_w - 30} {facade_y + 8}v{facade_h - 14}" stroke="{shadow}" stroke-width="24" opacity=".34"/>'
            f"{roof}{''.join(windows)}{extra}"
            '<circle cx="1014" cy="162" r="47" fill="#f0bf68" opacity=".88"/>'
            '<path d="M98 577q20-118 74-118t75 118" fill="#638b78" opacity=".9"/>'
            '<path d="M130 576v-95m-27 42 28 15m33-31-32 24" stroke="#526d5f" stroke-width="9" stroke-linecap="round"/>'
        )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760" role="img" aria-label="Synthetic schematic illustration, not a photograph">
<defs><linearGradient id="sky" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="{sky}"/><stop offset="1" stop-color="{horizon}"/></linearGradient></defs>
<rect width="1200" height="760" fill="url(#sky)"/>
<path d="M0 575q170-40 340 0t340 0 340 0 340 0v185H0z" fill="{accent}" opacity=".16"/>
{subject}
<rect x="38" y="34" width="186" height="42" rx="21" fill="#173634" opacity=".88"/>
<text x="131" y="61" text-anchor="middle" font-family="system-ui,sans-serif" font-size="15" font-weight="700" letter-spacing="1.5" fill="#fffaf0">SYNTHETIC DEMO</text>
<rect x="34" y="681" width="1132" height="48" rx="8" fill="#172c2b" opacity=".92"/>
<circle cx="61" cy="705" r="6" fill="{sun}"/>
<text x="82" y="712" font-family="system-ui,sans-serif" font-size="17" font-weight="650" letter-spacing="1.1" fill="#fffaf0">ORIGINAL VECTOR ILLUSTRATION · NOT A PHOTOGRAPH</text>
<text x="1137" y="711" text-anchor="end" font-family="system-ui,sans-serif" font-size="12" font-weight="600" letter-spacing="1.4" fill="#d7e7df">AUCTIONRADAR</text>
</svg>'''


def fixture_items(origin: str) -> list[dict]:
    """Return deterministic fiction; all links are reserved or same-origin."""
    records = [
        {
            "fixture_id": "demo-casa-patio-lumen",
            "title": "DEMO · Vivienda patio en Puerto Lumen",
            "asset_type": "housing",
            "category": "housing",
            "location": {"municipality": "Puerto Lumen", "province": "Provincia ficticia"},
            "prices": {"asking": 186000},
            "price_type": "asking",
            "built_area_m2": 92,
            "usable_area_m2": 74,
            "bedrooms": 3,
            "bathrooms": 2,
            "scenes": ("courtyard", "facade", "floorplan"),
        },
        {
            "fixture_id": "demo-apartamento-aurora",
            "title": "DEMO · Apartamento de esquina — Distrito Aurora",
            "asset_type": "housing",
            "category": "housing",
            "location": {"municipality": "Distrito Aurora", "province": "Provincia ficticia"},
            "prices": {"asking": 142500},
            "price_type": "asking",
            "built_area_m2": 78,
            "usable_area_m2": 64,
            "bedrooms": 2,
            "bathrooms": 1,
            "scenes": ("studio", "facade"),
        },
        {
            "fixture_id": "demo-casa-terraza-nube",
            "title": "DEMO · Casa adosada en Valle Nube",
            "asset_type": "housing",
            "category": "housing",
            "location": {"municipality": "Valle Nube", "province": "Provincia ficticia"},
            "prices": {"asking": 219000},
            "price_type": "asking",
            "built_area_m2": 108,
            "usable_area_m2": 86,
            "bedrooms": 3,
            "bathrooms": 2,
            "scenes": ("terrace", "courtyard"),
        },
        {
            "fixture_id": "demo-estudio-brisa",
            "title": "DEMO · Estudio con patio — Barrio Brisa",
            "asset_type": "housing",
            "category": "housing",
            "location": {"municipality": "Barrio Brisa", "province": "Provincia ficticia"},
            "prices": {"asking": 98500},
            "price_type": "asking",
            "built_area_m2": 51,
            "usable_area_m2": 43,
            "bedrooms": 1,
            "bathrooms": 1,
            "scenes": ("coast",),
        },
        {
            "fixture_id": "demo-furgoneta-taller",
            "title": "DEMO · Furgoneta taller — Ciudad de Ejemplo",
            "asset_type": "vehicles",
            "category": "vehicles",
            "location": {"municipality": "Ciudad de Ejemplo", "province": "Provincia ficticia"},
            "prices": {"asking": 24700},
            "price_type": "asking",
            "mileage_km": 68400,
            "make": "Marca ficticia",
            "model": "Taller 42",
            "year": 2023,
            "scenes": ("van",),
        },
    ]
    items = []
    for record in records:
        fixture_id = record["fixture_id"]
        listing_url = f"https://example.invalid/synthetic-demo/{fixture_id}"
        photos = [
            {
                "url": f"{origin}/__demo/illustration/{scene}.svg",
                "caption": "Ilustración sintética original · no es una fotografía",
                "source_url": listing_url,
                "image_type": "synthetic_illustration",
                "origin": "local_synthetic_fixture",
            }
            for scene in record["scenes"]
        ]
        item = {
            "id": fixture_id,
            "source_id": SOURCE_ID,
            "source_ids": [SOURCE_ID],
            "reference": fixture_id.upper(),
            "record_type": "listing",
            "url": listing_url,
            "title": record["title"],
            "asset_type": record["asset_type"],
            "category": record["category"],
            "observed_at": OBSERVED_AT,
            "currency": "EUR",
            "price_type": record["price_type"],
            "prices": record["prices"],
            "facts_scope": "synthetic_demo_fixture",
            "availability": {"status": "available"},
            "possession": {"status": "unknown"},
            "location": record["location"],
            "documents": [],
            "photos": photos,
            "selection": {"status": "pending_review", "reasons": []},
        }
        for key in (
            "built_area_m2",
            "usable_area_m2",
            "bedrooms",
            "bathrooms",
            "mileage_km",
            "make",
            "model",
            "year",
        ):
            if key in record:
                item[key] = record[key]
        items.append(item)
    return items


def should_block_job(path: str, method: str) -> bool:
    """Block all search execution and saved-search processing mutations."""
    if path == "/api/runs":
        return method.upper() != "GET"
    if path == "/api/searches" or path.startswith("/api/searches/"):
        return method.upper() != "GET"
    return False


def preview_app(data_dir: Path, port: int):
    """Build the unchanged app with one guarded, authenticated fixture route."""
    data_dir = Path(data_dir).resolve()
    if data_dir.exists() and any(data_dir.iterdir()):
        raise ValueError("Demo data directory must be empty and disposable")
    data_dir.mkdir(parents=True, exist_ok=True)
    configure_preview_environment(data_dir)

    from flask import Response, jsonify, request

    from auctionradar.web import create_app, ingest, security

    app = create_app(
        {
            "DATA_DIR": data_dir,
            "WORKSPACE_MODE": "private",
            "REGISTRATION_ENABLED": True,
            "SESSION_COOKIE_SECURE": False,
            "TRUSTED_HOSTS": ["127.0.0.1"],
        }
    )

    @app.get("/__demo/illustration/<scene>.svg")
    def serve_illustration(scene: str):
        try:
            markup = illustration_svg(scene)
        except KeyError:
            return jsonify({"error": "not_found"}), 404
        return Response(
            markup,
            mimetype="image/svg+xml",
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/__demo/seed")
    @security.login_required
    def seed_synthetic_workspace():
        origin = request.host_url.rstrip("/")
        items = fixture_items(origin)
        with tempfile.TemporaryDirectory(prefix="auctionradar-demo-input-") as input_dir:
            artifact = {
                "run_id": RUN_ID,
                "observed_at": OBSERVED_AT,
                "items": items,
                "summary": {"source_status_counts": {"synthetic_fixture": 1}},
            }
            (Path(input_dir) / f"{RUN_ID}.json").write_text(
                json.dumps(artifact, ensure_ascii=False), encoding="utf-8"
            )
            result = ingest.ingest_run(Path(input_dir), run_id=RUN_ID)
        mapped_items = [
            {"fixture_id": item["id"], "item_id": ingest.stable_item_key(item)} for item in items
        ]
        if result["ingested"] != len(items) or result["skipped"]:
            return jsonify({"error": "fixture_ingestion_failed"}), 500
        return jsonify(
            {
                "source_id": SOURCE_ID,
                "ingested_count": result["ingested"],
                "items": mapped_items,
            }
        )

    @app.before_request
    def prevent_demo_jobs():
        if should_block_job(request.path, request.method):
            return jsonify({"error": "demo_jobs_disabled"}), 409
        return None

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    app = preview_app(args.data_dir, args.port)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    app.run(
        host="127.0.0.1",
        port=args.port,
        threaded=True,
        use_reloader=False,
        debug=False,
    )


if __name__ == "__main__":
    main()
