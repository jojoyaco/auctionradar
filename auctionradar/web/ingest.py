"""Ingestion helpers bridging the CLI run output to the webapp database.

This module **does not** invoke ``curl`` nor open sockets. It operates on
already-produced run artefacts produced by the CLI. The runner writes
``runs/<run_id>.json``; ``ingest_run`` reads that file, re-uses
:func:`auctionradar.cli.canonical_url`, :func:`auctionradar.cli.identities`,
:func:`auctionradar.cli.normalize_fields` and :func:`auctionradar.cli.classify` to
rebuild the canonical item table, then upserts each row.

Identity stability guarantees:

* The CLI deduplicates items by URL/references/cadastral ids. The
  webapp's :func:`stable_item_key` derives its primary key from those
  identity facts only — never from the inner ``id`` field which is a
  portal-specific opaque string that may change between scrapes.
* When the runner observes a URL or reference that *did not exist*
  before, we register it in the new ``item_aliases`` table so future
  runs can match by either member of the identity set without the
  portal having to keep its references stable.
* Each lot in a multi-lot auction keeps its own row. Aliases are only
  registered when the *identity intersection* with the existing row is
  non-empty, so two different auctions under the same auction id stay
  distinct.

Absence never implies "sold": when an item disappears from a new run
we keep the previous ``first_seen`` timestamp and simply stop touching
``last_seen``. The record itself stays in the database with whatever
review verdict the user already chose.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import auctionradar.cli as search_cli  # type: ignore
from auctionradar.assets import ASSET_TYPES, normalize_category

from . import filters, models, urls

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Snapshot helpers — consumed by ``views.review_item`` and the runner
# ---------------------------------------------------------------------------


# Canonical set of fields that a reviewer actually acknowledges. These
# are deliberately small — if the canonical item changes shape the
# review snapshot stays comparable across time.
REVIEW_SNAPSHOT_FIELDS = (
    "prices",
    "possession",
    "availability",
    "right_sold",
    "title",
    "exact_price",
    "price_type",
)

# Fields whose value is *nested in a dict that already encodes a
# status*. We collapse those to just the inner status so timestamps
# inside ``evidence`` do not flip the change flag.
_SCALAR_DICT_FIELDS = {"possession", "availability"}


def review_snapshot(item: dict) -> dict:
    """Return the canonical snapshot used by both ``add_review`` and ingest.

    ``views.review_item`` stores the result here and the runner feeds
    it back into :func:`compare_snapshots` so the same definition of
    "substantive change" is used on both sides. Volatile timestamps and
    raw payloads are deliberately excluded.
    """
    out = {}
    for field in REVIEW_SNAPSHOT_FIELDS:
        value = item.get(field)
        if field in _SCALAR_DICT_FIELDS:
            out[field] = _scalar(value)
        else:
            out[field] = value
    return out


def _scalar(value: Any) -> Any:
    """Reduce nested ``{'status': '…'}`` shapes to their inner scalar.

    Useful for both comparison (a normalised item reports ``possession``
    as a dict with ``status`` and ``evidence``; we only care whether
    the ``status`` changed, not the timestamps inside ``evidence``).
    """
    if isinstance(value, dict):
        return value.get("status")
    return value


def compare_snapshots(prior: Optional[dict], current: dict) -> dict[str, bool]:
    """Return ``{field: changed}`` for each ``REVIEW_SNAPSHOT_FIELDS``.

    Both arguments are expected to be the dict produced by
    :func:`review_snapshot`; ``prior`` may be ``None`` (no review yet)
    in which case *every* field is reported as changed. The return
    value is consumed by :func:`auctionradar.web.models.update_change_flag`.
    """
    out: dict[str, bool] = {}
    for field in REVIEW_SNAPSHOT_FIELDS:
        if prior is None:
            out[field] = False
            continue
        out[field] = prior.get(field) != current.get(field)
    # ``exact_price`` and ``price_type`` together capture a price flip;
    # we still set the explicit ``exact_price`` flag so the UI can
    # surface the distinction.
    return out


# ---------------------------------------------------------------------------
# Substantive fingerprint (informs the ``items.raw_fingerprint`` column)
# ---------------------------------------------------------------------------


def substantive_fingerprint(item: dict) -> str:
    """Stable hash of the *substantive* facets of a listing.

    Used by :func:`stable_item_key` to detect identity-resilient row
    drift at scan time. Volatile timestamps (``observed_at``,
    ``raw_path``) and ad-hoc fields are stripped from the digest.
    """
    snapshot = review_snapshot(item)
    # Re-serialise the structured "prices" sub-document as bytes so a
    # numeric change in any field reflects on the fingerprint.
    snapshot["prices"] = item.get("prices")
    snapshot["possession"] = item.get("possession")
    snapshot["availability"] = item.get("availability")
    snapshot["right_sold"] = item.get("right_sold")
    encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Item shaping for the API
# ---------------------------------------------------------------------------


def shape_for_storage(item: dict, listing_url: str) -> dict:
    """Cleanse item before we persist it as ``data_json``.

    Strips large incidental blobs (raw evidence, page media) the
    runner does not need; everything else is preserved so historical
    facts and dates that *are* substantive (e.g. ``prices``,
    ``availability.status``) survive.
    """
    data = dict(item)
    for key in ("raw_evidence", "page_media", "tours_and_videos"):
        data.pop(key, None)
    data["listing_url"] = listing_url
    return data


# ---------------------------------------------------------------------------
# Identity resolution (intersection + alias table)
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    """Helper that flattens :func:`auctionradar.cli.identities` to a usable map.

    The CLI returns tuples such as ``('url', '…')`` or
    ``('source_reference', 'boe:ref-1')``. We turn each token into a
    canonical alias entry on first sight, so a renumbering inside the
    portal produces the same key (URL / reference stable) while a
    *new* URL gets registered for next time.
    """

    url: set[str]
    cadastral: set[str]
    source_reference: set[str]
    boe: set[str]
    identity: set[tuple[str, str]]

    @classmethod
    def from_item(cls, item: dict) -> "Identity":
        keys = search_cli.identities(item)
        out = cls(set(), set(), set(), set(), set(keys))
        for kind, value in keys:
            if kind == "url":
                # Canonicalise URLs so trivial trailing slash and
                # ``utm_*`` changes do not split the same row.
                out.url.add(search_cli.canonical_url(value) or value)
            elif kind == "cadastral_reference":
                out.cadastral.add(value)
            elif kind == "source_reference":
                out.source_reference.add(value)
            elif kind == "boe":
                out.boe.add(value)
        return out

    def alias_entries(self) -> Iterable[tuple[str, str]]:
        """Yield ``(kind, value)`` tuples suitable for the aliases table."""
        for url in self.url:
            yield ("url", url)
        for cadastral in self.cadastral:
            yield ("cadastral_reference", cadastral)
        for source_ref in self.source_reference:
            yield ("source_reference", source_ref)
        for boe in self.boe:
            yield ("boe", boe)


def _resolve_existing_item(ident: Identity) -> Optional[str]:
    """Find an item already known under one of these identity anchors.

    The implementation queries the ``item_aliases`` table in *kind
    priority order*:

    1. ``url`` (most specific, survives portal renumbering)
    2. ``cadastral_reference`` (national registry, very stable)
    3. ``boe`` (BOE-specific lot id, stable across portals)
    4. ``source_reference`` (last resort — **lots of unrelated
       portals share the same numeric reference**, so this anchor
       *must* come last to avoid collapsing multi-lot BOE auctions
       onto a single row).

    When the strongest anchor yields a hit we still require at
    least one *stronger-than-source-reference* anchor to coincide
    between the candidate and the lookup. This guarantees two
    different auctions under one ``source_reference`` stay distinct.
    """
    priority = ("url", "cadastral_reference", "boe", "source_reference")
    new_anchors = set(ident.alias_entries())
    for kind in priority:
        for kind_name, value in ident.alias_entries():
            if kind_name != kind:
                continue
            row = models.lookup_alias(kind_name, value)
            if row is None:
                continue
            candidate = row["item_id"]
            existing_rows = models.aliases_for_item(candidate)
            existing_anchors = {(row["alias_kind"], row["alias_value"]) for row in existing_rows}
            intersection = new_anchors & existing_anchors
            # ``url`` alone is enough; anything weaker requires at
            # least one non-trivial confirmation. Multiple BOE lots
            # all share the same ``source_reference``/``boe`` token
            # so we have to look at the URL too.
            if kind in {"url", "cadastral_reference"} and (kind_name, value) in intersection:
                return candidate
            if intersection - {("source_reference", value)}:
                return candidate
    return None


def stable_item_key(item: dict) -> str:
    """Compute the primary key for a freshly ingested item.

    The key prefers an *existing* alias match (so renumbering and
    new-identity detection stay stable) and falls back to a content
    hash of the identity set when nothing is known yet.

    BOE multi-lot safety: each lot produces its own URL via
    ``idLote=…`` and is therefore counted as its own key — the
    canonical URL preserves the lot id. Lots share their ``boe`` /
    ``source_reference`` token, but because we resolve via URL first
    and re-confirm the match in the candidate's identity set, they
    remain distinct rows.
    """
    ident = Identity.from_item(item)
    existing = _resolve_existing_item(ident)
    if existing is not None:
        return existing
    keys = sorted(ident.identity)
    # Drop any inner-id members so renumbering inside a portal
    # preserves the row.
    keys = [k for k in keys if k[0] not in {"id"}]
    if not keys:
        digest = hashlib.sha256(
            json.dumps(item, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        return digest[:20]
    digest = hashlib.sha256(json.dumps(keys, ensure_ascii=False).encode("utf-8")).hexdigest()
    return digest[:20]


# ---------------------------------------------------------------------------
# Profile overlay (settings → CLI profile)
# ---------------------------------------------------------------------------


def apply_profile_overlay(item: dict, *, settings: dict, profile: dict) -> dict:
    """Return a *new* item annotated with reasons derived from the overlay.

    The canonical :func:`auctionradar.cli.classify` does not know about
    vehicle budgets or mileage caps; we annotate the result with the
    new reasons **after** classifying. Unknown prices/mileage are
    left as ``None`` and never receive a reason — they remain pending.
    """
    enriched = dict(item)
    reasons_in = list((enriched.get("selection") or {}).get("reasons") or [])

    vehicle_settings = filters.VehicleBudgetFilter.from_overlay(
        (settings.get("vehicles") or {}) if isinstance(settings, dict) else None
    )
    if enriched.get("asset_type") == "vehicles":
        for reason in filters.vehicle_violation_reasons(enriched, vehicle_settings):
            if reason not in reasons_in:
                reasons_in.append(reason)

    preferred_regions = []
    if isinstance(settings.get("housing"), dict):
        preferred_regions = settings["housing"].get("preferred_regions") or []
    if isinstance(preferred_regions, list) and preferred_regions:
        # Surface preferred-region affinity without changing status:
        # pending items in preferred regions are still pending.
        text = json.dumps(enriched.get("location") or {}, ensure_ascii=False).lower()
        if text and any(p.lower() in text for p in preferred_regions):
            if "preferred_region_match" not in reasons_in:
                reasons_in.append("preferred_region_match")

    enriched["selection"] = {
        "status": "excluded"
        if any(r in reasons_in for r in ("vehicle_over_budget", "mileage_over_limit"))
        else enriched.get("selection", {}).get("status", "pending_review"),
        "reasons": reasons_in,
    }
    enriched["_profile_overlay_applied"] = {
        "housing_budget": (settings.get("housing") or {}).get("budget_max_all_in")
        if isinstance(settings, dict)
        else None,
        "preferred_regions": preferred_regions,
        "regions": settings.get("regions") if isinstance(settings, dict) else None,
        "vehicle_max_price": (settings.get("vehicles") or {}).get("max_vehicle_price")
        if isinstance(settings, dict)
        else None,
        "vehicle_max_mileage": (settings.get("vehicles") or {}).get("max_mileage_km")
        if isinstance(settings, dict)
        else None,
    }
    return enriched


# ---------------------------------------------------------------------------
# Run ingestion
# ---------------------------------------------------------------------------


def _resolve_run_artifact(history_dir: Path, run_id: Optional[str]) -> Optional[Path]:
    history_dir = Path(history_dir)
    if run_id:
        candidate = history_dir / f"{run_id}.json"
        if candidate.exists():
            return candidate
    # Fall back to ``latest.json`` only when no run_id is provided. The
    # runner sets ``run_id`` explicitly so this branch is the cold path.
    latest = history_dir / "latest.json"
    return latest if latest.exists() else None


def ingest_run(
    history_dir: Path,
    *,
    run_id: Optional[str] = None,
    settings: Optional[dict] = None,
    profile: Optional[dict] = None,
) -> dict:
    """Ingest a single CLI run JSON into the webapp database.

    Returns a small summary that the runner stores in ``runs.summary``.
    Keys:

    * ``ingested`` / ``new`` / ``updated`` / ``preserved_reviews`` /
      ``skipped`` — counts of items affected;
    * ``source_status_counts`` — mirror of the CLI summary;
    * ``partial`` — ``True`` when at least one source was
      ``error``, ``blocked``, ``disabled``, ``eligibility_unknown`` or
      ``asset_not_applicable``;
    * ``observed_at`` — the run's own timestamp, used by the runner to
      backdate ``first_seen`` / ``last_seen``.
    """
    history_dir = Path(history_dir)
    artifact = _resolve_run_artifact(history_dir, run_id)
    if artifact is None or not artifact.exists():
        raise FileNotFoundError(f"No run artefact found in {history_dir} (run_id={run_id})")

    with artifact.open(encoding="utf-8") as fh:
        run = json.load(fh)

    items = run.get("items") or []
    summary = {
        "ingested": 0,
        "preserved_reviews": 0,
        "new": 0,
        "updated": 0,
        "skipped": 0,
        "artifact_path": str(artifact),
        "observed_at": run.get("observed_at"),
        "source_status_counts": run.get("summary", {}).get("source_status_counts") or {},
        "selection_counts": run.get("summary", {}).get("selection_counts") or {},
        "partial": False,
    }
    failed_statuses = {
        "error",
        "blocked",
        "disabled",
        "eligibility_unknown",
        "asset_not_applicable",
    }
    if any((s in summary["source_status_counts"]) for s in failed_statuses):
        summary["partial"] = True

    overlay = settings or {}
    canonical_profile = profile or {}

    observed_at = run.get("observed_at") or datetime.now(timezone.utc).isoformat()

    for raw in items:
        enriched = apply_profile_overlay(
            normalize_category(raw), settings=overlay, profile=canonical_profile
        )
        asset = enriched.get("asset_type")
        if asset not in ASSET_TYPES or enriched.get("record_type") in (
            "development_notice",
            "auction_notice",
        ):
            summary["skipped"] += 1
            continue

        listing_url = (
            enriched.get("url") or enriched.get("discovery_url") or enriched.get("listing_url")
        )
        if not urls.is_safe_url(listing_url):
            summary["skipped"] += 1
            continue

        key = stable_item_key(enriched)
        existing = models.get_item(key)
        had_review = bool(existing and existing["latest_review_id"])

        data = shape_for_storage(enriched, listing_url)
        # The public flow must not delete photos retrieved by the
        # isolated reader with authorization. Its keys/cookies are never available here.
        if existing and existing["source_id"] in ("boe", "aeat"):
            try:
                previous = json.loads(existing["data_json"])
            except (ValueError, TypeError):
                previous = {}
            cached = [
                photo
                for photo in (previous.get("photos") or [])
                if isinstance(photo, dict)
                and photo.get("origin") == "boe_authenticated_cache"
                and re.fullmatch(r"[0-9a-f]{32}", str(photo.get("media_id") or ""))
            ]
            if cached:
                data["photos"] = list(data.get("photos") or []) + cached
                data["photos_total_extracted"] = len(data["photos"])
        fingerprint = substantive_fingerprint(enriched)
        first_seen = existing["first_seen"] if existing else observed_at
        # Per-item observed_at overrides the run-level timestamp when
        # present: it tells us *when this specific listing was last
        # spotted*, which is what ``last_seen`` should track.
        item_observed_at = enriched.get("observed_at")
        if isinstance(item_observed_at, str) and item_observed_at:
            last_seen = item_observed_at
        else:
            last_seen = observed_at
        if existing:
            try:
                if datetime.fromisoformat(
                    last_seen.replace("Z", "+00:00")
                ) < datetime.fromisoformat(existing["last_seen"].replace("Z", "+00:00")):
                    summary["skipped"] += 1
                    continue
            except (ValueError, TypeError):
                pass

        is_new = existing is None
        _, _ = models.upsert_item(
            item_id=key,
            asset_type=asset,
            source_id=enriched.get("source_id") or "",
            source_ids=enriched.get("source_ids") or [],
            first_seen=first_seen,
            last_seen=last_seen,
            data=data,
            listing_url=listing_url,
            raw_fingerprint=fingerprint,
        )
        # Register identity anchors — supports re-discovery under new
        # names without depending on URL retention.
        ident = Identity.from_item(enriched)
        for kind, value in ident.alias_entries():
            models.add_alias(key, kind, value)
        # Persist the BOE / source tokens as well even when the URL is
        # the only anchor present; the lookup finds them next run.
        models.add_alias(
            key,
            "id",
            search_cli.canonical_url(listing_url) or listing_url,
        )

        summary["ingested"] += 1
        if is_new:
            summary["new"] += 1
        else:
            summary["updated"] += 1
        if had_review:
            summary["preserved_reviews"] += 1

        if had_review:
            prior = models.last_review_snapshot(key)
            current = review_snapshot(enriched)
            changes = compare_snapshots(prior, current)
            models.update_change_flag(
                key,
                price=bool(changes.get("prices") or changes.get("price_type")),
                possession=bool(changes.get("possession")),
                availability=bool(changes.get("availability")),
                title=bool(changes.get("title")),
                right_sold=bool(changes.get("right_sold")),
                exact_price=bool(changes.get("exact_price")),
            )

    return summary
