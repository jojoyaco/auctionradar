"""Public HTTP discovery and evidence-preserving extraction, without browser sessions.

All prices and facts are seller publications, never due-diligence conclusions.
Source-specific routing is deliberately small; unsupported JavaScript catalogues
are reported as partial discovery instead of an empty inventory.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import subprocess
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

MAX_BODY = 8 * 1024 * 1024
USER_AGENT = "AuctionRadar/1.0 (public information research; no bidding)"
PRICE_KEYS = (
    "asking",
    "reserve",
    "auction_value",
    "current_bid",
    "minimum_bid",
    "award",
    "deposit",
)
SENSITIVE = re.compile(
    r"password|passwd|secret|authorization|token|cookie|session.?id|csrf|api.?key|gstaticmapimage|email|nombreGestor|telefono|asesorcomercial|agente$",
    re.I,
)


def normalized(value):
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)
    ).lower()


def public_url(url):
    """Strip fragments and credential-like query parameters from public evidence."""
    try:
        p = urlsplit(html.unescape(url).strip().replace("\\", "/"))
        if p.scheme not in ("http", "https") or not p.hostname or p.username or p.password:
            return None
        query = urlencode(
            [
                (k, v)
                for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not SENSITIVE.search(k)
            ]
        )
        return urlunsplit((p.scheme, p.netloc, p.path, query, ""))
    except ValueError:
        return None


def _safe_data(value):
    if isinstance(value, dict):
        return {str(k): _safe_data(v) for k, v in value.items() if not SENSITIVE.search(str(k))}
    if isinstance(value, list):
        return [_safe_data(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", value)
        value = re.sub(r"AIza[\w-]{20,}", "[REDACTED_API_KEY]", value)
    return value


def fetch(url, context):
    """Fetch public HTTP with a wall-clock deadline; never reads curl user config."""
    started = time.monotonic()
    result = {
        "url": public_url(url),
        "final_url": None,
        "http_status": None,
        "content_type": None,
        "body": "",
        "body_bytes": b"",
        "error": None,
        "raw_path": None,
        "sha256": None,
        "max_body_bytes": MAX_BODY,
        "truncated": False,
    }
    if not result["url"]:
        result["error"] = "URL no pública o protocolo no permitido"
        result["elapsed_seconds"] = 0
        return result
    private_transport = (
        os.environ.get("AUCTIONRADAR_WORKSPACE_MODE", "").lower() == "private"
        or context.get("public_network_only") is True
    )
    if private_transport:
        # Validate the original URL, not public_url's redacted/normalised form:
        # otherwise credential-like parameters could be silently accepted.
        from auctionradar.network import PublicNetworkError, validate_url

        try:
            result["url"] = validate_url(url)
        except PublicNetworkError:
            result.update(error="invalid_public_source_url", elapsed_seconds=0)
            return result
    timeout = max(0.1, float(context.get("timeout", 12)))
    payload = context.get("request_json")
    request_body = None
    if payload is not None:
        if not isinstance(payload, dict):
            result.update(error="La consulta JSON debe ser un objeto", elapsed_seconds=0)
            return result
        request_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(request_body) > 4096:
            result.update(error="Consulta JSON demasiado grande", elapsed_seconds=0)
            return result
        result["request_method"] = "POST"
        result["request_json"] = _safe_data(payload)
    with tempfile.TemporaryDirectory(prefix="cheap-search-") as directory:
        target = Path(directory) / "body"
        command = [
            "curl",
            "-q",
            "--silent",
            "--show-error",
            "--location",
            "--max-redirs",
            "4",
            "--proto",
            "=http,https",
            "--proto-redir",
            "=http,https",
            "--globoff",
            "--connect-timeout",
            str(min(timeout, 6)),
            "--max-time",
            str(timeout),
            "--max-filesize",
            str(MAX_BODY),
            "--compressed",
            "--user-agent",
            USER_AGENT,
            "--output",
            str(target),
            "--write-out",
            "%{http_code}\n%{content_type}\n%{url_effective}",
            "--url",
            result["url"],
        ]
        if request_body is not None:
            command.extend(["--header", "Content-Type: application/json", "--data-binary", "@-"])
        try:
            if private_transport:
                from auctionradar.network import fetch_public

                response = fetch_public(
                    result["url"],
                    timeout=timeout,
                    max_body=MAX_BODY,
                    user_agent=USER_AGENT,
                    request_body=request_body,
                )
                result.update(response)
                if response["error"]:
                    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
                    return result
                target.write_bytes(response["body_bytes"])
                # Keep extraction/redaction/evidence behaviour common to both transports.
                proc = subprocess.CompletedProcess(
                    command,
                    0,
                    f"{response['http_status'] or 0}\n{response['content_type'] or ''}\n{response['final_url']}".encode(),
                    b"",
                )
            else:
                proc = subprocess.run(
                    command, input=request_body, capture_output=True, timeout=timeout + 2
                )
            metadata = proc.stdout.decode("utf-8", "replace").split("\n")
            if metadata and metadata[0].isdigit():
                result["http_status"] = int(metadata[0]) or None
            result["content_type"] = metadata[1] if len(metadata) > 1 else None
            result["final_url"] = public_url(metadata[2]) if len(metadata) > 2 else result["url"]
            if proc.returncode:
                result["error"] = (
                    proc.stderr.decode("utf-8", "replace")[:500].strip()
                    or f"curl exit {proc.returncode}"
                )
            data = target.read_bytes()[:MAX_BODY] if target.exists() else b""
            result["body_bytes"] = data
            result["truncated"] = proc.returncode == 63 or len(data) >= MAX_BODY
            charset = re.search(r"charset=([\w-]+)", result["content_type"] or "", re.I)
            try:
                result["body"] = data.decode(charset.group(1) if charset else "utf-8", "replace")
            except LookupError:
                result["body"] = data.decode("utf-8", "replace")
            result["sha256"] = hashlib.sha256(data).hexdigest() if data else None
            if data and context.get("raw_dir"):
                raw_dir = Path(context["raw_dir"])
                raw_dir.mkdir(parents=True, exist_ok=True)
                suffix = (
                    ".pdf"
                    if data.startswith(b"%PDF")
                    else ".json"
                    if request_body is not None
                    else ".html"
                )
                identity = result["url"].encode() + (
                    b"\nPOST\n" + request_body if request_body is not None else b""
                )
                filename = hashlib.sha256(identity).hexdigest()[:20] + suffix
                destination = raw_dir / filename
                if suffix == ".pdf":
                    destination.write_bytes(data)
                elif suffix == ".json":
                    try:
                        visible = _safe_data(json.loads(result["body"]))
                        keys = context.get("json_response_keys")
                        # A dynamic list allows fields returned by the portal but explicitly
                        # marked as unpublished to be omitted.
                        if callable(keys):
                            keys = keys(visible)
                        if isinstance(keys, list):
                            visible = (
                                {key: visible[key] for key in keys if key in visible}
                                if isinstance(visible, dict)
                                else {"unrecognized_response_shape": True}
                            )
                        destination.write_text(
                            json.dumps(visible, ensure_ascii=False), encoding="utf-8"
                        )
                    except ValueError:
                        destination.write_text("[invalid public JSON response]", encoding="utf-8")
                else:
                    # No response headers/cookies. Redact token-like inline fields.
                    redacted = re.sub(
                        r'(?i)(["\'](?:access_token|refresh_token|password|csrf_token|session_id)["\']\s*:\s*)["\'][^"\']*["\']',
                        r'\1"[REDACTED]"',
                        result["body"],
                    )
                    redacted = re.sub(
                        r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", redacted
                    )
                    redacted = re.sub(r"AIza[\w-]{20,}", "[REDACTED_API_KEY]", redacted)
                    destination.write_text(redacted, encoding="utf-8")
                result["raw_path"] = str(destination)
        except (subprocess.TimeoutExpired, OSError) as error:
            result["error"] = f"{type(error).__name__}: {error}"[:500]
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text_parts, self.links, self.images, self.scripts, self.meta = [], [], [], [], {}
        self.script = None
        self.skip = 0
        self.title_parts = []
        self.in_title = False
        self.current_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "script":
            self.script = {"id": attrs.get("id"), "type": attrs.get("type"), "text": ""}
        elif tag in ("style", "noscript"):
            self.skip += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "a":
            self.current_link = {
                "url": attrs.get("href", ""),
                "text": "",
                "rel": attrs.get("rel", ""),
            }
            self.links.append(self.current_link)
        elif tag == "img":
            self.images.append(
                {
                    "url": attrs.get("data-src")
                    or attrs.get("data-original")
                    or attrs.get("src", ""),
                    "caption": attrs.get("alt") or attrs.get("title"),
                }
            )
        elif tag == "meta":
            self.meta[attrs.get("property") or attrs.get("name") or ""] = attrs.get("content")
        if tag in ("p", "div", "br", "li", "tr", "dt", "dd", "h1", "h2", "td", "th"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "script" and self.script is not None:
            self.scripts.append(self.script)
            self.script = None
        elif tag in ("style", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self.in_title = False
        elif tag == "a":
            self.current_link = None

    def handle_data(self, data):
        if self.script is not None:
            self.script["text"] += data
        elif not self.skip:
            self.text_parts.append(data)
            if self.in_title:
                self.title_parts.append(data)
            if self.current_link is not None:
                self.current_link["text"] += data

    @property
    def text(self):
        return "\n".join(
            " ".join(line.split()) for line in "".join(self.text_parts).splitlines() if line.strip()
        )


def parse_page(body):
    parser = Page()
    parser.feed(body)
    return parser


def embedded_json(page):
    found = []
    for script in page.scripts:
        if not script["text"].strip():
            continue
        if "json" in (script["type"] or "") or script["id"] in (
            "serverApp-state",
            "__NEXT_DATA__",
            "__NUXT_DATA__",
        ):
            try:
                found.append(
                    {
                        "id": script["id"],
                        "type": script["type"],
                        "data": _safe_data(json.loads(html.unescape(script["text"]))),
                    }
                )
            except (ValueError, RecursionError):
                pass
    return found


def walk(value, path=""):
    if isinstance(value, dict):
        for key, val in value.items():
            key_path = f"{path}.{key}" if path else str(key)
            yield key_path, key, val
            yield from walk(val, key_path)
    elif isinstance(value, list):
        for index, val in enumerate(value):
            yield from walk(val, f"{path}[{index}]")


def money(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = re.sub(r"[^\d,.-]", "", str(value))
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = (
            raw.replace(".", "").replace(",", ".")
            if raw.rfind(",") > raw.rfind(".")
            else raw.replace(",", "")
        )
    elif "," in raw:
        raw = raw.replace(",", ".") if len(raw.rsplit(",", 1)[1]) <= 2 else raw.replace(",", "")
    elif "." in raw and len(raw.rsplit(".", 1)[1]) == 3:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


_AMOUNT = r"([\d][\d.,\s\u00a0]*)(?:\s*(?:€|EUR|euros))?"


def labeled_amount(text, labels, require_currency=False):
    amount_pattern = r"([\d][\d.,\s\u00a0]*)\s*(?:€|EUR\b|euros\b)" if require_currency else _AMOUNT
    for label in labels:
        match = re.search(r"(?i)(?:" + label + r")\s*[:\-]?\s*" + amount_pattern, text)
        if match:
            number = money(match.group(1))
            if number is not None:
                return number, match.group(0).strip()
    return None, None


def _asset_type(source, text, context=None):
    """Title- and HTML-body-based heuristic.

    Priority order:
      1. Title precedence: if the title starts with a specific pattern
         (commercial/land/vehicles/boats/machinery/electronics/furniture/
         collectibles), it takes precedence over the body and navigation.
      2. Body: use body signals when the title is generic.
      3. Fallback: use the first asset_type declared by the source, or 'unknown'.
    """
    title_text = text.split("\n", 1)[0]
    types = source.get("asset_types", [])
    # New patterns shared with the parent taxonomy (auctionradar/assets.py).
    # Apply them to the title to ensure it takes precedence over navigation.
    try:
        from auctionradar.assets import _DETECTION_RULES as _BROAD_RULES
    except ImportError:
        _BROAD_RULES = []
    # Title: new specific categories first, followed by the classic ones.
    title_norm = normalized(title_text)
    # Guard: 'Calle del Barco' is NOT a boat.
    street_name_barco = re.search(
        r"\b(?:calle|avenida|plaza|paseo|carrer)\b[^.\n]{0,40}\bbarco\b",
        title_norm,
    )
    if not street_name_barco:
        for pattern, (category, family) in _BROAD_RULES:
            if family == "vehicles":
                # The classic patterns handle vehicle subcategories.
                continue
            if family in ("commercial", "land"):
                # Already covered by the specific classic patterns.
                continue
            if pattern.search(title_norm):
                return family
    if re.search(
        r"^(?:subasta concursal |subasta publica )?(?:venta de |subasta de |lote \d+ [:-] )?(?:plaza(?:s)? de (?:garaje|aparcamiento)|garajes?|local(?:es)? comercial(?:es)?|nave(?:s)? industrial(?:es)?|oficina(?:s)?)",
        title_norm,
    ):
        return "commercial"
    if re.search(
        r"^(?:subasta concursal |subasta publica )?(?:venta de |subasta de |lote \d+ [:-] )?(?:solar|suelo|terreno|parcela)",
        title_norm,
    ):
        return "land"
    if types == ["vehicles"] or re.search(
        r"furgon|berlingo|transit|ducato|sprinter|vehiculo|caravan", title_norm
    ):
        return "vehicles"
    if re.search(r"vivienda|apartamento|piso\b|casa\b|residencial", title_norm):
        return "housing"
    housing = bool(
        re.search(r"vivienda|apartamento|piso\b|casa\b|residencial|habitacion", normalized(text))
    )
    vehicle = bool(
        re.search(
            r"furgon|berlingo|transit|ducato|sprinter|vehiculo|kilomet|\bkm\b|caravan|master\b",
            normalized(text),
        )
    )
    if vehicle and not housing:
        return "vehicles"
    if housing:
        return "housing"
    # Body: only for new categories (boats/machinery/electronics/...).
    # Do not override housing/vehicles, to preserve the classic heuristic.
    body_norm = normalized(text)
    if not street_name_barco:
        for pattern, (category, family) in _BROAD_RULES[:5]:
            if family in ("boats", "machinery", "electronics", "furniture", "collectibles"):
                if pattern.search(body_norm):
                    return family
    types = source.get("asset_types", [])
    return types[0] if len(types) == 1 else "unknown"


def extract_item(body, url, source, observed_at):
    page = parse_page(body)
    text = page.text
    records = embedded_json(page)
    # Angular transfer state contains navigation, translations and recommendations.
    # Normalize only the entity bound to this exact URL; retain public raw evidence.
    focused_entity = None
    if source["id"] == "aliseda":
        reference_from_url = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1].lower()
        for record in records:
            if record["id"] == "serverApp-state" and isinstance(record["data"], dict):
                matching = {
                    k: v
                    for k, v in record["data"].items()
                    if "/get-property/" + reference_from_url in k.lower()
                }
                if matching:
                    focused_entity = next(iter(matching.values()))
                    records = [dict(record, data=matching)]
                    break
    if source["id"] == "solvia":
        for record in records:
            if record["id"] == "ng-state" and isinstance(record["data"], dict):
                entity = record["data"].get("propertyBasicDetail")
                if (
                    isinstance(entity, dict)
                    and str(entity.get("idVivienda", str(entity.get("id", "")).split("-")[0]))
                    in url
                ):
                    focused_entity = entity
                    records = [dict(record, data={"propertyBasicDetail": entity})]
                    break
    if focused_entity is None:
        product_entities = []
        for record in records:
            stack = [record["data"]]
            while stack:
                node = stack.pop()
                if isinstance(node, list):
                    stack.extend(node)
                elif isinstance(node, dict):
                    kind = node.get("@type", [])
                    kind = [kind] if isinstance(kind, str) else kind
                    if any(
                        t
                        in (
                            "Product",
                            "Car",
                            "Vehicle",
                            "SingleFamilyResidence",
                            "House",
                            "Apartment",
                            "Residence",
                        )
                        for t in kind
                    ):
                        entity_url = node.get("url") or node.get("@id", "").split("#")[0]
                        if not entity_url or public_url(entity_url) == public_url(url):
                            product_entities.append(node)
                    elif "@graph" in node:
                        stack.append(node["@graph"])
        if len(product_entities) > 1:
            matching_entities = [
                e
                for e in product_entities
                if str(e.get("sku", "")).lower() and str(e["sku"]).lower() in url.lower()
            ]
            if len(matching_entities) == 1:
                product_entities = matching_entities
        if len(product_entities) == 1:
            focused_entity = product_entities[0]
            records = [
                {
                    "id": "current_jsonld_entity",
                    "type": "application/ld+json",
                    "data": focused_entity,
                }
            ]
    flat = [entry for record in records for entry in walk(record["data"])]
    title = (
        page.meta.get("og:title")
        or "".join(page.title_parts).strip()
        or urlsplit(url).path.rsplit("/", 1)[-1]
    )
    if source["id"] == "actcon":
        for heading in re.findall(r"<h[12]\b[^>]*>(.*?)</h[12]>", body, re.S | re.I):
            heading_text = parse_page(heading).text.strip()
            if len(heading_text) > 12 and not re.search(
                r"(?i)cookies|esta p[aá]gina web", heading_text
            ):
                title = heading_text
                break
    facts, evidence, prices = [], [], dict.fromkeys(PRICE_KEYS)
    reference = (
        str(focused_entity.get("id") or focused_entity.get("sku") or "").upper()
        if focused_entity
        else None
    )
    reference = reference or None
    if focused_entity and focused_entity.get("name"):
        title = str(focused_entity["name"])
    for path, key, value in flat:
        k = normalized(key)
        if not isinstance(value, (dict, list)) and value is not None:
            # Preserve path and publication rather than forcing arbitrary JSON into facts.
            facts.append({"field": path, "value": value, "basis": "seller_embedded_json"})
        if (
            not reference
            and k
            in ("sku", "reference", "referencia", "referenciaactivo", "codigoinmueble", "idactivo")
            and value
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{2,}", str(value))
        ):
            reference = str(value)
        if (
            k == "name"
            and ".offers" not in path
            and len(str(value)) < 250
            and not "".join(page.title_parts).strip()
        ):
            title = str(value)
        if k in ("price", "precio", "precioventa", "pvp") and isinstance(value, (str, int, float)):
            # Only price of a product/real-estate entity, not generic min/max facets.
            if (
                (
                    ".offers." in path.lower()
                    or path.lower().startswith("offers.")
                    or source["id"] in ("aliseda", "servihabitat", "solvia")
                )
                and money(value)
                and prices["asking"] is None
            ):
                prices["asking"] = money(value)
                evidence.append({"field": "prices.asking", "path": path, "value": value})
    labels = {
        "asking": [r"precio\s+de\s+venta", r"precio\s+actual", r"precio\s+del\s+inmueble"],
        "reserve": [r"precio\s+de\s+reserva", r"precio\s+reserva", r"valor\s+de\s+reserva"],
        "auction_value": [
            r"valor\s+de\s+tasaci[oó]n",
            r"valor\s+subasta",
            r"valor\s+de\s+subasta",
            r"tasaci[oó]n",
        ],
        "current_bid": [r"puja\s+actual", r"puja\s+m[aá]s\s+alta", r"oferta\s+actual"],
        "minimum_bid": [r"puja\s+m[ií]nima", r"pr[oó]xima\s+puja", r"precio\s+de\s+salida"],
        "award": [r"precio\s+de\s+adjudicaci[oó]n"],
        "deposit": [r"importe\s+del\s+dep[oó]sito", r"dep[oó]sito", r"fianza"],
    }
    for field, patterns in labels.items():
        auction_source = "auction" in source.get("kind", "") or source["id"] in (
            "boe",
            "aeat",
            "gobid",
            "escrapalia",
            "pnsd_vehicles",
            "actcon",
            "eactivos",
            "iag",
            "procuradores",
        )
        atype = _asset_type(source, title + "\n" + text[:3500])
        # A deposit requires a currency when this is NOT an auction and the category is
        # machinery/vehicles/boats (it could be "depósito 25 L").
        require_currency = (
            field == "deposit"
            and not auction_source
            and atype in ("vehicles", "boats", "machinery")
        )
        amount, quote = labeled_amount(text, patterns, require_currency=require_currency)
        if amount is not None:
            prices[field] = amount
            evidence.append({"field": f"prices.{field}", "quote": quote})
    # A BOE auction valuation is not the price offered to a purchaser.
    if source["id"] in ("boe", "aeat", "gobid", "escrapalia", "pnsd_vehicles"):
        prices["asking"] = None
    possession = {"status": "unknown", "documented_delivery_free": False, "evidence": []}
    possession_words = []
    for path, key, value in flat:
        if re.search(r"posesion|ocupacion|situacionposesoria", normalized(key)) and isinstance(
            value, (str, int)
        ):
            possession_words.append(str(value))
            possession["evidence"].append({"path": path, "value": value})
    possession_match = re.search(
        r"(?i)(?:situaci[oó]n\s+posesoria\s*[:\-]?\s*|(?:posesi[oó]n|ocupaci[oó]n)\s*[:\-]\s*)([^\n]{0,160})",
        text,
    )
    if possession_match:
        possession_words.append(possession_match.group(1))
        possession["evidence"].append({"quote": possession_match.group(0)})
    possess_text = normalized(" ".join(possession_words))
    if re.search(r"no consta|desconocid|sin informacion|no disponible", possess_text):
        possession["status"] = "unknown"
    elif re.search(r"libre|desocupad|vacant", possess_text):
        possession["status"] = "seller_claim_free"
    elif re.search(r"ocupad|arrendad|inquilino", possess_text):
        possession["status"] = "occupied_or_tenanted"
    risk_flags = []
    if re.search(
        r"(?i)(?:inmueble|vivienda|casa|activo)\s+(?:se\s+encuentra\s+)?ocupad|ocupad[oa]\s+(?:por|sin|ilegal)|renta\s+antigua",
        text,
    ):
        possession["status"] = "occupied_or_tenanted"
    no_possession = re.search(
        r"(?i)(?:venta\s+de\s+)?inmueble\s+sin\s+posesi[oó]n(?:\s*\(ocupad[oa]\))?|sin\s+posesi[oó]n\s*\(ocupad[oa]\)",
        text,
    )
    if no_possession:
        possession["status"] = "occupied_or_tenanted"
        possession["evidence"].append(
            {
                "quote": no_possession.group(0),
                "source_url": url,
                "basis": "explicit_seller_disclosure",
            }
        )
    for flag, pattern in [
        ("partial_ownership", r"proindiviso|nuda propiedad|cuota indivisa"),
        ("debt_assignment", r"cesion de credito|venta de deuda"),
        ("ruin", r"estado de ruina|declarad. en ruina"),
    ]:
        if re.search(pattern, normalized(text)):
            risk_flags.append(flag)
    status = "unknown"
    status_evidence = []
    for path, key, value in flat:
        if normalized(key) == "availability" and isinstance(value, str):
            status_evidence.append({"path": path, "value": value})
            if re.search(r"soldout|outofstock|discontinued", value, re.I):
                status = "unavailable"
            elif re.search(r"instock|limitedavailability", value, re.I):
                status = "seller_claim_available"
    if focused_entity and focused_entity.get("Activo") == 1:
        status = "seller_claim_available"
        status_evidence.append({"path": "Activo", "value": 1})
    if re.search(
        r"(?i)(?:inmueble|veh[ií]culo|lote|producto)\s+(?:ya\s+)?(?:vendid[oa]|adjudicad[oa]|no disponible)|este anuncio ya no est[aá] disponible",
        text,
    ):
        status = "unavailable"
        status_evidence.append({"quote": "Estado de no disponibilidad publicado en ficha"})
    visits_negative = bool(
        re.search(r"(?i)no\s+(?:es\s+)?visitable|no\s+se\s+(?:puede|podr[aá])\s+visitar", text)
    )
    visits_positive = bool(
        re.search(r"(?i)(?<!no )(?<!NO )\bvisitable\b|visitas\s+permitidas", text)
    )
    # Remove negated occurrences before testing affirmative ones.
    positive_text = re.sub(r"(?i)no\s+(?:es\s+)?visitable", "", text)
    visits_positive = bool(re.search(r"(?i)\bvisitable\b|visitas\s+permitidas", positive_text))
    if re.search(r"(?i)visitable\s*[:\-]?\s*no consta", text):
        visits_positive = False
    visits_status = (
        "conflict"
        if visits_negative and visits_positive
        else "not_visitable"
        if visits_negative
        else "seller_claim_visitable"
        if visits_positive
        else "unknown"
    )
    photos, documents, tours = [], [], []
    candidates = [] if focused_entity else list(page.images)
    if page.meta.get("og:image") and not focused_entity:
        candidates.append({"url": page.meta["og:image"], "caption": None})
    for path, key, value in flat:
        if isinstance(value, str) and re.search(r"^https?://|^/", value):
            if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", value, re.I):
                candidates.append({"url": value, "caption": None})
            elif re.search(r"\.pdf(?:\?|$)", value, re.I):
                documents.append(
                    {
                        "url": public_url(urljoin(url, value)),
                        "title": key,
                        "read_status": "not_read",
                    }
                )
            elif re.search(r"floorfy|youtube|youtu\.be|matterport", value, re.I):
                tours.append(public_url(urljoin(url, value)))
        elif normalized(key) == "image" and isinstance(value, list):
            candidates.extend({"url": v, "caption": None} for v in value if isinstance(v, str))
    # Gallery links often point to originals while img src is only a thumbnail.
    for link in page.links:
        if focused_entity and re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", link["url"], re.I):
            continue
        target = public_url(urljoin(url, link["url"]))
        if not target:
            continue
        if re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", target, re.I):
            candidates.append({"url": target, "caption": link["text"] or None})
        elif re.search(r"\.pdf(?:\?|$)|verdocumento|documento\.php|download", target, re.I):
            documents.append(
                {"url": target, "title": link["text"].strip() or None, "read_status": "not_read"}
            )
        elif re.search(r"floorfy|youtube|youtu\.be|matterport", target, re.I):
            tours.append(target)
    if focused_entity:
        gallery = focused_entity.get("imagenes") or focused_entity.get("listaImagenesInmueble")
        if isinstance(gallery, list):
            candidates = [
                {
                    "url": g.get("Uri") or g.get("url", ""),
                    "caption": g.get("Orden") or g.get("categoria"),
                }
                for g in gallery
                if isinstance(g, dict)
            ]
        elif focused_entity.get("image"):
            images = focused_entity["image"]
            images = images if isinstance(images, list) else [images]
            candidates = [
                {
                    "url": (g.get("url") or g.get("@id", "")) if isinstance(g, dict) else g,
                    "caption": None,
                }
                for g in images
            ]
    page_media = []
    if not focused_entity:
        page_media = [
            {
                "url": public_url(urljoin(url, c["url"])),
                "caption": c.get("caption"),
                "attribution": "page_media_unverified_asset_attribution",
            }
            for c in candidates
            if c.get("url")
        ]
        candidates = []
    for candidate in candidates:
        target = public_url(urljoin(url, candidate["url"]))
        if source["id"] in ("boe", "aeat") and "/imagenes/" in (target or ""):
            continue
        if not target or re.search(
            r"logo|icon|spinner|banner|placeholder|avatar|pixel|facebook|instagram", target, re.I
        ):
            continue
        if target in {p["url"] for p in photos}:
            continue
        photos.append(
            {
                "url": target,
                "source_url": url,
                "caption": candidate["caption"],
                "attribution": "current_entity_gallery"
                if focused_entity
                else "page_media_unverified_asset_attribution",
                "kind": "floorplan"
                if re.search(r"plano|floorplan|planimetr", target, re.I)
                else "unclassified",
                "observed_at": observed_at,
                "verification": "published_url_only",
                "visual_content_verified": False,
            }
        )
    documents = list({d["url"]: dict(d, source_url=url) for d in documents if d["url"]}.values())
    premium_match = re.search(
        r"(?i)(?:comisi[oó]n|buyer.?s? premium|premium|honorarios)\s*[:\-]?\s*([\d.,]+)\s*%", text
    )
    premium_context = "\n".join(
        match.group(0)
        for match in re.finditer(
            r"(?i)(?:comisi[oó]n|buyer.?s? premium|premium|honorarios)[^\n.]{0,180}(?:\.[0-9]{3}[^\n.]{0,80})?",
            text,
        )
    )
    minimum, minimum_quote = labeled_amount(premium_context, [r"m[ií]nimo(?:\s+de)?"])
    premium = {
        "rate_percent": money(premium_match.group(1)) if premium_match else None,
        "minimum_eur": minimum,
        "vat_included": False
        if re.search(r"(?i)m[aá]s\s+IVA|\+\s*IVA|IVA\s+no\s+incluido", text)
        else None,
        "evidence": premium_match.group(0) if premium_match else minimum_quote,
    }
    id_match = re.search(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]+|ant\d{7,}|\b604\d{5}\b", url, re.I)
    if not reference and id_match:
        reference = id_match.group(0).upper()
    lot_reference = (
        dict(parse_qsl(urlsplit(url).query)).get("idLote")
        if source["id"] in ("boe", "aeat")
        else None
    )
    identity = (reference or url) + (":lot=" + lot_reference if lot_reference else "")
    normalized_fields = {
        key: None
        for key in (
            "built_area_m2",
            "usable_area_m2",
            "bedrooms",
            "bathrooms",
            "construction_year",
            "floor",
            "elevator",
        )
    }
    description = None
    if focused_entity:
        entity = focused_entity
        description = (
            entity.get("Description") or entity.get("description") or entity.get("textoDescripcion")
        )
        for target, aliases in {
            "built_area_m2": ("ConstructedArea", "m2"),
            "usable_area_m2": ("UsableArea",),
            "bedrooms": ("Bedrooms", "totalDormitorios", "numberOfBedrooms"),
            "bathrooms": ("Bathrooms", "totalBanyos", "numberOfBathroomsTotal"),
            "construction_year": ("yearBuilt",),
        }.items():
            for alias in aliases:
                value = entity.get(alias)
                if value and not isinstance(value, (dict, list)):
                    normalized_fields[target] = money(value)
                    break
        if isinstance(entity.get("floorSize"), dict):
            normalized_fields["built_area_m2"] = money(entity["floorSize"].get("value"))
        address = entity.get("address", {})
        if isinstance(address, dict):
            normalized_fields["location"] = {
                "address": address.get("streetAddress")
                or " ".join(
                    str(address.get(k, "")) for k in ("TipoVia", "StreetName", "StreetNumber")
                ).strip()
                or None,
                "city": address.get("addressLocality") or address.get("Ciudad"),
                "province": address.get("addressRegion")
                or (
                    address.get("provincia", {}).get("Nombre")
                    if isinstance(address.get("provincia"), dict)
                    else None
                ),
                "postal_code": address.get("postalCode") or address.get("PostalCode"),
                "latitude": address.get("Latitude"),
                "longitude": address.get("Longitude"),
            }
        if source["id"] == "solvia":
            normalized_fields["location"] = {
                "address": entity.get("direccion"),
                "city": entity.get("poblacion", {}).get("name"),
                "province": entity.get("provincia", {}).get("name"),
                "postal_code": entity.get("cp"),
            }
        if description:
            for target, pattern in [
                ("bedrooms", r"(\d+)\s+(?:habitaciones|dormitorios)"),
                ("bathrooms", r"(\d+)\s+baños"),
            ]:
                match = re.search(pattern, description, re.I)
                if match and not normalized_fields[target]:
                    normalized_fields[target] = int(match.group(1))
    ownership_percentage = None
    right_sold = None
    fraction = re.search(r"(?i)(\d+(?:[.,]\d+)?)\s*%\s*(?:del?\s+)?pleno\s+dominio", title)
    if fraction and 0 < float(fraction.group(1).replace(",", ".")) < 100:
        ownership_percentage = float(fraction.group(1).replace(",", "."))
        right_sold = "undivided_share"
        if "partial_ownership" not in risk_flags:
            risk_flags.append("partial_ownership")
        evidence.append(
            {
                "field": "full_ownership_percentage",
                "quote": fraction.group(0),
                "value": ownership_percentage,
            }
        )
    item = {
        "id": f"{source['id']}-{hashlib.sha256(identity.encode()).hexdigest()[:16]}",
        "source_id": source["id"],
        "url": url,
        "title": title,
        "reference": reference,
        "lot_reference": lot_reference,
        "record_type": "asset",
        "observed_at": observed_at,
        "full_ownership_percentage": ownership_percentage,
        "right_sold": right_sold,
        "asset_type": _asset_type(source, title + "\n" + text[:3500]),
        "prices": prices,
        "currency": "EUR",
        "possession": possession,
        "availability": {
            "status": status,
            "evidence": status_evidence,
            "checked_at": observed_at,
            "verification": "primary_page_retrieved",
        },
        "visits": {"status": visits_status},
        "risk_flags": risk_flags,
        "fees": {"buyer_premium": premium},
        "photos": photos,
        "photos_total_extracted": len(photos),
        "documents": documents,
        "tours_and_videos": list(dict.fromkeys(tours)),
        "facts": facts,
        "facts_scope": "current_listing_entity" if focused_entity else "unscoped_public_page",
        "normalized_fields": normalized_fields,
        "description": description,
        "page_media": page_media,
        "embedded_data": records,
        "published_text": _safe_data(text),
        "evidence": [
            dict(e, source_url=url, observed_at=observed_at, basis="seller_publication")
            for e in evidence
        ],
        "all_in_cost": None,
        "yield": None,
        "extraction_method": "html_text_and_embedded_json",
    }
    return item


# Patterns identify detail pages, not arbitrary pages containing sale vocabulary.
DETAIL_PATTERNS = {
    "escogecasa": r"/inmueble/[^/?]+/[^/?]+/\d+(?:/?(?:\?|$))",
    "segipsa_pnsd_property": r"/subasta/[^/?]+",
    "boe": r"detalleSubasta\.php\?",
    "aeat": r"detalleSubasta\.php\?",
    "aliseda": r"/inmueble/[^/?]+",
    "servihabitat": r"/venta/[^?]+/\d{6,}|/inmueble/[^?]+|/\d{7,}(?:[/?]|$)",
    "gobid": r"/subastas/[^/?]+/[^/?]+-\d+(?:[/?]|$)|/lote/|/lot/",
    "escrapalia": r"/es/lote/|/esme/lote/|/lot/|/es/lots/",
    "pnsd_vehicles": r"/es/lote/|/esme/lote/|/lot/",
    "actcon": r"/activo/[^/?]+/|/inmueble/|/lote/|/subasta/[^/?]+|/producto/|/activos/[^/?]+",
    "ayvens": r"/car/[^/?]+/[^/?]+/[^/?]+/\d+(?:[/?]|$)|/vehicle/\d+|/vehiculo/\d+",
    "northgate": r"/vehiculos-ocasion/catalogo/[^/?]+/[^/?]+/[^/?]+/[^/?]+|/vehiculo/[^/?]*\d+|/ficha/[^/?]*\d+",
    "arval": r"/coche-segunda-mano/|/coches-segunda-mano/[^/?]+/[^/?]+|/vehiculo/",
    "coches_net": r"/[^/]+-\d+-[^/]+\.aspx",
    "autoscout24": r"/anuncios/|/offers/",
    "mobile_de": r"/fahrzeuge/details\.html\?|/auto-inserat/",
    "truckscout24": r"/tsp/ts-|/furgonetas/[^/]+/[^/]+/\d+",
    "truck1": r"/furgonetas/[^/]+-a\d+\.html",
    "idealista": r"/inmueble/\d+",
    "pisos_com": r"/comprar/[^/]+-\d+",
    "fotocasa": r"/es/comprar/vivienda/[^?]+/\d+/d",
    "indomio": r"/anuncios/\d+",
    "eactivos": r"/[A-Z0-9]{7,}/(?:[?]|$)|/lote/|/subasta/[^/?]+",
    "solvia": r"/propiedades/[^/?]+|/ficha/|/inmueble/",
    "diglo": r"/venta-pisos/|/inmueble/|/ficha/|/activo/",
    "altamira": r"/inmueble/|/venta/[^/?]+/[^/?]+",
}
LISTING_PATTERN = re.compile(
    r"subastas_ava\.php|/subastas?(?:[/?]|$)|/buscador|/comprar-viviendas|/catalog(?:[/?]|$)|/vehiculos-ocasion(?:[/?]|$)|/inmuebles(?:[/?]|$)|/ventas(?:[/?]|$)|/venta-viviendas|/lotes(?:[/?]|$)|/assets(?:[/?]|$)|/auctions?(?:[/?]|$)",
    re.I,
)


def discover_links(body, base_url, source, all_assets=False):
    page = parse_page(body)
    details, listings, documents, pagination = [], [], [], []
    pattern = DETAIL_PATTERNS.get(
        source["id"], r"/inmueble/[^/?]+|/lote/[^/?]+|/vehiculo/[^/?]+|/detalle(?:Subasta)?\.php\?"
    )
    links = list(page.links)
    # SPA JSON-LD often exposes itemListElement URLs without HTML anchors.
    for record in embedded_json(page):
        for path, key, value in walk(record["data"]):
            if normalized(key) in ("url", "href", "link") and isinstance(value, str):
                links.append({"url": value, "text": "", "rel": ""})
    base_host = (urlsplit(base_url).hostname or "").removeprefix("www.")
    for link in links:
        target = public_url(urljoin(base_url, link["url"]))
        if not target:
            continue
        host = (urlsplit(target).hostname or "").removeprefix("www.")
        same_domain = host == base_host or host.endswith("." + base_host)
        if re.search(r"\.pdf(?:\?|$)|verdocumento|documento\.php", target, re.I):
            documents.append(target)
        elif (same_domain or source["id"] == "aeat" and host == "subastas.boe.es") and re.search(
            pattern, target, re.I
        ):
            if (
                source["id"] == "ayvens"
                and not all_assets
                and not re.search(
                    r"transit|berlingo|kangoo|combo|ducato|sprinter|master|boxer|trafic|proace|caddy|vito|transporter|furgon|expert|jumpy|movano|vivaro",
                    target,
                    re.I,
                )
            ):
                # Historical whitelist: vans only. In broad mode (all_assets=True),
                # allow cars, motorcycles, and other asset types through so
                # the downstream classifier can decide from the title content.
                continue
            if source["id"] == "ayvens" and "/contact" in target.lower():
                # A contact page is never an asset listing.
                continue
            if source["id"] in ("boe", "aeat") and "detalleSubasta.php" in target:
                parsed = urlsplit(target)
                keep = [
                    (k, v) for k, v in parse_qsl(parsed.query) if k in ("idSub", "idLote") and v
                ]
                target = urlunsplit(
                    (parsed.scheme, parsed.netloc, parsed.path, urlencode(keep), "")
                )
            details.append(target)
        elif (
            same_domain
            and not re.search(r"financetype=leasing", target, re.I)
            and (
                "next" in link.get("rel", "")
                or re.search(r"(?i)siguiente|next|p[aá]gina\s+\d+", link["text"])
                or re.search(r"[?&](?:page|pagina|pag)=\d+", target)
            )
        ):
            pagination.append(target)
        elif (
            same_domain
            and not re.search(r"financetype=leasing", target, re.I)
            and LISTING_PATTERN.search(target)
            and target.rstrip("/") != base_url.rstrip("/")
        ):
            listings.append(target)
    return {
        key: list(dict.fromkeys(values))
        for key, values in [
            ("details", details),
            ("listings", listings),
            ("documents", documents),
            ("pagination", pagination),
        ]
    }


def _request_metadata(result, role):
    return {k: v for k, v in dict(result, role=role).items() if k not in ("body", "body_bytes")}


def _blocked(result):
    text = normalized(result.get("body", "")[:15000])
    if result.get("http_status") in (401, 403, 429):
        return "authentication_required" if result.get("http_status") == 401 else "blocked"
    if re.search(
        r"just a moment|verify you are human|captcha|access denied|challenge-platform|cloudflare ray id|incapsula incident",
        text,
    ):
        return "blocked"
    return None


# ---------------------------------------------------------------------------
# BOE / AEAT: partition by category in broad mode.
# Map UI categories to (kind, subtype) for the public subastas_ava.php form.
# `subtype=''` means all subtypes within this kind.
# Subtypes verified against the live form page (campo[3]=BIEN.TIPO,
# dato[3] I/V/M, dato[4] subtype code).
# ---------------------------------------------------------------------------
_BOE_CATEGORY_QUERY = {
    "housing": ("I", "501"),
    "commercial": ("I", "502"),
    "garages": ("I", "503"),
    "land": ("I", "506"),
    "vans": ("V", "9102"),
    "cars": ("V", "9101"),
    "motorcycles": ("V", "9103"),
    "trucks": ("V", "9102"),
    "vehicles_unspec": ("V", "9103"),
    "boats": ("M", "14"),
    "machinery": ("M", "5"),
    "furniture": ("M", "7"),
    "collectibles": ("M", "17"),
}

_BOE_KIND_BUCKETS = (("I", ""), ("V", ""), ("M", ""))
_BROAD_PARTITION_LIMIT = 16
_BOE_PARTITION_BASELINE_PAGES = 1
_BOE_PARTITION_BASELINE_DETAILS = 2


def _safe_boe_categories(values):
    """Return a deduplicated list of valid category codes."""
    if not isinstance(values, (list, tuple)):
        values = []
    try:
        from auctionradar.assets import CATEGORY_CHOICES as _CHOICES

        valid = {entry["id"] for entry in _CHOICES}
    except ImportError:
        valid = set(_BOE_CATEGORY_QUERY) | {"other", "unknown"}
    seen = []
    seen_set = set()
    for raw in values:
        code = str(raw).strip()
        if not code or code in seen_set or code not in valid:
            continue
        seen.append(code)
        seen_set.add(code)
    return seen


def _boe_category_groups(categories):
    """Derive ordered, deduplicated (kind, subtype, label) groups.

    Other, unknown or an empty category list covers the I/V/M buckets without a
    subtype. Categories mapped to the same group share one query."""
    cats = _safe_boe_categories(categories)
    fallback = not cats or any(
        c in ("other", "unknown") or c not in _BOE_CATEGORY_QUERY for c in cats
    )
    seen = set()
    groups = []
    for code in cats:
        mapping = _BOE_CATEGORY_QUERY.get(code)
        if mapping is None:
            continue
        key = (mapping[0], mapping[1])
        if key in seen:
            continue
        seen.add(key)
        groups.append((key[0], key[1], code))
    if fallback or not groups:
        for kind, subtype in _BOE_KIND_BUCKETS:
            key = (kind, subtype)
            if key in seen:
                continue
            seen.add(key)
            groups.append((kind, subtype, kind + (subtype and "_" + subtype or "_all")))
    return groups[:_BROAD_PARTITION_LIMIT]


def _boe_partition_query(kind, subtype, aeat_origin):
    """Build a published subastas_ava.php query URL for one category group."""
    form = [
        ("campo[0]", "SUBASTA.ORIGEN"),
        ("dato[0]", aeat_origin),
        ("campo[2]", "SUBASTA.ESTADO.CODIGO"),
        ("dato[2]", "EJ"),
        ("campo[3]", "BIEN.TIPO"),
        ("dato[3]", kind),
    ]
    if subtype:
        form.append(("dato[4]", subtype))
    form.extend(
        [
            ("accion", "Buscar"),
            ("page_hits", "50"),
            ("sort_field[0]", "SUBASTA.FECHA_FIN"),
            ("sort_order[0]", "asc"),
        ]
    )
    return "https://subastas.boe.es/subastas_ava.php?" + urlencode(form)


def _broad_partition_discover(source, context, groups):
    """Run bounded BOE/AEAT discovery separately for each category partition.

    Each partition gets at least one page and two detail requests. Combined limits
    are max(max_pages, n) pages and max(max_details, 2*n) details. Separate cache
    directories prevent collisions. Seeds contain only the partition query URL."""
    max_pages = max(1, int(context.get("max_pages", 2)))
    max_details = max(0, int(context.get("max_details", 6)))
    n = max(1, len(groups))
    per_pages = max(_BOE_PARTITION_BASELINE_PAGES, max_pages // n)
    per_details = max(_BOE_PARTITION_BASELINE_DETAILS, max_details // n)
    combined_pages_cap = max(max_pages, n)
    combined_details_cap = max(max_details, n * 2)
    aeat_origin = "A" if source.get("id") == "aeat" else ""
    base_raw = context.get("raw_dir") or ""

    accumulated = {
        "status": "partial",
        "items": [],
        "requests": [],
        "limitations": [],
        "depth": "none",
        "discovered_detail_urls": [],
        "document_urls": [],
        "pagination": {"visited": [], "unvisited": [], "complete": False},
        "query": {
            "source_url": source.get("url"),
            "asset_type": context.get("asset_type"),
            "regions": context.get("regions", []),
            "server_side_filters_applied": True,
            "broad_partition": True,
            "partition_groups": [{"label": lbl, "kind": k, "subtype": s} for k, s, lbl in groups],
            "per_group_pages": per_pages,
            "per_group_details": per_details,
            "combined_pages_cap": combined_pages_cap,
            "combined_details_cap": combined_details_cap,
            "adapter_groups": len(groups),
        },
        "adapter": "html_embedded_json_v1",
        "inventory_complete": False,
    }

    seen_detail_keys = set()
    seen_ids = set()
    any_successful = False
    error_states = []

    for kind, subtype, label in groups:
        partition_url = _boe_partition_query(kind, subtype, aeat_origin)
        partition_raw_dir = (Path(base_raw) / label) if base_raw else ""
        partition_context = dict(context)
        partition_context["max_pages"] = per_pages
        partition_context["max_details"] = per_details
        partition_context["raw_dir"] = str(partition_raw_dir)
        partition_context["seeds"] = [partition_url]
        partition_context["broad_partition"] = True
        partition_context["partition_label"] = label
        partition_context["partition_kind"] = kind
        partition_context["partition_subtype"] = subtype
        try:
            part_result = discover(source, partition_context)
        except Exception as error:
            accumulated["limitations"].append(
                {
                    "reason": f"broad_partition_failed_{label}",
                    "error": f"{type(error).__name__}: {error}"[:300],
                }
            )
            error_states.append(str(error))
            continue
        # Combine reports while preserving maximum depth and coverage by group.
        accumulated["requests"].extend(part_result.get("requests", []))
        accumulated["limitations"].extend(part_result.get("limitations", []))
        accumulated["pagination"]["visited"].extend(
            part_result.get("pagination", {}).get("visited", [])
        )
        accumulated["pagination"]["unvisited"].extend(
            part_result.get("pagination", {}).get("unvisited", [])
        )
        accumulated["discovered_detail_urls"].extend(part_result.get("discovered_detail_urls", []))
        accumulated["document_urls"].extend(part_result.get("document_urls", []))
        # Profundidad: la mayor entre particiones (access_only < listing < listing_and_details).
        depth_order = {"none": 0, "access_only": 1, "listing": 2, "listing_and_details": 3}
        if depth_order.get(part_result.get("depth", "none"), 0) > depth_order.get(
            accumulated["depth"], 0
        ):
            accumulated["depth"] = part_result["depth"]
        # partitions. Final deduplication is handled by search_cli; this step only combines the reports.
        # partitions. Final deduplication is handled by search_cli; here we only
        # evitamos contar el mismo detalle dos veces.
        for item in part_result.get("items", []):
            item_url = item.get("url") or item.get("discovery_url")
            identity = (item_url, item.get("lot_reference"))
            if identity in seen_detail_keys:
                continue
            seen_detail_keys.add(identity)
            seen_ids.add(item.get("id"))
            accumulated["items"].append(item)
        if part_result.get("counts", {}).get("successful_requests", 0):
            any_successful = True
        if part_result.get("status") in ("blocked", "authentication_required", "error"):
            error_states.append(part_result.get("status"))

    accumulated["discovered_detail_urls"] = list(
        dict.fromkeys(accumulated["discovered_detail_urls"])
    )
    accumulated["document_urls"] = list(dict.fromkeys(accumulated["document_urls"]))
    accumulated["pagination"]["unvisited"] = list(
        dict.fromkeys(
            url
            for url in accumulated["pagination"]["unvisited"]
            if url not in accumulated["pagination"]["visited"]
        )
    )
    accumulated["pagination"]["complete"] = False
    accumulated["limitations"].append(
        {
            "reason": "Inventario BOE/AEAT acotado por presupuesto broad particionado",
            "groups": len(groups),
            "per_group_pages": per_pages,
            "per_group_details": per_details,
            "combined_pages_cap": combined_pages_cap,
            "combined_details_cap": combined_details_cap,
        }
    )
    if accumulated["items"]:
        accumulated["status"] = "partial"
    elif not any_successful:
        reasons = "|".join(error_states)
        if "authentication_required" in reasons:
            accumulated["status"] = "authentication_required"
        elif "blocked" in reasons:
            accumulated["status"] = "blocked"
        elif error_states:
            accumulated["status"] = "error"
        else:
            accumulated["status"] = "partial"
    accumulated["counts"] = {
        "items": len(accumulated["items"]),
        "discovered_details": len(accumulated["discovered_detail_urls"]),
        "requests": len(accumulated["requests"]),
        "successful_requests": sum(
            1
            for r in accumulated["requests"]
            if r.get("http_status") and 200 <= r["http_status"] < 400
        ),
    }
    return accumulated


def discover_configured(source, context):
    """Dispatch a product source by connector, without changing its private ID."""
    connector = source.get("adapter_id", "generic_html")
    if connector not in {"generic_html", "boe", "aeat", *DETAIL_PATTERNS.keys()}:
        raise ValueError("unsupported_adapter")
    runtime = {key: value for key, value in source.items() if key != "adapter_id"}
    # A source called "boe" with the generic connector must stay generic.
    runtime["id"] = "generic_html" if connector == "generic_html" else connector
    scoped = dict(context, public_network_only=True)
    if isinstance(scoped.get("seeds"), dict):
        scoped["seeds"] = scoped["seeds"].get(source["id"], [])
    result = discover(runtime, scoped)
    for item in result.get("items", []):
        if not isinstance(item, dict):
            continue
        original = item.get("id")
        if isinstance(original, str) and original.startswith(runtime["id"] + "-"):
            item["id"] = source["id"] + original[len(runtime["id"]) :]
        item["source_id"] = source["id"]
        item["source_ids"] = [
            source["id"] if value == runtime["id"] else value
            for value in item.get("source_ids", [runtime["id"]])
        ]
    return result


def discover(source, context):
    """Retrieve fresh listings and details with bounded coverage and explicit failures.

    Narrow BOE/AEAT mode uses the original housing/vehicle queries. Broad mode
    uses category partitions with a shared, bounded request budget. Direct calls
    retain the narrow I/501 and V/9102 fallback for compatibility."""
    # Dispatch to the partition wrapper for BOE/AEAT in broad mode.
    # The wrapper calls discover() again for each partition with
    # `broad_partition=True`, so do not rebuild it here.
    if (
        source.get("id") in ("boe", "aeat")
        and bool(context.get("all_assets"))
        and not context.get("broad_partition")
    ):
        groups = _boe_category_groups(context.get("categories"))
        return _broad_partition_discover(source, context, groups)
    result = {
        "status": "partial",
        "items": [],
        "requests": [],
        "limitations": [],
        "depth": "none",
        "discovered_detail_urls": [],
        "document_urls": [],
        "pagination": {"visited": [], "unvisited": [], "complete": False},
        "query": {
            "source_url": source["url"],
            "asset_type": context.get("asset_type"),
            "regions": context.get("regions", []),
            "server_side_filters_applied": False,
        },
        "adapter": "html_embedded_json_v1",
        "inventory_complete": False,
    }
    max_pages, max_details = (
        max(1, int(context.get("max_pages", 2))),
        max(0, int(context.get("max_details", 3))),
    )
    seeds = context.get("seeds", [])
    if isinstance(seeds, dict):
        seeds = seeds.get(source["id"], [])
    seeds = [entry.get("url") if isinstance(entry, dict) else entry for entry in seeds]
    seeds = [public_url(value) for value in seeds if value and public_url(value)]
    fetcher = context.get("fetcher", fetch)
    all_assets = bool(context.get("all_assets", False))
    seed_pattern = DETAIL_PATTERNS.get(
        source["id"], r"/inmueble/[^/?]+|/lote/[^/?]+|/vehiculo/[^/?]+|detalleSubasta\.php\?"
    )
    detail_seeds = [u for u in seeds if re.search(seed_pattern, u, re.I)]
    listing_seeds = [u for u in seeds if u not in detail_seeds]
    if source["id"] in ("boe", "aeat"):
        # In partitioned broad mode, the outer wrapper (_broad_partition_discover)
        # has already injected the correct search URL into `seeds` and set
        # `broad_partition=True`, so do not rebuild it here.
        # Rebuild the URL only in narrow mode (all_assets=False) or when the
        # wrapper did not provide partition-specific seeds.
        if not (all_assets and context.get("broad_partition")):
            asset = context.get("asset_type")
            categories = (
                [("I", "501")]
                if asset == "housing"
                else [("V", "9102")]
                if asset == "vehicles"
                else [("I", "501"), ("V", "9102")]
            )
            search_urls = []
            for kind, subtype in categories:
                form = {
                    "campo[0]": "SUBASTA.ORIGEN",
                    "dato[0]": "A" if source["id"] == "aeat" else "",
                    "campo[2]": "SUBASTA.ESTADO.CODIGO",
                    "dato[2]": "EJ",
                    "campo[3]": "BIEN.TIPO",
                    "dato[3]": kind,
                    "dato[4]": subtype,
                    "accion": "Buscar",
                    "page_hits": "50",
                    "sort_field[0]": "SUBASTA.FECHA_FIN",
                    "sort_order[0]": "asc",
                }
                search_urls.append("https://subastas.boe.es/subastas_ava.php?" + urlencode(form))
            listing_seeds = search_urls + listing_seeds
            result["query"]["server_side_filters_applied"] = True
            result["query"]["published_form"] = (
                "subastas_ava.php: en ejecución; inmuebles vivienda y/o vehículos furgoneta según --asset"
            )
        else:
            # In partitioned broad mode, the wrapper URL is the only listing
            # seed. Marcamos filtro servidor aplicado y publicamos el form usado.
            result["query"]["server_side_filters_applied"] = True
            partition_label = context.get("partition_label", "broad_partition")
            result["query"]["published_form"] = (
                "subastas_ava.php: en ejecución; partición broad {label} "
                "(kind={kind}, subtype={subtype}, max_pages={pages}, max_details={details})".format(
                    label=partition_label,
                    kind=context.get("partition_kind", "?"),
                    subtype=context.get("partition_subtype", "?"),
                    pages=max_pages,
                    details=max_details,
                )
            )
    listing_queue, detail_queue, visited = (
        list(dict.fromkeys(listing_seeds + [source["url"]])),
        detail_seeds,
        set(),
    )
    successful = 0
    while listing_queue and len(result["pagination"]["visited"]) < max_pages:
        url = listing_queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        response = fetcher(url, context)
        result["requests"].append(_request_metadata(response, "listing"))
        result["pagination"]["visited"].append(url)
        blocked = _blocked(response)
        if (
            response.get("error")
            or blocked
            or not response.get("http_status")
            or response["http_status"] >= 400
        ):
            result["limitations"].append(
                {
                    "url": url,
                    "reason": blocked
                    or response.get("error")
                    or f"HTTP {response.get('http_status')}",
                }
            )
            continue
        successful += 1
        result["depth"] = "access_only"
        actual_url = response.get("final_url") or url
        links = discover_links(response["body"], actual_url, source, all_assets=all_assets)
        detail_queue.extend(links["details"])
        result["document_urls"].extend(links["documents"])
        listing_queue.extend(links["pagination"] + links["listings"])
        if links["details"]:
            result["depth"] = "listing"
        # A source URL may itself be an actual property/lot detail.
        if re.search(DETAIL_PATTERNS.get(source["id"], r"(?!)"), actual_url, re.I):
            detail_queue.insert(0, actual_url)
            visited.discard(actual_url)
    detail_queue = list(dict.fromkeys(detail_queue))
    result["discovered_detail_urls"] = detail_queue
    for url in detail_queue[:max_details]:
        if url in visited:
            continue
        visited.add(url)
        response = fetcher(url, context)
        result["requests"].append(_request_metadata(response, "detail"))
        blocked = _blocked(response)
        if (
            response.get("error")
            or blocked
            or not response.get("http_status")
            or response["http_status"] >= 400
        ):
            result["limitations"].append(
                {
                    "url": url,
                    "reason": blocked
                    or response.get("error")
                    or f"HTTP {response.get('http_status')}",
                }
            )
            continue
        actual_url = response.get("final_url") or url
        # Redirect to the homepage or login does not verify the original asset.
        if (
            urlsplit(actual_url).path in ("", "/")
            and urlsplit(url).path not in ("", "/")
            or re.search(r"/login|/signin|/iniciar-sesion", actual_url, re.I)
        ):
            result["limitations"].append(
                {
                    "url": url,
                    "reason": "La ficha redirige a portada o acceso; disponibilidad no verificada",
                }
            )
            continue
        detail_body = response["body"]
        auxiliary_requests = []
        item = extract_item(detail_body, actual_url, source, context.get("observed_at"))
        if source["id"] in ("boe", "aeat") and "detalleSubasta.php" in actual_url:
            from auctionradar.connectors.boe.detail import enrich_boe

            item, auxiliary_requests = enrich_boe(
                item, dict(context, boe_html=response["body"]), fetcher
            )
            result["requests"].extend(auxiliary_requests)
        if source["id"] == "escogecasa":
            from auctionradar.connectors.escogecasa.detail import enrich_escogecasa

            item, gallery_requests = enrich_escogecasa(
                item, dict(context, escogecasa_html=detail_body), fetcher
            )
            result["requests"].extend(gallery_requests)
        if source["id"] == "servihabitat" and "/promociones/" in actual_url:
            item["record_type"] = "development_notice"
            item["asset_type"] = "unknown"
            item["extraction_limitations"] = [
                "Promoción con varios activos; la ficha no identifica una sola vivienda."
            ]
        if source["id"] == "segipsa_pnsd_property":
            item["record_type"] = "auction_notice"
            item["asset_type"] = "unknown"
            item["prices"] = dict.fromkeys(PRICE_KEYS)
            item["normalized_fields"] = {}
            item["photos"] = []
            item["photos_total_extracted"] = 0
            item["availability"] = {
                "status": "unknown",
                "verification": "primary_auction_notice_retrieved",
                "checked_at": context.get("observed_at"),
            }
            item["extraction_limitations"] = [
                "Convocatoria con múltiples lotes: el texto y los documentos no representan una vivienda única."
            ]
        item["auxiliary_evidence"] = auxiliary_requests
        detail_text = item["published_text"]
        shell_title = normalized(item["title"])
        shell = shell_title in (
            "aste industriali on-line",
            "subastas online - la mayor oferta de stocks en espana - escrapalia",
        )
        shell = (
            shell
            or source["id"] == "diglo"
            and "compra y alquiler" in shell_title
            and item.get("facts_scope") != "current_listing_entity"
        )
        shell = shell or bool(
            re.search(r"/contact(?:[/?]|$)|/centros-northgate-ocasion/", actual_url, re.I)
        )
        # Thin JavaScript shells cannot establish facts about this asset.
        if shell or len(detail_text.strip()) < 40 and not item["facts"]:
            result["limitations"].append(
                {
                    "url": url,
                    "reason": "HTML sin ficha del activo: shell JavaScript, página genérica o contenido insuficiente; no cuenta como activo",
                }
            )
            continue
        item["raw_evidence"] = {
            "path": response.get("raw_path"),
            "sha256": response.get("sha256"),
            "http_status": response.get("http_status"),
        }
        item["discovery_url"] = url
        result["items"].append(item)
        successful += 1
        result["depth"] = "listing_and_details"
    result["document_urls"] = list(dict.fromkeys(result["document_urls"]))
    result["pagination"]["unvisited"] = list(
        dict.fromkeys(u for u in listing_queue if u not in visited)
    )
    result["limitations"].append(
        {
            "reason": "Inventario no exhaustivo: recorrido HTTP público acotado; catálogos JavaScript, formularios, autenticación y documentación pueden requerir adaptador adicional."
        }
    )
    if len(detail_queue) > max_details:
        result["limitations"].append(
            {
                "reason": "Límite de fichas por fuente",
                "discovered": len(detail_queue),
                "retrieved_limit": max_details,
            }
        )
    if result["items"]:
        result["status"] = "partial"
    elif not successful:
        reasons = [entry.get("reason", "") for entry in result["limitations"]]
        result["status"] = (
            "authentication_required"
            if "authentication_required" in reasons
            else "blocked"
            if "blocked" in reasons
            else "error"
        )
    result["counts"] = {
        "items": len(result["items"]),
        "discovered_details": len(detail_queue),
        "requests": len(result["requests"]),
        "successful_requests": successful,
    }
    return result
