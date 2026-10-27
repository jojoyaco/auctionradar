"""Shared asset taxonomy.

ASSET_TYPES defines root families; CATEGORY_CHOICES provides UI identifiers,
English/Spanish labels and their family. infer_category detects a category;
normalize_category returns a copy with consistent category and asset_type.

Unknown evidence remains unknown. The other category requires confirmed asset
detail, not missing information. Web ingestion consumes this shared taxonomy."""

from __future__ import annotations

import re
import unicodedata

# Root family. Order matters: evaluate the most specific match first.
ASSET_TYPES: tuple = (
    "housing",
    "commercial",
    "land",
    "vehicles",
    "boats",
    "machinery",
    "electronics",
    "furniture",
    "collectibles",
    "other",
    "unknown",
)

# UI categories. code -> (label_es, label_en, asset_type)
_CATEGORY_DEFS: dict = {
    "housing": ("Vivienda", "Housing", "housing"),
    "commercial": ("Local comercial", "Commercial", "commercial"),
    "garages": ("Plaza de garaje", "Garage", "commercial"),
    "land": ("Terreno / Solar", "Land", "land"),
    "vans": ("Furgoneta", "Van", "vehicles"),
    "cars": ("Coche", "Car", "vehicles"),
    "motorcycles": ("Moto", "Motorcycle", "vehicles"),
    "trucks": ("Camión", "Truck", "vehicles"),
    "vehicles_unspec": ("Vehículo (sin especificar)", "Vehicle (unspecified)", "vehicles"),
    "boats": ("Embarcación", "Boat", "boats"),
    "machinery": ("Maquinaria", "Machinery", "machinery"),
    "electronics": ("Electrónica", "Electronics", "electronics"),
    "furniture": ("Mobiliario", "Furniture", "furniture"),
    "collectibles": ("Objeto de colección", "Collectible", "collectibles"),
    "other": ("Otro", "Other", "other"),
    "unknown": ("Sin clasificar", "Unclassified", "unknown"),
}

CATEGORY_CHOICES: list = [
    {"id": code, "label_es": es, "label_en": en, "asset_type": fam}
    for code, (es, en, fam) in _CATEGORY_DEFS.items()
]


def _norm(value):
    if value is None:
        return ""
    text = str(value)
    # Strip accents and lowercase robustly.
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    ).lower()


# Recognition patterns. Each entry maps to (asset_family, category_code).
# Evaluate the most specific families (boats, machinery, electronics...) BEFORE
# generic vehicle families to avoid false positives such as "moto naútica".
_HOUSING_RE = re.compile(
    r"\b(?:vivienda|apartamento|piso(?:s)?\b|casa(?:s)?\b|residencial|habitacion|duplex|atico|chalet|estudio|loft)\b"
)
_GARAGES_RE = re.compile(r"\b(?:plaza(?:s)? de (?:garaje|aparcamiento)|garajes?|parking(?:s)?)\b")
_COMMERCIAL_RE = re.compile(
    r"\b(?:local(?:es)?(?: comercial(?:es)?)?|oficina(?:s)?|nave(?:s)? industrial(?:es)?|trastero(?:s)?)\b"
)
_LAND_RE = re.compile(
    r"\b(?:solar(?:es)?|suelo(?:s)?|terreno(?:s)?|parcela(?:s)?|finca rustica|finca urbanizable)\b"
)
_VANS_RE = re.compile(
    r"\b(?:furgon(?:eta)?|berlingo|transit|ducato|sprinter|kangoo|combo|caddy|vito|transporter|trafic|master\b|boxer|jumpy|expert\b|proace|movano|vivaro|camper|autocaravana)\b"
)
_CARS_RE = re.compile(
    r"\b(?:coche(?:s)?|automovil(?:es)?|turismo(?:s)?|sedan|berlina(?:s)?|compacto(?:s)?|suv)\b"
)
_MOTORCYCLES_RE = re.compile(
    r"\b(?:moto(?:s)?(?: ciclist(?:a|as))?|motocicleta(?:s)?|ciclomotor(?:es)?|scooter(?:s)?|vespa)\b"
)
# 'camion' with or without an accent; evaluate it after 'camioneta' (not covered).
_TRUCKS_RE = re.compile(
    r"\b(?:cami[oó]n(?:es)?|tracto(?:r|camion(?:eta)?)|camioneta(?:s)?|furg[oó]n(?:es)? pesado(?:s)?)\b"
)
_BOATS_NAUTICAL_RE = re.compile(
    r"\b(?:embarcaciones?\b|embarcaci[oó]n(?:es)?|barco(?:s)?|buque(?:s)?|boat(?:s)?|yacht(?:s)?|sailboat(?:s)?|motorboat(?:s)?|yate(?:s)?|velero(?:s)?|lancha(?:s)?(?:\s+neum[aá]tica(?:s)?)?|moto(?:s)?\s+na[uú]tica(?:s)?|jet\s*ski|kayak(?:s)?(?:\s+de\s+mar)?|paddle\s*surf|surf(?:board)?|catamar[aá]n(?:es)?)\b"
)
# 'neumatico' alone does not qualify; it requires a nautical term before or after it.
_BOAT_TIRE_FALSE_POSITIVE = re.compile(
    r"(?:\b(?:neumatico(?:s)?|rueda(?:s)?|cubierta(?:s)?|llanta(?:s)?)\b)"
)
_MACHINERY_RE = re.compile(
    r"\b(?:maquinaria(?:s)?|tractor(?:es)?|excavadora(?:s)?|retro(?:s)?(?:excavadora(?:s)?)?|grua(?:s)?(?: de obra)?|cortacesped|cosechadora(?:s)?|motocultor(?:es)?|fresadora(?:s)? industrial(?:es)?|torno(?:s)?(?: industrial(?:es)?)?|generador(?:es)?(?: el[eé]ctrico(?:s)?)?|compresor(?:es)? industrial(?:es)?)\b"
)
_ELECTRONICS_RE = re.compile(
    r"\b(?:electrodom[eé]stico(?:s)?|televisor(?:es)?|televisi[oó]n(?:es)?|tv\b|monitor(?:es)?|ordenador(?:es)?|computador(?:es)?|port[aá]til(?:es)?|laptop(?:s)?|smartphone(?:s)?|m[oó]vil(?:es)?(?: de \w+)?|tablet(?:s)?|consola(?:s)?(?: de videojuegos)?|playstation|xbox|nintendo|c[aá]mara(?:s)?(?: digital(?:es)?)?|impresora(?:s)?|esc[aá]ner(?:es)?|router(?:es)?|electrodom)\b"
)
_FURNITURE_RE = re.compile(
    r"\b(?:mobiliario(?:s)?|mueble(?:s)?|sof[aá](?:s)?|sill[oó]n(?:es)?|mesa(?:s)?(?: de \w+)?|silla(?:s)?|armario(?:s)?|estanter[ií]a(?:s)?|cama(?:s)?(?: \w+)?|colch[oó]n(?:es)?|c[oó]moda(?:s)?|vajilla(?:s)?|cuberter[ií]a(?:s)?|librer[ií]a(?:s)?)\b"
)
_COLLECTIBLES_RE = re.compile(
    r"\b(?:joya(?:s)?|jewell?ery|antiguedad(?:es)?|obra(?:s)?|cuadro(?:s)?|painting(?:s)?|litograf[ií]a(?:s)?|escultura(?:s)?|estatua(?:s)?|moneda(?:s)?(?: antigua(?:s)?)?|sello(?:s)?|c[oó]mic(?:s)?|figura(?:s)?(?: de acci[oó]n)?|juguete(?:s)?(?: antiguo(?:s)?|de colecci[oó]n)?|vinilo(?:s)?|disco(?:s)?(?: de vinilo)?)\b"
)


def _match_first(text, patterns):
    """Return the first matching (category_code, family), or None."""
    for pattern, (category, family) in patterns:
        if pattern.search(text):
            return category, family
    return None


# Evaluation order — from most specific to most general.
# Each entry maps to (category_code, family). The category_code must be valid.
# en CATEGORY_CHOICES (UI code).
_DETECTION_RULES: list = [
    # Boats require an explicit nautical term. Evaluate them BEFORE motorcycles
    # so "moto naútica" is classified as a boat.
    (_BOATS_NAUTICAL_RE, ("boats", "boats")),
    # Machinery: tractor/excavadora/torno, etc.
    (_MACHINERY_RE, ("machinery", "machinery")),
    # Electronics: TVs, mobile phones, laptops, etc.
    (_ELECTRONICS_RE, ("electronics", "electronics")),
    # Collectibles: obra, moneda antigua, vinilo, etc.
    (_COLLECTIBLES_RE, ("collectibles", "collectibles")),
    # Furniture: mueble, sofá, cama, etc.
    (_FURNITURE_RE, ("furniture", "furniture")),
    # Trucks: before generic cars/vans.
    (_TRUCKS_RE, ("trucks", "vehicles")),
    # Vans: before cars/motorcycles.
    (_VANS_RE, ("vans", "vehicles")),
    # Motorcycles: before cars.
    (_MOTORCYCLES_RE, ("motorcycles", "vehicles")),
    # Cars.
    (_CARS_RE, ("cars", "vehicles")),
    # Land: solar, suelo, terreno.
    (_LAND_RE, ("land", "land")),
    # Garages: plaza de garaje, parking — UI code 'garages', family commercial.
    (_GARAGES_RE, ("garages", "commercial")),
    # Commercial: local, oficina, nave industrial.
    (_COMMERCIAL_RE, ("commercial", "commercial")),
    # Housing last — the most general category.
    (_HOUSING_RE, ("housing", "housing")),
]


def _looks_like_boat(text):
    """Reject isolated tire and street-name matches.

    The Spanish word neumático can describe a tire or an inflatable boat. Require
    explicit nautical context rather than treating that word alone as a boat."""
    return bool(_BOATS_NAUTICAL_RE.search(text))


def _looks_like_street_name(text):
    """Do not classify an address such as Calle del Barco as a boat.

    A nautical word preceded by a street-type name is treated conservatively."""
    return bool(
        re.search(
            r"\b(?:calle|avenida|plaza|paseo|carrer|carretera|via)\b[^.\n]{0,40}\bbarco\b",
            text,
            re.I,
        )
    )


def infer_category(item):
    """Detect a category from a listing dictionary or text.

    The title takes precedence. Street names do not establish nautical assets.
    Use other only for confirmed detail that does not match a supported category;
    otherwise preserve unknown."""
    if isinstance(item, dict):
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        text = str(item.get("text") or item.get("description") or "")
    else:
        title = str(item or "")
        url = ""
        text = ""

    title_norm = _norm(title)
    street_guard = bool(_looks_like_street_name(title_norm))
    if street_guard:
        # Strip the address portion from the title before evaluating patterns.
        title_norm = re.sub(
            r"\b(?:calle|avenida|plaza|paseo|carrer|carretera|via)\b[^.\n]{0,80}\bbarco\b",
            " ",
            title_norm,
            flags=re.I,
        )

    # The title takes precedence over other fields.
    haystack = title_norm
    if haystack:
        primary = re.sub(
            r"^(?:(?:subasta(?: publica| concursal)?|venta|lote\s*\d*)\s*(?:de\s+)?[:\-]?\s*)+",
            "",
            haystack,
        )
        for pattern, code in (
            (_GARAGES_RE, "garages"),
            (_COMMERCIAL_RE, "commercial"),
            (_LAND_RE, "land"),
            (_HOUSING_RE, "housing"),
            (_TRUCKS_RE, "trucks"),
        ):
            if pattern.match(primary):
                if code == "trucks" and _MACHINERY_RE.match(primary):
                    return "machinery"
                return code
        match = _match_first(haystack, _DETECTION_RULES)
        if match:
            return match[0]

    # If the title is empty or has no match, try the body.
    if not haystack:
        body = _norm(" ".join([title, text, url]))
        match = _match_first(body, _DETECTION_RULES)
        if match:
            return match[0]
        return "unknown"

    # If the title is nonempty and unmatched, consult the body only for
    # new categories (boats/machinery/electronics/furniture/collectibles)
    # that were not represented in the classic heuristic.
    body_norm = _norm(text)
    body_match = _match_first(body_norm, _DETECTION_RULES[:5])
    if body_match and body_match[0] in (
        "boats",
        "machinery",
        "electronics",
        "furniture",
        "collectibles",
    ):
        return body_match[0]

    return "unknown"


def normalize_category(item):
    """Return a copy with consistent category and asset_type.

    Unknown means insufficient detail; other means confirmed but unclassified
    detail. Ingestion uses category for the UI code and asset_type for its family."""
    if not isinstance(item, dict):
        raise TypeError("normalize_category espera un dict")
    out = dict(item)
    category = infer_category(out)
    if category == "unknown":
        explicit = out.get("category")
        if explicit in _CATEGORY_DEFS and explicit != "unknown":
            category = explicit
        else:
            family = out.get("asset_type")
            if family in ASSET_TYPES and family != "unknown":
                category = "vehicles_unspec" if family == "vehicles" else family
            elif (
                out.get("record_type") == "asset"
                and out.get("facts_scope") == "current_listing_entity"
            ):
                category = "other"
    out["category"] = category

    # Map category -> family (via CATEGORY_CHOICES).
    family = None
    for entry in CATEGORY_CHOICES:
        if entry["id"] == category:
            family = entry["asset_type"]
            break
    # Defensive fallback: if category is not in choices, force unknown.
    if family is None:
        family = "unknown"
    out["asset_type"] = family
    return out


def is_broad_mode(context):
    """True si el contexto activa el modo de reconocimiento amplio."""
    if not isinstance(context, dict):
        return False
    return bool(context.get("all_assets"))


def asset_type_for_family(category_or_family):
    """Return the root asset family for a category or family identifier."""
    if category_or_family in ASSET_TYPES:
        return category_or_family
    for entry in CATEGORY_CHOICES:
        if entry["id"] == category_or_family:
            return entry["asset_type"]
    return "unknown"
