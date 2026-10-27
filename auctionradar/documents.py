"""Optional public-document reading; a link alone does not establish that a document was read."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import unicodedata
from pathlib import Path


def enrich_documents(item, context, fetch=None):
    """Attach actual extracted text and scope, without legal interpretation."""
    maximum = int(context.get("max_documents", 1))
    converter = shutil.which("pdftotext")
    attempted = 0
    for index, document in enumerate(item.get("documents", [])):
        if isinstance(document, str):
            document = {"url": document}
            item["documents"][index] = document
        document.update(
            extracted_text=None, text_scope=None, observed_at=context.get("observed_at")
        )
        label = str(document.get("url", "")) + " " + str(document.get("title", ""))
        label = "".join(
            c for c in unicodedata.normalize("NFKD", label) if not unicodedata.combining(c)
        ).lower()
        site_policy = re.search(
            r"privacidad|privacy|cookies|codigo.?de.?conducta|codigo conducta|proveedores|seguridad.?de.?la.?informacion|normas seguridad|guia.?analisis|guia.?alquiler|politica.?de.?calidad",
            label,
        )
        document.setdefault(
            "scope", "site_policy" if site_policy else "unconfirmed_asset_or_sale_document"
        )
        if document["scope"] == "site_policy":
            document["read_status"] = "not_read_site_policy"
            continue
        if attempted >= maximum:
            document["read_status"] = "not_read_limit"
            continue
        if not converter:
            document["read_status"] = "converter_unavailable"
            continue
        attempted += 1
        try:
            if fetch is None:
                from auctionradar.connectors.catalog import fetch
            response = fetch(document["url"], context)
            if isinstance(response, dict):
                document["http_status"] = response.get("http_status")
                if response.get("error"):
                    raise OSError(response["error"])
                data = response.get("body_bytes", b"")
            else:
                data = response
            if not data.startswith(b"%PDF-"):
                document["read_status"] = "not_pdf"
                continue
            if len(data) > 8 * 1024 * 1024:
                document["read_status"] = "size_limit"
                continue
            document["sha256"] = hashlib.sha256(data).hexdigest()
            with tempfile.TemporaryDirectory(prefix="compra-document-") as tmp:
                pdf = Path(tmp) / "document.pdf"
                pdf.write_bytes(data)
                result = subprocess.run(
                    [converter, "-layout", str(pdf), "-"],
                    capture_output=True,
                    timeout=max(1, float(context.get("timeout", 15))),
                )
                if result.returncode:
                    document["read_status"] = "extraction_failed"
                    document["error"] = result.stderr.decode("utf-8", errors="replace")[:500]
                    continue
                value = result.stdout.decode("utf-8", errors="replace")
                document["extracted_text"] = value[:200000]
                document["read_status"] = (
                    "text_extracted" if value.strip() else "no_text_requires_ocr"
                )
                document["text_scope"] = (
                    "first_200000_characters_text_only_no_ocr"
                    if len(value) > 200000
                    else "all_pages_text_only_no_ocr"
                )
                document["page_count_from_text"] = value.count("\f")
                raw = context.get("raw_dir")
                if raw:
                    folder = Path(raw)
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / (document["sha256"] + ".pdf.txt")
                    path.write_text(value[:200000], encoding="utf-8")
                    document["text_path"] = str(path)
        except Exception as exc:
            document["read_status"] = "fetch_failed"
            document["error"] = f"{type(exc).__name__}: {exc}"[:500]
    return item
