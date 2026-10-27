"""Catalog filters and derived values; never invent an asset valuation."""

import hashlib
import json
import math
import re
import unicodedata

from auctionradar.assets import CATEGORY_CHOICES, normalize_category

from .urls import is_safe_url

CATEGORY_IDS = {c["id"] for c in CATEGORY_CHOICES} | {"vehicles"}
SORTS = {"updated", "newest", "oldest", "price_asc", "price_desc", "ai_score"}


def fold_text(value):
    text = str(value or "").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def amount(value):
    if isinstance(value, dict):
        value = value.get("amount", value.get("value"))
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (TypeError, ValueError):
        return None


def published_price(item):
    prices = item.get("prices") or {}
    if not isinstance(prices, dict):
        prices = {}
    for kind in (
        "asking",
        "award",
        "current_bid",
        "bid",
        "minimum_bid",
        "starting",
        "reserve",
        "auction_value",
    ):
        value = amount(prices.get(kind))
        if value is not None and value > 0:
            return value, kind
    value = amount(item.get("exact_price"))
    if value is not None and value > 0 and item.get("price_type") not in ("appraisal", "valuation"):
        return value, item.get("price_type") or "published"
    return None, None


def derived_fields(data, asset_type=None):
    enriched = normalize_category(
        {**data, "asset_type": asset_type or data.get("asset_type", "unknown")}
    )
    price, kind = published_price(enriched)
    raw_photos = enriched.get("photos") or []
    photos = (
        sum(
            isinstance(p, dict)
            and (
                is_safe_url(p.get("url"))
                or (
                    p.get("origin") == "boe_authenticated_cache"
                    and re.fullmatch(r"[0-9a-f]{32}", str(p.get("media_id") or "")) is not None
                )
            )
            for p in raw_photos
        )
        if isinstance(raw_photos, list)
        else 0
    )
    return enriched["category"], price, kind, photos


def normalize_criteria(value, source_ids=None):
    if not isinstance(value, dict):
        raise ValueError("criteria_required")
    allowed = {
        "categories",
        "query",
        "regions",
        "preferred_regions",
        "price_min",
        "price_max",
        "all_in_max",
        "include_unknown_price",
        "required_fields",
        "source_ids",
        "exclude_occupied",
        "include_unavailable",
    }
    if set(value) - allowed:
        raise ValueError("unknown_criteria")
    out = {}
    for key, maximum in (
        ("categories", 20),
        ("regions", 52),
        ("preferred_regions", 52),
        ("required_fields", 30),
        ("source_ids", 100),
    ):
        entries = value.get(key) or []
        if (
            not isinstance(entries, list)
            or len(entries) > maximum
            or any(not isinstance(v, str) or not v.strip() or len(v) > 120 for v in entries)
        ):
            raise ValueError("invalid_" + key)
        out[key] = list(dict.fromkeys(v.strip() for v in entries))
    if set(out["categories"]) - CATEGORY_IDS:
        raise ValueError("invalid_categories")
    if source_ids is not None and set(out["source_ids"]) - set(source_ids):
        raise ValueError("invalid_sources")
    query = value.get("query", "")
    if not isinstance(query, str) or len(query) > 500:
        raise ValueError("invalid_query")
    out["query"] = query.strip()
    for key in ("price_min", "price_max", "all_in_max"):
        raw = value.get(key)
        parsed = amount(raw) if raw not in (None, "") else None
        if raw not in (None, "") and (parsed is None or parsed > 1e12):
            raise ValueError("invalid_" + key)
        out[key] = parsed
    if (
        out["price_min"] is not None
        and out["price_max"] is not None
        and out["price_min"] > out["price_max"]
    ):
        raise ValueError("invalid_price_range")
    for key, default in (
        ("include_unknown_price", True),
        ("exclude_occupied", False),
        ("include_unavailable", False),
    ):
        flag = value.get(key, default)
        if not isinstance(flag, bool):
            raise ValueError("invalid_" + key)
        out[key] = flag
    if out["all_in_max"] is not None and "all_in_cost_estimate" not in out["required_fields"]:
        out["required_fields"].append("all_in_cost_estimate")
    if out["exclude_occupied"] and "possession" not in out["required_fields"]:
        out["required_fields"].append("possession")
    return out


def matches_criteria(item, criteria):
    data = normalize_category(item)
    if "user_feedback" in (data.get("selection") or {}).get("reasons", []) and not criteria.get(
        "include_unavailable"
    ):
        return False
    cats = criteria.get("categories") or []
    if (
        cats
        and data["category"] not in cats
        and not ("vehicles" in cats and data["asset_type"] == "vehicles")
    ):
        return False
    if criteria.get("source_ids") and not (
        {data.get("source_id")} | set(data.get("source_ids") or [])
    ) & set(criteria["source_ids"]):
        return False
    text = fold_text(
        json.dumps(
            {
                k: data.get(k)
                for k in ("title", "description", "detail_text", "location", "reference")
            },
            ensure_ascii=False,
        )
    )
    if criteria.get("query") and not all(
        word in text for word in fold_text(criteria["query"]).split()
    ):
        return False
    location = fold_text(json.dumps(data.get("location") or {}, ensure_ascii=False))
    if criteria.get("regions") and not any(
        fold_text(region) in location for region in criteria["regions"]
    ):
        return False
    price, _ = published_price(data)
    if price is None and (
        criteria.get("price_min") is not None or criteria.get("price_max") is not None
    ):
        if not criteria.get("include_unknown_price", True):
            return False
    if price is not None:
        if criteria.get("price_min") is not None and price < criteria["price_min"]:
            return False
        if criteria.get("price_max") is not None and price > criteria["price_max"]:
            return False
    all_in = amount(data.get("all_in_cost_estimate"))
    if (
        all_in is not None
        and criteria.get("all_in_max") is not None
        and all_in > criteria["all_in_max"]
    ):
        return False

    def status(value):
        return value.get("status") if isinstance(value, dict) else value

    if not criteria.get("include_unavailable") and status(data.get("availability")) in (
        "sold",
        "vendido",
        "withdrawn",
        "unavailable",
    ):
        return False
    if criteria.get("exclude_occupied") and status(data.get("possession")) in (
        "occupied",
        "occupied_or_tenanted",
        "old_rent_tenancy",
    ):
        return False
    return True


def content_fingerprint(item):
    volatile = {
        "observed_at",
        "observed_on",
        "first_seen",
        "last_seen",
        "fetched_at",
        "checked_at",
        "elapsed_seconds",
        "raw_path",
        "raw_dir",
        "path",
        "text_path",
        "requests",
        "published_text",
        "page_media",
        "raw_evidence",
    }

    def stable(value):
        if isinstance(value, dict):
            return {k: stable(v) for k, v in value.items() if k not in volatile}
        if isinstance(value, list):
            return [stable(v) for v in value]
        return value

    return hashlib.sha256(
        json.dumps(stable(item), sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()
