import tempfile
import unittest
from pathlib import Path

import auctionradar.documents as document_reader


class DocumentTests(unittest.TestCase):
    def test_failed_fetch_is_not_read(self):
        item = {"documents": [{"url": "https://example.org/x.pdf"}]}

        def fail(url, context):
            raise TimeoutError("timeout")

        document_reader.enrich_documents(item, {"max_documents": 2}, fetch=fail)
        self.assertEqual(item["documents"][0]["read_status"], "fetch_failed")
        self.assertIsNone(item["documents"][0]["extracted_text"])

    def test_html_login_is_not_pdf(self):
        item = {"documents": [{"url": "https://example.org/x.pdf"}]}
        document_reader.enrich_documents(item, {}, fetch=lambda u, c: b"<html>Login</html>")
        self.assertEqual(item["documents"][0]["read_status"], "not_pdf")

    def test_limit_is_explicit(self):
        item = {
            "documents": [
                {"url": "https://example.org/a.pdf"},
                {"url": "https://example.org/b.pdf"},
            ]
        }
        document_reader.enrich_documents(item, {"max_documents": 0})
        self.assertEqual(item["documents"][1]["read_status"], "not_read_limit")

    def test_site_policy_is_not_read_as_asset_document(self):
        item = {
            "documents": [
                {
                    "url": "https://example.org/codigo-de-conducta.pdf",
                    "title": "Código conducta proveedores",
                }
            ]
        }

        def forbidden(u, c):
            self.fail("No debe descargar política general como expediente")

        document_reader.enrich_documents(item, {}, fetch=forbidden)
        self.assertEqual(item["documents"][0]["scope"], "site_policy")
        self.assertEqual(item["documents"][0]["read_status"], "not_read_site_policy")

    def test_pdf_text_extraction(self):
        import shutil

        if not shutil.which("pdftotext"):
            self.skipTest("Poppler opcional no instalado")
        # Minimal valid document; verify real conversion, not a mocked converter.
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        stream = b"BT /F1 12 Tf 20 250 Td (Condiciones oficiales 2026) Tj ET"
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
        data = b"%PDF-1.4\n"
        offsets = [0]
        for i, obj in enumerate(objects, 1):
            offsets.append(len(data))
            data += str(i).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
        start = len(data)
        data += (
            b"xref\n0 6\n0000000000 65535 f \n"
            + b"".join(f"{n:010d} 00000 n \n".encode() for n in offsets[1:])
            + b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
            + str(start).encode()
            + b"\n%%EOF"
        )
        item = {"documents": [{"url": "https://example.org/a.pdf"}]}
        with tempfile.TemporaryDirectory() as tmp:
            document_reader.enrich_documents(
                item, {"raw_dir": Path(tmp), "observed_at": "2026-10-17"}, fetch=lambda u, c: data
            )
            doc = item["documents"][0]
            self.assertEqual(doc["read_status"], "text_extracted")
            self.assertIn("Condiciones oficiales 2026", doc["extracted_text"])
            self.assertEqual(doc["text_scope"], "all_pages_text_only_no_ocr")


if __name__ == "__main__":
    unittest.main()
