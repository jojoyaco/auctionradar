"""Public Escogecasa photos and displayed prices, without authenticated sessions.

The published client reads /nodejs/getFicha by POST. Parameters come from the
listing JSON props, never recommended listings. A confirmed gallery does not
make surrounding navigation text evidence about the current asset."""

import json
import math
import re
from urllib.parse import urljoin, urlsplit


def _identity(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in ("www.escogecasa.es", "escogecasa.es"):
        return None
    match = re.fullmatch(r"/(?:[a-z]{2}/)?inmueble/[^/]+/([^/]+)/(\d+)/?", parsed.path)
    return match.groups() if match else None


def _props(body, identifier):
    from auctionradar.connectors.catalog import parse_page

    decoder = json.JSONDecoder()
    for script in parse_page(body).scripts:
        for match in re.finditer(r"self\.__next_f\.push\s*\(", script.get("text", "")):
            raw = script["text"]
            start = match.end()
            while start < len(raw) and raw[start].isspace():
                start += 1
            try:
                record, _ = decoder.raw_decode(raw, start)
            except (ValueError, TypeError):
                continue
            if (
                not isinstance(record, list)
                or len(record) != 2
                or record[0] != 1
                or not isinstance(record[1], str)
            ):
                continue
            for line in record[1].splitlines():
                if ":" not in line:
                    continue
                try:
                    entry = json.loads(line.split(":", 1)[1])
                except ValueError:
                    continue
                if not isinstance(entry, list) or len(entry) < 4 or not isinstance(entry[3], dict):
                    continue
                props = entry[3]
                if (
                    str(props.get("idInmueble")) == identifier
                    and props.get("esFichaPromo") is False
                ):
                    if isinstance(props.get("lang"), int) and isinstance(
                        props.get("idGestion"), int
                    ):
                        return props
    return None


def _published_price(data):
    """Return only the current EUR sale amount displayed by the official client."""
    from auctionradar.connectors.catalog import money

    if not isinstance(data, dict) or data.get("esFichaPromo") is not False:
        return None
    if type(data.get("idGestion")) is not int or data["idGestion"] != 1:
        return None
    if type(data.get("precioVisible01")) is not int or data["precioVisible01"] != 1:
        return None
    value = data.get("precio")
    if isinstance(value, str) and not re.fullmatch(
        r"\s*(?:(?:\d+|\d{1,3}(?:[.\s\u00a0]\d{3})+)(?:,\d{1,2})?|\d+\.\d{1,2})\s*(?:€|EUR)?\s*",
        value,
        re.I,
    ):
        return None
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        amount = money(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return amount if amount is not None and math.isfinite(amount) and 0 < amount <= 1e12 else None


def _public_response_keys(data):
    keys = ["idInmueble", "referencia", "esFichaPromo", "fotos", "idGestion", "precioVisible01"]
    if _published_price(data) is not None:
        keys.append("precio")
    return keys


def _apply_public_price(item, data, props, reference, identifier, url, endpoint, observed_at):
    amount = _published_price(data)
    if amount is None or type(props.get("idGestion")) is not int or props["idGestion"] != 1:
        return
    item["prices"] = dict(item.get("prices") or {})
    item["prices"]["asking"] = amount
    item["currency"] = "EUR"
    evidence = {
        "field": "prices.asking",
        "value": amount,
        "path": "precio",
        "source_url": url,
        "api_url": endpoint,
        "basis": "seller_public_api",
        "observed_at": observed_at,
        "property_id": identifier,
        "property_reference": reference,
        "price_visible": True,
        "management": "sale",
    }
    item["evidence"] = [
        entry
        for entry in item.get("evidence") or []
        if not (isinstance(entry, dict) and entry.get("field") == "prices.asking")
    ] + [evidence]
    item["facts"] = [
        entry
        for entry in item.get("facts") or []
        if not (isinstance(entry, dict) and entry.get("field") == "prices.asking")
    ] + [dict(evidence)]
    item["price_extraction_method"] = "escogecasa_public_detail_api"


def enrich_escogecasa(item, context, fetcher=None):
    from auctionradar.connectors.catalog import _request_metadata, fetch, parse_page, public_url

    fetcher = fetcher or fetch
    url = item.get("url", "")
    identity = _identity(url)
    if not identity:
        return item, []
    reference, identifier = identity
    body = context.get("escogecasa_html", "")
    props = _props(body, identifier)
    requests = []
    candidates = []
    problem = None
    endpoint = urljoin(url, "/nodejs/getFicha")
    if props:
        payload = {
            "idIdioma": props["lang"],
            "idInmueble": identifier,
            "idPromocion": None,
            "esFichaPromo": False,
            "idGestion": props["idGestion"],
        }
        response = fetcher(
            endpoint,
            {**context, "request_json": payload, "json_response_keys": _public_response_keys},
        )
        requests.append(_request_metadata(response, "asset_gallery_api"))
        final = urlsplit(response.get("final_url") or endpoint)
        if (
            response.get("error")
            or response.get("http_status") != 200
            or final.scheme != "https"
            or final.hostname not in ("www.escogecasa.es", "escogecasa.es")
            or final.path != "/nodejs/getFicha"
        ):
            problem = "No se pudo consultar la galería pública de la ficha"
        else:
            try:
                data = json.loads(response.get("body", ""))
            except ValueError:
                data = None
            if (
                not isinstance(data, dict)
                or str(data.get("idInmueble")) != identifier
                or str(data.get("referencia")) != reference
                or data.get("esFichaPromo") is True
            ):
                problem = "La respuesta de la galería no acredita el inmueble solicitado"
            else:
                _apply_public_price(
                    item,
                    data,
                    props,
                    reference,
                    identifier,
                    url,
                    endpoint,
                    context.get("observed_at"),
                )
                if isinstance(data.get("fotos"), list):
                    candidates = [
                        {
                            "url": photo.get("urlFoto"),
                            "caption": photo.get("descripcion"),
                            "evidence_url": endpoint,
                        }
                        for photo in data["fotos"][:50]
                        if isinstance(photo, dict)
                    ]
                else:
                    problem = "La ficha pública no contiene una galería reconocible"
    else:
        problem = "No se encontraron los parámetros publicados de la galería de este inmueble"
    if not candidates:
        meta = parse_page(body).meta
        image = public_url(meta.get("og:image", ""))
        if (
            image
            and _identity(meta.get("og:url", "")) == identity
            and urlsplit(image).hostname == "ngb-repositorio.urbaniza.com"
            and re.search(
                r"/inmuebles/fotos/[^?]*/" + re.escape(identifier) + r"_\d+\.(?:jpe?g|png|webp)$",
                urlsplit(image).path,
                re.I,
            )
        ):
            candidates = [{"url": image, "caption": meta.get("og:image:alt"), "evidence_url": url}]
    photos = list(item.get("photos") or [])
    seen = {photo.get("url") for photo in photos if isinstance(photo, dict)}
    for photo in candidates:
        target = public_url(photo.get("url") or "")
        if (
            not target
            or target in seen
            or re.search(
                r"logo|icon|spinner|banner|placeholder|avatar|pixel", urlsplit(target).path, re.I
            )
        ):
            continue
        if not re.search(r"\.(?:jpe?g|png|webp)(?:\?|$)", target, re.I):
            continue
        caption = photo.get("caption")
        photos.append(
            {
                "url": target,
                "source_url": url,
                "caption": caption if isinstance(caption, str) else None,
                "attribution": "current_entity_gallery",
                "kind": "unclassified",
                "observed_at": context.get("observed_at"),
                "verification": "published_url_only",
                "visual_content_verified": False,
                "gallery_evidence_url": photo["evidence_url"],
                "property_id": identifier,
                "property_reference": reference,
            }
        )
        seen.add(target)
    item["photos"] = photos
    item["photos_total_extracted"] = len(photos)
    if problem:
        item.setdefault("enrichment_limitations", []).append({"reason": problem, "url": endpoint})
    if photos:
        item["photo_extraction_method"] = "escogecasa_public_gallery"
    return item, requests
