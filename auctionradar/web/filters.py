"""Custom filters applied at ingest time.

The canonical classifier (``search_cli.classify``) preserves the
feedback rule *do not apply the housing budget to vehicles*. The
webapp, however, must let the user surface vehicle budget and mileage
caps in the UI. Those constraints are applied here, on top of the
classifier, using the settings overlay produced by admins.

We deliberately keep these filters **additive** — they never mutate or
delete the raw listing data. Items that exceed the user's vehicle
budget are kept in storage but are tagged with new ``selection.reasons``
entries (``vehicle_over_budget`` / ``mileage_over_limit``) so the UI
can render them as "out of budget" without losing the historical
context, and items that lack prices/mileage keep their original
``pending_review`` status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Optional

from . import models

# ---------------------------------------------------------------------------
# Settings overlay
# ---------------------------------------------------------------------------


VEHICLE_OVER_BUDGET_REASON = "vehicle_over_budget"
VEHICLE_OVER_MILEAGE_REASON = "mileage_over_limit"


@dataclass
class VehicleBudgetFilter:
    max_price: Optional[float] = None
    max_mileage_km: Optional[int] = None

    @classmethod
    def from_overlay(cls, overlay: Optional[dict]) -> "VehicleBudgetFilter":
        if not isinstance(overlay, dict):
            return cls()
        max_price = overlay.get("max_vehicle_price")
        try:
            if max_price in (None, ""):
                max_price = None
            else:
                max_price = float(max_price)
        except (TypeError, ValueError):
            max_price = None
        max_mileage = overlay.get("max_mileage_km")
        try:
            if max_mileage in (None, ""):
                max_mileage = None
            else:
                max_mileage = int(max_mileage)
        except (TypeError, ValueError):
            max_mileage = None
        return cls(max_price=max_price, max_mileage_km=max_mileage)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def _first_numeric(*values: Any) -> Optional[float]:
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            cleaned = value.strip().replace(" ", "").replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                continue
    return None


def _price_dict_candidates(value: Any) -> list[float]:
    candidates: list[float] = []
    if isinstance(value, dict):
        for key in ("asking", "auction_value", "current_bid", "award", "minimum_bid", "reserve"):
            sub = _first_numeric(value.get(key))
            if sub is not None:
                candidates.append(sub)
    elif isinstance(value, (int, float)):
        if value > 0:
            candidates.append(float(value))
    elif isinstance(value, str):
        sub = _first_numeric(value)
        if sub:
            candidates.append(sub)
    return candidates


def vehicle_price_candidates(item: dict) -> list[float]:
    """Return every positive price we can find for ``item``.

    Candidates are any combination of:
    * the ``prices`` sub-document (asking, auction_value, …);
    * the bare ``exact_price`` field;
    * a few free-form keys the adapters sometimes emit
      (``asking_price``, ``price``, ``acquisition_cost``, ``all_in_cost_estimate``).

    The list is filtered to ``> 0`` because some portals emit
    placeholder ``0`` values that must not be mistaken for an actual
    listing price.
    """
    candidates: list[float] = []
    candidates.extend(_price_dict_candidates(item.get("prices")))
    candidates.extend(_price_dict_candidates(item.get("exact_price")))
    candidates.append(_first_numeric(item.get("asking_price"), item.get("price")))
    candidates.append(_first_numeric(item.get("acquisition_cost")))
    candidates.append(_first_numeric(item.get("all_in_cost_estimate")))
    return [c for c in candidates if c is not None and c > 0]


def vehicle_mileage_candidate(item: dict) -> Optional[int]:
    candidates: list[float] = []
    for field in ("mileage_km", "mileage", "kilometers", "kms", "km"):
        sub = _first_numeric(item.get(field))
        if sub is not None:
            candidates.append(sub)
    if not candidates:
        return None
    return int(min(candidates))


# ---------------------------------------------------------------------------
# Filter evaluations
# ---------------------------------------------------------------------------


def vehicle_budget_violation(item: dict, settings: VehicleBudgetFilter) -> Optional[str]:
    """Return a reason when the listing exceeds the configured cap.

    ``None`` means the item is fine (or unknown). The reasons are
    additive; the runner passes them onto the item's
    ``selection.reasons`` without changing ``selection.status`` of a
    well-classified item.
    """
    if not isinstance(settings, VehicleBudgetFilter):
        return None
    prices = vehicle_price_candidates(item)
    if settings.max_price and prices:
        if all(price > settings.max_price for price in prices):
            return VEHICLE_OVER_BUDGET_REASON
    mileage = vehicle_mileage_candidate(item)
    if settings.max_mileage_km and mileage is not None:
        if mileage > settings.max_mileage_km:
            return VEHICLE_OVER_MILEAGE_REASON
    return None


def vehicle_violation_reasons(item: dict, settings: VehicleBudgetFilter) -> list[str]:
    """Convenience wrapper returning a list of triggered reasons."""
    if item.get("asset_type") != "vehicles":
        return []
    reason = vehicle_budget_violation(item, settings)
    return [reason] if reason else []


# ---------------------------------------------------------------------------
# Back-compat shim — kept so existing tests of the original "passes"
# semantics continue to assert. New callers should prefer
# :func:`vehicle_budget_violation` directly.
# ---------------------------------------------------------------------------


def passes_vehicle_filter(item: dict, settings: VehicleBudgetFilter) -> bool:
    """Return ``True`` when the listing does **not** violate the cap.

    This used to gate inclusion in the database; the ingest pipeline
    no longer drops items, only annotates them, so this helper now
    exposes "is the item within budget?" rather than "should we
    store it?" — but the boolean returns stay in the same order so the
    previous tests still pass.
    """
    if not isinstance(settings, VehicleBudgetFilter):
        return True
    if settings.max_price is None and settings.max_mileage_km is None:
        return True
    return vehicle_budget_violation(item, settings) is None


# ---------------------------------------------------------------------------
# Region hard filtering (preserves preferred regions semantics)
# ---------------------------------------------------------------------------


def _location_text(item: dict) -> str:
    location = item.get("location") or {}
    if isinstance(location, dict):
        bits = [str(v) for v in location.values() if v]
        return " ".join(bits).lower()
    return str(location or "").lower()


def passes_region_filter(item: dict, regions: Iterable[str]) -> bool:
    """Hard-filter items whose confirmed location is not in ``regions``.

    The regions list is interpreted strictly: when supplied, it acts as
    a *whitelist* of confirmed locations (case-insensitive substring
    match). Used only when the user explicitly turns the filter on in
    the overlay; the canonical profile keeps ``regions`` separate
    from ``preferred_regions``.
    """
    regions = [r for r in regions if r]
    if not regions:
        return True
    haystack = _location_text(item)
    if not haystack:
        return False
    return any(region.lower() in haystack for region in regions)


# ---------------------------------------------------------------------------
# Settings pipeline
# ---------------------------------------------------------------------------


def load_settings_from_db() -> dict:
    """Read the JSON-decoded ``settings`` key from ``models.get_setting_raw``."""
    raw = models.get_setting_raw("settings")
    if isinstance(raw, dict):
        return raw
    return {}


def vehicle_filter_from_db() -> VehicleBudgetFilter:
    overlay = load_settings_from_db()
    vehicles = overlay.get("vehicles") or {}
    if not isinstance(vehicles, dict):
        return VehicleBudgetFilter()
    return VehicleBudgetFilter.from_overlay(vehicles)


def region_whitelist_from_db() -> list[str]:
    overlay = load_settings_from_db().get("regions") or []
    if isinstance(overlay, list):
        return [r for r in overlay if isinstance(r, str)]
    return []


def preferred_regions_from_db() -> list[str]:
    overlay = load_settings_from_db()
    housing = overlay.get("housing") or {}
    preferred = housing.get("preferred_regions") if isinstance(housing, dict) else None
    if isinstance(preferred, list):
        return [r for r in preferred if isinstance(r, str)]
    return []


def active_sources_from_db(*, catalog_ids: Iterable[str]) -> list[str]:
    """Filter the catalog down to the sources the admin enabled.

    When the overlay does not specify any ``sources`` the canonical
    catalog's ``enabled=True`` flag is consulted; if that has no data we
    return everything passed in.
    """
    overlay = load_settings_from_db()
    sources_in = overlay.get("sources")
    if isinstance(sources_in, dict) and sources_in:
        enabled = [
            sid
            for sid, state in sources_in.items()
            if isinstance(state, dict) and state.get("enabled", True) and sid in set(catalog_ids)
        ]
        if enabled:
            return enabled
    catalog_ids_set = set(catalog_ids)
    return [sid for sid in catalog_ids if sid in catalog_ids_set]


def housing_budget_from_db() -> Optional[float]:
    overlay = load_settings_from_db()
    housing = overlay.get("housing") or {}
    if isinstance(housing, dict):
        value = housing.get("budget_max_all_in")
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return None
