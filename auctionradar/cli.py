#!/usr/bin/env python3
"""Repeatable public auction search; stdout contains JSON only."""

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
DEFAULTS = ROOT / "defaults"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def canonical_url(value):
    if not value:
        return None
    p = urlsplit(str(value))
    ignored = {
        "financetype",
        "quoteid",
        "ver",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "gclid",
        "fbclid",
    }
    query = sorted((k, v) for k, v in parse_qsl(p.query) if k.lower() not in ignored)
    return urlunsplit(
        (p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), urlencode(query), "")
    )


def identities(item):
    keys = set()
    lot = None
    for url_field in ("url", "listing_url", "source_url"):
        value = item.get(url_field)
        if value and re.search(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]+", str(value), re.I):
            query = {k.lower(): v for k, v in parse_qsl(urlsplit(str(value)).query)}
            if query.get("idlote") is not None:
                lot = query["idlote"]
                break
    lot_suffix = ":lot:" + str(lot) if lot is not None else ""
    for field in (
        "id",
        "candidate_id",
        "reference",
        "listing_reference",
        "cadastral_reference",
        "fingerprint",
    ):
        value = item.get(field)
        if value:
            text = str(value).strip().lower()
            if field in ("reference", "listing_reference"):
                if re.fullmatch(r"sub-[a-z]+-\d{4}-[a-z0-9]+", text):
                    keys.add(("boe", text.upper() + lot_suffix))
                else:
                    keys.add(
                        ("source_reference", str(item.get("source_id", "unknown")) + ":" + text)
                    )
            elif field == "cadastral_reference":
                if re.fullmatch(r"[a-z0-9]{20}", text):
                    keys.add(("cadastral_reference", text))
            else:
                keys.add(
                    (
                        "id" if field in ("id", "candidate_id") else field,
                        text + lot_suffix if field in ("id", "candidate_id") else text,
                    )
                )
    for field in ("url", "listing_url", "source_url"):
        value = item.get(field)
        if value:
            keys.add(("url", canonical_url(value)))
            boe = re.search(
                r"(?<![A-Z0-9])SUB-[A-Z]+-\d{4}-[A-Z0-9]+(?![A-Z0-9])", str(value), re.I
            )
            if boe:
                keys.add(("boe", boe.group().upper() + lot_suffix))
            numeric = re.search(r"/(\d{5,})(?:[/?#]|$)", str(value))
            if numeric and item.get("source_id"):
                keys.add(("source_reference", f"{item['source_id']}:{numeric.group(1)}"))
    return keys


def scalar(value):
    if isinstance(value, dict):
        return value.get("status", value.get("value", value.get("amount")))
    return value


def normalize_fields(item):
    """Normalize facts about the current listing; ignore global navigation and recommendations."""
    item = dict(item)
    fields = (
        "built_area_m2",
        "usable_area_m2",
        "bedrooms",
        "bathrooms",
        "construction_year",
        "floor",
        "elevator",
        "location",
    )
    for field in fields:
        item.setdefault(field, None)
    evidence = item.setdefault("normalization_evidence", [])

    def assign(field, value, path):
        if value is None or value == "" or item.get(field) is not None:
            return
        if field in (
            "built_area_m2",
            "usable_area_m2",
            "bedrooms",
            "bathrooms",
            "construction_year",
        ):
            if isinstance(value, bool):
                return
            text = str(value).strip().replace(" ", "")
            if not re.fullmatch(r"\d+(?:[.,]\d+)?", text):
                return
            value = float(text.replace(",", "."))
            if value <= 0:
                return
            if (
                field == "construction_year"
                and not 1500 <= value <= datetime.now(timezone.utc).year
            ):
                return
            if field in ("bedrooms", "bathrooms", "construction_year"):
                if not value.is_integer():
                    return
                value = int(value)
        if field == "elevator":
            if isinstance(value, bool):
                pass
            elif str(value).strip().casefold() in ("si", "sí", "yes", "true"):
                value = True
            elif str(value).strip().casefold() in ("no", "false"):
                value = False
            else:
                return
        item[field] = value
        entry = {
            "field": field,
            "source_field": path,
            "value": value,
            "basis": "published_current_listing",
        }
        if entry not in evidence:
            evidence.append(entry)

    for field, value in item.get("normalized_fields", {}).items():
        if field in fields:
            assign(field, value, "normalized_fields." + field)
    aliases = {
        "superficieconstruida": "built_area_m2",
        "superficieconstruidatotal": "built_area_m2",
        "builtarea": "built_area_m2",
        "builtaream2": "built_area_m2",
        "superficieutil": "usable_area_m2",
        "usablearea": "usable_area_m2",
        "usableaream2": "usable_area_m2",
        "numerohabitaciones": "bedrooms",
        "habitaciones": "bedrooms",
        "bedrooms": "bedrooms",
        "numberofbedrooms": "bedrooms",
        "numerobanos": "bathrooms",
        "banos": "bathrooms",
        "bathrooms": "bathrooms",
        "numberofbathrooms": "bathrooms",
        "anoconstruccion": "construction_year",
        "constructionyear": "construction_year",
        "yearbuilt": "construction_year",
        "planta": "floor",
        "floor": "floor",
        "floorlevel": "floor",
        "ascensor": "elevator",
        "elevator": "elevator",
    }
    if item.get("facts_scope") == "current_listing_entity":
        location = {}
        location_keys = {
            "municipio": "municipality",
            "poblacion": "municipality",
            "addresslocality": "municipality",
            "provincia": "province",
            "addressregion": "region",
            "codigopostal": "postal_code",
            "postalcode": "postal_code",
            "direccion": "address",
            "streetaddress": "address",
        }
        for fact in item.get("facts", []):
            path = str(fact.get("field", ""))
            if re.search(r"relat|recommend|similar|translation|organization|itemlist", path, re.I):
                continue
            key = re.split(r"[.\[\]]", path)[-1]
            key = "".join(c for c in unicodedata.normalize("NFKD", key).lower() if c.isalnum())
            if key in aliases:
                assign(aliases[key], fact.get("value"), path)
            elif key in location_keys and fact.get("value") not in (None, "", 0):
                location[location_keys[key]] = fact["value"]
        if location:
            assign("location", location, "scoped_facts.address")
    # Only detail-specific descriptive text is eligible, never full-page published_text.
    text = item.get("detail_text") or item.get("description") or ""
    if isinstance(text, str):
        labels = {
            "built_area_m2": r"(?:superficie|sup\.)\s*(?:total\s*)?construida\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*m[²2]",
            "usable_area_m2": r"(?:superficie|sup\.)\s*[úu]til\s*[:\-]?\s*(\d+(?:[.,]\d+)?)\s*m[²2]",
            "bedrooms": r"(\d+)\s*(?:habitaciones|dormitorios)\b",
            "bathrooms": r"(\d+)\s*ba[ñn]os?\b",
            "construction_year": r"a[ñn]o\s+(?:de\s+)?construcci[oó]n\s*[:\-]?\s*(\d{4})",
        }
        for field, pattern in labels.items():
            match = re.search(pattern, text, re.I)
            if match:
                assign(field, match.group(1), "detail_description:" + match.group(0))
    item.setdefault("exact_price", None)
    item.setdefault("price_type", None)
    prices = item.get("prices", {})
    if (
        isinstance(prices, dict)
        and isinstance(prices.get("asking"), (int, float))
        and prices["asking"] > 0
    ):
        if item["exact_price"] is None:
            item["exact_price"] = prices["asking"]
            item["price_type"] = "asking"
    return item


def classify(item, profile, feedback, broad_mode=False):
    item = normalize_fields(item)
    reasons = []
    excluded = []
    # Broad mode: every family recognized by the shared taxonomy counts
    # as 'asset_type_confirmed'; only 'unknown' and unmodeled categories
    # require review.
    if broad_mode:
        recognized = {
            "housing",
            "vehicles",
            "commercial",
            "land",
            "boats",
            "machinery",
            "electronics",
            "furniture",
            "collectibles",
            "other",
        }
        if item.get("asset_type") not in recognized:
            reasons.append("asset_type_unconfirmed")
        elif item.get("asset_type") not in ("housing", "vehicles") and not profile.get(
            item["asset_type"]
        ):
            reasons.append("category_profile_unconfigured")
    else:
        if item.get("asset_type") not in ("housing", "vehicles"):
            reasons.append("asset_type_unconfirmed")
    for record in feedback.get("records", []):
        if record.get("apply_to_future_searches", True) and identities(item) & identities(record):
            excluded.append("user_feedback")
            item["selection_feedback"] = record
    availability = str(scalar(item.get("availability")) or "unknown").lower()
    if availability in ("sold", "vendido", "withdrawn", "retirado", "unavailable"):
        excluded.append("unavailable")
    if availability not in ("available", "active", "disponible", "for_sale", "open"):
        reasons.append("availability_unconfirmed")
    if item.get("asset_type") == "housing":
        possession = str(scalar(item.get("possession")) or "unknown").lower()
        if possession in (
            "occupied",
            "occupied_or_tenanted",
            "ocupado",
            "ocupada",
            "old_rent_tenancy",
            "renta antigua",
        ):
            excluded.append("possession_excluded")
        elif possession not in ("vacant", "free", "libre", "unoccupied"):
            reasons.append("possession_unknown")
        if str(scalar(item.get("right_sold")) or "").lower() in (
            "bare_ownership",
            "nuda propiedad",
            "undivided_share",
            "proindiviso",
            "debt",
            "credit_assignment",
        ):
            excluded.append("right_sold_excluded")
        total = scalar(item.get("all_in_cost_estimate"))
        cap = profile.get("housing", {}).get("budget", {}).get("max_all_in")
        if isinstance(total, (int, float)) and cap is not None and total > cap:
            excluded.append("all_in_over_budget")
        if total is None:
            reasons.append("all_in_cost_unknown")
    requirement_key = {"housing": "housing_information", "vehicles": "vehicle_information"}.get(
        item.get("asset_type")
    )
    for field in (
        profile.get("result_requirements", {}).get(requirement_key, {}).get("required_fields", [])
    ):
        item.setdefault(field, None)
    item.setdefault("all_in_cost_estimate", None)
    item.setdefault("renovation_cost_estimate", None)
    item.setdefault("yield_estimate", None)
    item.setdefault("photos", [])
    item.setdefault("documents", [])
    for photo in item["photos"]:
        if isinstance(photo, dict):
            photo.setdefault(
                "image_type",
                {"floorplan": "plan", "plan": "plan", "real": "real", "render": "render"}.get(
                    photo.get("kind"), "unknown"
                ),
            )
            photo.setdefault("source_url", item.get("url"))
            photo.setdefault("origin", "official_listing_url")
            photo.setdefault("caption", None)
    item["photo_status"] = "available" if item["photos"] else "no_photos_available"
    for duplicate in item.get("duplicate_observations", []):
        duplicate_selection = classify(duplicate, profile, feedback, broad_mode=broad_mode)[
            "selection"
        ]
        if duplicate_selection["status"] == "excluded":
            excluded.extend(duplicate_selection["reasons"])
    if item.get("conflicts"):
        reasons.append("conflicting_source_observations")
    item["selection"] = {
        "status": "excluded" if excluded else ("pending_review" if reasons else "candidate"),
        "reasons": excluded + reasons,
    }
    return item


def deduplicate(items, source_order=None):
    if source_order is not None:
        items = sorted(
            items,
            key=lambda item: (
                source_order.get(item.get("source_id"), len(source_order)),
                canonical_url(item.get("url")) or "",
                str(item.get("id", "")),
            ),
        )
    result = []
    known = {}
    for original in items:
        item = dict(original)
        keys = identities(item)
        index = next((known[k] for k in keys if k in known), None)
        if index is None:
            index = len(result)
            item["source_ids"] = list(
                dict.fromkeys(item.get("source_ids", []) + [item.get("source_id")])
            )
            item.setdefault("id", hashlib.sha256(str(sorted(keys)).encode()).hexdigest()[:20])
            result.append(item)
        else:
            prior = result[index]
            prior["source_ids"] = list(dict.fromkeys(prior["source_ids"] + [item.get("source_id")]))
            prior.setdefault("duplicate_observations", []).append(item)
            for field in ("photos", "documents", "evidence"):
                if isinstance(prior.get(field), list) and isinstance(item.get(field), list):
                    prior[field] += [v for v in item[field] if v not in prior[field]]
            conflicts = {
                k: {"first": prior[k], "other": v}
                for k, v in item.items()
                if k in prior
                and prior[k] != v
                and k in ("prices", "possession", "availability", "right_sold")
            }
            if conflicts:
                prior.setdefault("conflicts", []).append(
                    {"source_id": item.get("source_id"), "values": conflicts}
                )
        for k in keys:
            known[k] = index
    return result


def save_history(run, directory):
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / "latest.json"
    previous = read_json(latest) if latest.exists() else {"items": []}
    old = {v["id"]: v for v in previous.get("items", [])}
    new = {v["id"]: v for v in run["items"]}

    def stable(item):
        volatile = {
            "observed_at",
            "observed_on",
            "elapsed",
            "elapsed_seconds",
            "elapsed_ms",
            "raw_path",
            "text_path",
            "raw_dir",
            "fetched_at",
            "checked_at",
            "path",
            "requests",
            "body_sha256",
            "sha256",
        }
        if isinstance(item, dict):
            return {k: stable(v) for k, v in item.items() if k not in volatile}
        if isinstance(item, list):
            return [stable(v) for v in item]
        return item

    run["changes"] = {
        "previous_run_id": previous.get("run_id"),
        "new_ids": sorted(new.keys() - old.keys()),
        "changed_ids": sorted(
            k for k in new.keys() & old.keys() if stable(new[k]) != stable(old[k])
        ),
        "not_seen_ids": sorted(old.keys() - new.keys()),
        "sold_ids": [
            k
            for k, v in new.items()
            if str(scalar(v.get("availability"))).lower() in ("sold", "vendido")
        ],
        "absence_means_sold": False,
    }
    encoded = json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    (directory / (run["run_id"] + ".json")).write_text(encoded, encoding="utf-8")
    temporary = directory / "latest.tmp"
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(latest)


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", default=str(DEFAULTS / "source-catalog.json"))
    p.add_argument("--profile", default=str(DEFAULTS / "search-profile.json"))
    p.add_argument("--feedback", default=str(DEFAULTS / "selection-feedback.json"))
    p.add_argument("--seeds", default=str(DEFAULTS / "seeds.json"))
    p.add_argument("--source", action="append", default=[])
    p.add_argument("--asset", choices=["housing", "vehicles"])
    p.add_argument(
        "--all-assets",
        action="store_true",
        help="Recognize all supported asset families; the default covers housing and vehicles.",
    )
    p.add_argument("--region", action="append", default=[])
    p.add_argument(
        "--category",
        action="append",
        default=[],
        help="Repeatable category code (housing, vans, boats, machinery, ...). Guides broad BOE/AEAT queries; at most 16 valid codes.",
    )
    p.add_argument("--output", type=Path)
    p.add_argument("--history-dir", type=Path, default=Path.cwd() / "instance/runs")
    p.add_argument("--timeout", type=float, default=12)
    p.add_argument("--max-pages", type=int, default=2)
    p.add_argument("--max-details", type=int, default=8)
    p.add_argument("--max-documents", type=int, default=1)
    p.add_argument("--workers", type=int, default=6)
    return p


def main(argv=None):
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if min(args.timeout, args.max_pages, args.max_details, args.workers) <= 0:
        arg_parser.error("Limits must be positive")
    if args.max_documents < 0:
        arg_parser.error("--max-documents must not be negative")
    # Validate --category: at most 16 codes, each belonging to the taxonomy.
    categories = [str(value).strip() for value in (args.category or []) if str(value).strip()]
    if len(categories) > 16:
        arg_parser.error("--category: at most 16 codes")
    try:
        from auctionradar.assets import CATEGORY_CHOICES as _CAT_CHOICES

        valid_codes = {entry["id"] for entry in _CAT_CHOICES}
    except ImportError:
        valid_codes = set()
    if valid_codes:
        unknown = [code for code in categories if code not in valid_codes]
        if unknown:
            arg_parser.error("--category: unknown codes: " + ", ".join(sorted(unknown)))
    catalog = read_json(args.catalog)
    profile = read_json(args.profile)
    feedback = read_json(args.feedback)
    seed_data = read_json(args.seeds) if Path(args.seeds).exists() else {}
    if isinstance(seed_data, dict):
        seed_data = seed_data.get("sources", seed_data)
    observed = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
    raw = args.history_dir / "raw" / run_id
    raw.mkdir(parents=True, exist_ok=True)
    sources = catalog["sources"]
    ids = {s["id"] for s in sources}
    if set(args.source) - ids:
        parser().error("Fuentes desconocidas: " + ", ".join(sorted(set(args.source) - ids)))
    reports = []
    items = []
    import auctionradar.connectors.catalog as adapters

    broad_mode = bool(args.all_assets)

    def consult(source):
        base = {
            "source_id": source["id"],
            "source_url": source["url"],
            "observed_at": observed,
            "query": {
                "asset": args.asset,
                "all_assets": broad_mode,
                "regions": args.region,
                "categories": categories,
            },
            "items_count": 0,
            "requests": [],
            "limitations": [],
            "depth": "none",
        }
        if args.source and source["id"] not in args.source:
            return dict(base, status="not_selected")
        if not source.get("enabled", True):
            return dict(base, status="disabled")
        # In broad mode, do not filter by the source asset_types: let
        # the classifier decide from the title/URL content. Only
        # legacy mode retains the catalog filter.
        if args.asset and not broad_mode and args.asset not in source.get("asset_types", []):
            return dict(base, status="asset_not_applicable")
        allowed = source.get("access", {}).get("allowed_buyer_types")
        if allowed and "individual" not in allowed:
            return dict(
                base,
                status="eligibility_unknown",
                limitations=["Participación profesional; elegibilidad individual no acreditada."],
            )
        seeds = seed_data.get(source["id"], []) if isinstance(seed_data, dict) else []
        if isinstance(seeds, dict):
            seeds = seeds.get("urls", seeds.get("seeds", []))
        context = {
            "timeout": args.timeout,
            "max_pages": args.max_pages,
            "max_details": args.max_details,
            "max_documents": args.max_documents,
            "raw_dir": str(raw / source["id"]),
            "seeds": seeds,
            "observed_at": observed,
            "asset": args.asset,
            "asset_type": args.asset,
            "regions": args.region,
            "all_assets": broad_mode,
            "categories": categories,
        }
        try:
            result = (
                adapters.discover_configured(source, context)
                if os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private"
                and "adapter_id" in source
                else adapters.discover(source, context)
            )
            found = result.pop("items", [])
            for item in found:
                item.setdefault("source_id", source["id"])
                item.setdefault("observed_at", observed)
                # Broad mode: the parent taxonomy determines the category.
                if broad_mode:
                    try:
                        from auctionradar.assets import normalize_category

                        item.update(normalize_category(item))
                    except ImportError:
                        pass
                if not item.get("asset_type") and args.asset and not broad_mode:
                    item["asset_type"] = args.asset
                from auctionradar.documents import enrich_documents

                enrich_documents(item, context)
                item.update(normalize_fields(item))
            base.update(result)
            base.update(items=found, items_count=len(found))
            return base
        except Exception as error:
            return dict(base, status="error", limitations=[f"{type(error).__name__}: {error}"])

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(consult, s): s for s in sources}
        for future in as_completed(futures):
            report = future.result()
            found = report.pop("items", [])
            for item in found:
                # Broad mode: do not apply the catalog filter to the
                # asset type. Honor only an explicit --asset filter.
                if args.asset and not broad_mode and item.get("asset_type") != args.asset:
                    continue
                if args.region:
                    haystack = json.dumps(item.get("location", {}), ensure_ascii=False).casefold()
                    if not any(v.casefold() in haystack for v in args.region):
                        item.setdefault("filter_notes", []).append(
                            "Región no confirmada o fuera del filtro solicitado"
                        )
                        continue
                items.append(classify(item, profile, feedback, broad_mode=broad_mode))
            reports.append(report)
            if report["status"] not in ("not_selected", "asset_not_applicable"):
                print(
                    f"{report['source_id']}: {report['status']} ({report['items_count']})",
                    file=sys.stderr,
                )
    order = {s["id"]: i for i, s in enumerate(sources)}
    reports.sort(key=lambda x: order[x["source_id"]])
    run = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "observed_at": observed,
        "mode": "public_discovery_only",
        "filters": {
            "source": args.source,
            "asset": args.asset,
            "regions": args.region,
            "categories": categories,
            "all_assets": broad_mode,
        },
        "limits": {
            "timeout_seconds": args.timeout,
            "max_pages_per_source": args.max_pages,
            "max_details_per_source": args.max_details,
            "max_documents_per_item": args.max_documents,
        },
        "profile": profile,
        "sources": reports,
        "items": [
            classify(item, profile, feedback, broad_mode=broad_mode)
            for item in deduplicate(items, source_order=order)
        ],
        "limitations": [
            "Cobertura acotada por páginas y fichas; una portada accesible no acredita búsqueda completa.",
            "Sin autenticación portable; no se toman cookies del navegador.",
            "Los valores desconocidos permanecen null; no se calculan costes o rentabilidades sin bases.",
        ],
    }
    run["summary"] = {
        "catalog_sources": len(sources),
        "items": len(run["items"]),
        "source_status_counts": {
            s: sum(r["status"] == s for r in reports)
            for s in sorted({r["status"] for r in reports})
        },
        "selection_counts": {
            s: sum(i["selection"]["status"] == s for i in run["items"])
            for s in ("candidate", "pending_review", "excluded")
        },
    }
    save_history(run, args.history_dir)
    encoded = json.dumps(run, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError) as error:
        print(f"Error de configuración o escritura: {error}", file=sys.stderr)
        raise SystemExit(2)
