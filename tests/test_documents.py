import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import auctionradar.documents as document_reader


class DocumentTests(unittest.TestCase):
    def test_failed_fetch_is_not_read(self):
        item = {"documents": [{"url": "https://example.org/x.pdf"}]}
        context = {"max_documents": 2}
        fetch = Mock(side_effect=TimeoutError("timeout"))

        with patch.object(document_reader.shutil, "which", return_value="/mock/pdftotext"):
            document_reader.enrich_documents(item, context, fetch=fetch)

        fetch.assert_called_once_with("https://example.org/x.pdf", context)
        self.assertEqual(item["documents"][0]["read_status"], "fetch_failed")
        self.assertIsNone(item["documents"][0]["extracted_text"])

    def test_html_login_is_not_pdf(self):
        item = {"documents": [{"url": "https://example.org/x.pdf"}]}
        context = {}
        fetch = Mock(return_value=b"<html>Login</html>")

        with patch.object(document_reader.shutil, "which", return_value="/mock/pdftotext"):
            document_reader.enrich_documents(item, context, fetch=fetch)

        fetch.assert_called_once_with("https://example.org/x.pdf", context)
        self.assertEqual(item["documents"][0]["read_status"], "not_pdf")

    def test_missing_converter_is_reported_without_fetching(self):
        item = {"documents": [{"url": "https://example.org/x.pdf"}]}
        fetch = Mock()

        with patch.object(document_reader.shutil, "which", return_value=None) as which:
            document_reader.enrich_documents(item, {}, fetch=fetch)

        which.assert_called_once_with("pdftotext")
        fetch.assert_not_called()
        self.assertEqual(item["documents"][0]["read_status"], "converter_unavailable")
        self.assertIsNone(item["documents"][0]["extracted_text"])

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

    @staticmethod
    def _synthetic_pdf():
        # Minimal valid document shared by unit and real-converter coverage.
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
        return data

    def test_pdf_text_extraction(self):
        data = self._synthetic_pdf()
        item = {"documents": [{"url": "https://example.org/a.pdf"}]}
        output = b"Condiciones oficiales 2026\n"
        fetch = Mock(return_value=data)

        def run_converter(command, **kwargs):
            self.assertEqual(command[0], "/mock/pdftotext")
            self.assertEqual(command[1], "-layout")
            self.assertEqual(command[-1], "-")
            pdf = Path(command[2])
            self.assertTrue(pdf.is_file())
            self.assertEqual(pdf.read_bytes(), data)
            self.assertTrue(kwargs["capture_output"])
            self.assertEqual(kwargs["timeout"], 15)
            return document_reader.subprocess.CompletedProcess(command, 0, output, b"")

        with tempfile.TemporaryDirectory() as tmp:
            context = {"raw_dir": Path(tmp), "observed_at": "2026-10-17"}
            with (
                patch.object(
                    document_reader.shutil, "which", return_value="/mock/pdftotext"
                ) as which,
                patch.object(document_reader.subprocess, "run", side_effect=run_converter) as run,
            ):
                document_reader.enrich_documents(item, context, fetch=fetch)

            which.assert_called_once_with("pdftotext")
            fetch.assert_called_once_with("https://example.org/a.pdf", context)
            run.assert_called_once()
            doc = item["documents"][0]
            self.assertEqual(doc["read_status"], "text_extracted")
            self.assertEqual(doc["extracted_text"], output.decode())
            self.assertEqual(doc["text_scope"], "all_pages_text_only_no_ocr")
            self.assertEqual(doc["observed_at"], "2026-10-17")
            expected_sha = hashlib.sha256(data).hexdigest()
            self.assertEqual(doc["sha256"], expected_sha)
            self.assertEqual(doc["text_path"], str(Path(tmp) / (expected_sha + ".pdf.txt")))
            self.assertEqual(Path(doc["text_path"]).read_text(encoding="utf-8"), output.decode())

    @unittest.skipUnless(
        document_reader.shutil.which("pdftotext"), "Optional Poppler not installed"
    )
    def test_real_pdf_text_extraction(self):
        data = self._synthetic_pdf()
        item = {"documents": [{"url": "https://example.org/synthetic.pdf"}]}
        fetch = Mock(return_value=data)
        with tempfile.TemporaryDirectory() as tmp:
            context = {"raw_dir": Path(tmp), "observed_at": "2026-10-17"}
            document_reader.enrich_documents(item, context, fetch=fetch)
            fetch.assert_called_once_with("https://example.org/synthetic.pdf", context)
            doc = item["documents"][0]
            self.assertEqual(doc["read_status"], "text_extracted")
            self.assertIn("Condiciones oficiales 2026", doc["extracted_text"])
            self.assertEqual(doc["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(doc["text_scope"], "all_pages_text_only_no_ocr")
            self.assertEqual(
                Path(doc["text_path"]).read_text(encoding="utf-8"), doc["extracted_text"]
            )


if __name__ == "__main__":
    unittest.main()
