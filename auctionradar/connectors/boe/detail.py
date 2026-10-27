"""Enrich BOE listings through published navigation, without assuming tab numbers."""

import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

# Use a conservative cap to avoid fetching malicious or noisy galleries.
MAX_BOE_PHOTOS = 50


class AssetTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields, self.rows, self.cell, self.parts = {}, [], None, []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows = []
        if tag in ("th", "td"):
            self.cell, self.parts = tag, []

    def handle_data(self, value):
        if self.cell:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == self.cell:
            self.rows.append(" ".join(" ".join(self.parts).split()))
            self.cell = None
        if tag == "tr" and len(self.rows) == 2:
            key, value = self.rows
            # Multiple biens can have conflicting values: retain every row.
            if key in self.fields and self.fields[key] != value:
                old = self.fields[key]
                self.fields[key] = old + [value] if isinstance(old, list) else [old, value]
            else:
                self.fields[key] = value


def _bien_blocks(tab_html):
    """Return (asset_number, inner_html) for each published Bien N block.

    Without headings, treat the tab as one unnumbered block. One heading keeps the
    whole tab; multiple headings split it into independently scoped asset blocks."""
    tab_html = re.sub(r"<!--.*?-->", "", tab_html, flags=re.S)
    tab_html = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", "", tab_html, flags=re.S | re.I)
    pattern = re.compile(r"(?is)<h4\b[^>]*>\s*Bien\s+(\d+)\s*[-\u2013\u2014:]")
    matches = list(pattern.finditer(tab_html))
    if not matches:
        return []
    if len(matches) == 1:
        try:
            number = int(matches[0].group(1))
        except (TypeError, ValueError):
            number = None
        return [(number, tab_html[matches[0].start() :])]
    blocks = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tab_html)
        try:
            number = int(match.group(1))
        except (TypeError, ValueError):
            number = None
        blocks.append((number, tab_html[start:end]))
    return blocks


class _BienPhotoParser(HTMLParser):
    """Extract actual image URLs from the current asset block.

    Inspect src, data-src, data-original and image links. Ignore institutional
    logos, Open Graph images and media not associated with the asset."""

    _LOGO_HINTS = re.compile(
        r"logo|icon|spinner|banner|placeholder|avatar|pixel|facebook|instagram|"
        r"portal_subastas|logoMPR|logo_boe|logoBuscar|logoAcceso|ayuda\.|sprite",
        re.I,
    )
    _INSTITUTIONAL_PATH = re.compile(r"^/?imagenes/", re.I)
    _IMG_EXT = re.compile(r"\.(?:jpe?g|png|webp|gif)(?:\?|$)", re.I)

    def __init__(self, base_url, id_sub):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.id_sub = id_sub or ""
        self.candidates = []  # lista de (raw_url, caption)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "img":
            url = (
                attrs.get("data-src")
                or attrs.get("data-original")
                or attrs.get("data-lazy")
                or attrs.get("src")
                or ""
            )
            caption = attrs.get("alt") or attrs.get("title") or ""
            self._consider(url, caption)
        elif tag == "a":
            href = attrs.get("href") or ""
            # Consider links that point to an image (public galleries).
            if self._IMG_EXT.search(href):
                self._consider(href, attrs.get("title") or "")

    def _consider(self, raw_url, caption):
        if not raw_url:
            return
        # Descarta credenciales embebidas y esquemas no-http(s).
        lowered = raw_url.strip().lower()
        if lowered.startswith(("javascript:", "data:", "vbscript:", "file:")):
            return
        if "@" in raw_url.split("//", 1)[-1].split("/", 1)[0]:
            return
        if self._LOGO_HINTS.search(raw_url):
            return
        # Institutional paths on the BOE portal (logos, sprites, help). Real
        # asset photos live under `/reg/fotos/...` or another non-institutional
        # path, so this rule is safe.
        if self._INSTITUTIONAL_PATH.search(raw_url):
            return
        self.candidates.append((raw_url, caption.strip()))


def collect_boe_photos(
    tab_html, tab_url, id_sub, observed_at, bien_number=None, max_photos=MAX_BOE_PHOTOS
):
    """Return canonical photos for the current asset, bounded by max_photos.

    For multiple Bien N blocks, accept only the requested asset number. Sanitize
    URLs through the public connector; omit credentials and sensitive query data.
    Mark floor plans from explicit captions or URLs. Always return a list."""
    from auctionradar.connectors.catalog import public_url

    if not tab_html or not tab_url:
        return []
    blocks = _bien_blocks(tab_html)
    if bien_number is not None:
        # Filter to the current lot block when available; if the structure
        # cannot be identified, accept NO photos (strict lot scoping).
        selected = [inner for number, inner in blocks if number == bien_number]
        if not selected:
            return []
    else:
        # Without a lot number, accept the single block or all blocks
        # only when the tab is NOT separated by lot.
        if len(blocks) == 1:
            selected = [blocks[0][1]]
        else:
            # Multiple lots and no explicit number: reject to avoid mixing photos.
            return []
    parser = _BienPhotoParser(tab_url, id_sub)
    seen = set()
    photos = []
    for inner in selected:
        parser.feed(inner)
    id_sub_pattern = re.compile(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]+", re.I)
    current_id = (id_sub or [""])[0] if isinstance(id_sub, list) else (id_sub or "")
    for raw_url, caption in parser.candidates:
        absolute = public_url(urljoin(tab_url, raw_url))
        if not absolute:
            continue
        # Scope de subasta: si la URL referencia OTRO idSub (en query o en
        # path), discard it. If it references none, accept it: photo paths
        # may not include the idSub.
        if current_id:
            qs = parse_qs(urlsplit(absolute).query)
            qs_ids = qs.get("idSub") or []
            if qs_ids and qs_ids[0] != current_id:
                continue
            path_ids = id_sub_pattern.findall(absolute)
            if path_ids and not all(token.upper() == current_id.upper() for token in path_ids):
                continue
        if absolute in seen:
            continue
        seen.add(absolute)
        kind = (
            "floorplan"
            if re.search(r"plano|floorplan|planimetr", (absolute + " " + caption), re.I)
            else "unclassified"
        )
        photos.append(
            {
                "url": absolute,
                "source_url": tab_url,
                "caption": caption or None,
                "attribution": "current_entity_gallery",
                "kind": kind,
                "observed_at": observed_at,
                "verification": "published_url_only",
                "visual_content_verified": False,
            }
        )
        if len(photos) >= max_photos:
            break
    return photos


def enrich_boe(item, context, fetcher):
    """Return (item, HTTP metadata); context.boe_html avoids refetching the base page."""
    from auctionradar.connectors.catalog import Page, extract_item, normalized, public_url

    requests = []
    base = item["url"]
    original_id = parse_qs(urlsplit(base).query).get("idSub")

    def retrieve(url, purpose):
        response = fetcher(url, context)
        metadata = {k: v for k, v in response.items() if k not in ("body", "body_bytes")}
        metadata["purpose"] = purpose
        requests.append(metadata)
        if response.get("http_status") != 200 or response.get("error"):
            item.setdefault("enrichment_limitations", []).append(
                {
                    "url": url,
                    "reason": response.get("error") or f"HTTP {response.get('http_status')}",
                }
            )
            return None
        return response.get("body", "")

    body = context.get("boe_html")
    if body is None:
        body = retrieve(base, "detail_navigation")
    if not body:
        return item, requests
    navigation = Page()
    navigation.feed(body)
    urls = []
    for link in navigation.links:
        if normalized(link["text"].strip()) not in ("bienes", "documentos", "documentacion"):
            continue
        url = public_url(urljoin(base, link["url"]))
        if not url or urlsplit(url).hostname != urlsplit(base).hostname:
            continue
        if parse_qs(urlsplit(url).query).get("idSub") != original_id:
            continue
        original_lot = (parse_qs(urlsplit(base).query).get("idLote") or [""])[0]
        linked_lot = (parse_qs(urlsplit(url).query).get("idLote") or [""])[0]
        if original_lot and original_lot != linked_lot:
            continue
        if url not in urls:
            urls.append(url)
    if not urls:
        item.setdefault("enrichment_limitations", []).append(
            {
                "url": base,
                "reason": "No se encontró enlace publicado a Bienes/Documentos; no se supone endpoint.",
            }
        )
    for url in urls[:3]:
        tab = retrieve(url, "asset_tab")
        if not tab:
            continue
        table = AssetTable()
        table.feed(tab)
        item.setdefault("boe_asset_fields", {}).update(table.fields)
        page = Page()
        page.feed(tab)
        # Official asset label: it appears in <h4>Bien N - <TIPO></h4>. General
        # tab navigation may mention "Buque" as a heading
        # (e.g. "Subastas de Buques") and must NOT trigger classification.
        bien_label_match = re.search(
            r"(?is)<h4\b[^>]*>\s*Bien\s+\d+\s*[-\u2013\u2014:]\s*([^<]+?)\s*</h4>", tab
        )
        bien_label = bien_label_match.group(1).strip() if bien_label_match else ""
        text = "\n".join(x.strip() for x in page.text_parts if x.strip())
        item.setdefault("boe_asset_evidence", []).append(
            {
                "source_url": url,
                "published_text": text,
                "observed_at": item.get("observed_at"),
                "basis": "primary_publication",
            }
        )
        item["published_text"] += "\n" + text
        source = {"id": item.get("source_id", "boe"), "asset_types": ["housing", "vehicles"]}
        parsed = extract_item(tab, url, source, item.get("observed_at"))
        if "inmueble (vivienda)" in normalized(text):
            item["asset_type"] = "housing"
        # The official asset label takes precedence over navigation or recommendations.
        bien_label_norm = normalized(bien_label)
        if bien_label:
            from auctionradar.assets import normalize_category

            typed = normalize_category(
                {
                    "title": bien_label,
                    "record_type": "asset",
                    "facts_scope": "current_listing_entity",
                }
            )
            if typed["asset_type"] != "unknown":
                item["asset_type"] = typed["asset_type"]
                item["category"] = typed["category"]
            if normalized(item.get("title", "")) in (
                "portal de subastas electronicas",
                "portal de subastas",
            ):
                item["title"] = (
                    bien_label
                    + " — "
                    + str(
                        table.fields.get("Localidad") or item.get("reference") or item.get("id", "")
                    )
                )
        if bien_label and table.fields:
            if item.get("facts_scope") != "current_listing_entity":
                item["facts"] = []
            item["facts_scope"] = "current_listing_entity"
            item.setdefault("facts", []).extend(
                {
                    "field": "boe." + key,
                    "value": value,
                    "source_url": url,
                    "basis": "official_current_asset_tab",
                }
                for key, value in table.fields.items()
            )
            if isinstance(table.fields.get("Descripción"), str):
                item["description"] = table.fields["Descripción"]
        if "buque" in bien_label_norm:
            item["asset_type"] = "boats"
            item.setdefault("boe_bien_labels", []).append(bien_label.strip() or "Buque")
            item.setdefault("enrichment_notes", []).append(
                {
                    "note": "asset_type_boats_from_official_bien_label",
                    "basis": "official_publication_tab",
                }
            )
        raw = table.fields.get("Situación posesoria")
        values = raw if isinstance(raw, list) else [raw] if raw is not None else []
        normalized_values = [normalized(v) for v in values]
        if values:
            status = "unknown"
            if all(v in ("libre", "desocupado", "desocupada") for v in normalized_values):
                status = "seller_claim_free"
            elif any(
                ("ocupad" in v and "desocupad" not in v) or "ocupant" in v or "arrendad" in v
                for v in normalized_values
            ):
                status = "occupied_or_tenanted"
            item["possession"] = {
                "status": status,
                "documented_delivery_free": False,
                "evidence": [
                    {"quote": v, "source_url": url, "field": "Situación posesoria"} for v in values
                ],
            }
        visit = table.fields.get("Visitable")
        if isinstance(visit, str):
            item["visits"] = {
                "status": {"si": "offered", "no": "not_visitable"}.get(
                    normalized(visit), "unknown"
                ),
                "evidence": visit,
                "source_url": url,
            }
        item.setdefault("location", {})
        for key, target in [
            ("Dirección", "address"),
            ("Código Postal", "postal_code"),
            ("Localidad", "locality"),
            ("Provincia", "province"),
        ]:
            if key in table.fields:
                item["location"][target] = table.fields[key]
        if "Referencia catastral" in table.fields:
            item["cadastral_reference"] = table.fields["Referencia catastral"]
        documents = item.setdefault("documents", [])
        existing = {d["url"] for d in documents}
        for document in parsed["documents"]:
            if document["url"] not in existing:
                documents.append(document)
                existing.add(document["url"])
        # Photos for the current asset block. Collect them from the actual HTML
        # of the official tab without assuming an endpoint. If the tab does not
        # expose photos, the limitation is recorded honestly: an empty list and
        # the existing `enrichment_limitation` if the tab failed.
        # Bien N y Lote N son numeraciones diferentes. Un lote puede contener
        # There may be multiple assets; without an asset identifier, do not select the first.
        bien_number = item.get("bien_number")
        if len(_bien_blocks(tab)) > 1 and bien_number is None:
            item.setdefault("enrichment_limitations", []).append(
                {"url": url, "reason": "boe_multiple_assets_photo_scope_unknown"}
            )
        try:
            photos = collect_boe_photos(
                tab,
                url,
                original_id,
                item.get("observed_at"),
                bien_number=bien_number,
            )
        except (ValueError, AttributeError):
            photos = []
        # Honest limitation: if the official tab does NOT expose photos for the
        # current asset and displays the "Iniciar sesión" marker, record it
        # in `enrichment_limitations` rather than inventing empty entries.
        if not photos and re.search(
            r"para consultar (?:la )?informacion complementaria.{0,400}iniciar sesion",
            normalized(page.text),
            re.S,
        ):
            item.setdefault("enrichment_limitations", []).append(
                {
                    "url": url,
                    "reason": "boe_gallery_behind_authentication",
                    "observed_at": item.get("observed_at"),
                }
            )
        photos_attr = item.setdefault("photos", [])
        existing_photos = {p["url"] for p in photos_attr}
        for photo in photos:
            if len(photos_attr) >= MAX_BOE_PHOTOS:
                break
            if photo["url"] in existing_photos:
                continue
            photos_attr.append(photo)
            existing_photos.add(photo["url"])
        item["photos_total_extracted"] = len(photos_attr)
        item.setdefault("evidence", []).append(
            {
                "source_url": url,
                "basis": "primary_publication",
                "observed_at": item.get("observed_at"),
                "fields": table.fields,
            }
        )
    return item, requests
