"""Real-loopback regressions for AI provider redirect handling."""

from __future__ import annotations

import contextlib
import datetime
import http.server
import pathlib
import ssl
import tempfile
import threading
import unittest
import urllib.request

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from auctionradar.web import ai_filter


class _RedirectHandler(http.server.BaseHTTPRequestHandler):
    server: _RedirectServer  # type: ignore[assignment]

    def do_POST(self):  # noqa: N802 - http.server convention
        length = int(self.headers.get("Content-Length", "0") or 0)
        self.rfile.read(length)
        self.server.requests.append(
            {
                "method": "POST",
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        self.send_response(self.server.redirect_status)
        self.send_header("Location", self.server.location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args, **_kwargs):
        return


class _TargetHandler(http.server.BaseHTTPRequestHandler):
    server: _TargetServer  # type: ignore[assignment]

    def _record(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length:
            self.rfile.read(length)
        self.server.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        )
        body = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _record
    do_POST = _record

    def log_message(self, *_args, **_kwargs):
        return


class _RedirectServer(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _RedirectHandler)
        self.requests = []
        self.redirect_status = 302
        self.location = ""


class _TargetServer(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), _TargetHandler)
        self.requests = []


@contextlib.contextmanager
def _serving(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _add_test_tls(server, directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-loopback.invalid")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    certificate_path = pathlib.Path(directory) / "synthetic-loopback-cert.pem"
    key_path = pathlib.Path(directory) / "synthetic-loopback-key.pem"
    certificate_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certificate_path, key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)


class AIProviderRedirectTests(unittest.TestCase):
    def test_provider_post_never_follows_redirect_to_another_origin(self):
        target = _TargetServer()
        redirector = _RedirectServer()
        redirector.location = f"http://127.0.0.1:{target.server_port}/redirect-target"
        self.assertNotEqual(target.server_port, redirector.server_port)
        api_key = "synthetic-redirect-regression-key"
        settings = ai_filter.AISettings(
            provider="openai_compatible",
            base_url=f"http://127.0.0.1:{redirector.server_port}",
            model="synthetic-test-model",
            api_key=api_key,
            timeout=2,
            max_retries=0,
        )
        item = {
            "asset_type": "housing",
            "facts_scope": "current_listing_entity",
            "facts": [],
            "prices": {},
        }

        with _serving(target), _serving(redirector):
            for status in (301, 302, 303, 307, 308):
                with self.subTest(redirect_status=status):
                    redirector.redirect_status = status
                    redirector.requests.clear()
                    target.requests.clear()

                    result = ai_filter.evaluate_item("synthetic prompt", item, settings=settings)

                    self.assertEqual(result["status"], "error")
                    self.assertEqual(result["error_code"], ai_filter.ERROR_RESPONSE)
                    self.assertEqual(len(redirector.requests), 1)
                    self.assertEqual(redirector.requests[0]["method"], "POST")
                    self.assertEqual(redirector.requests[0]["authorization"], f"Bearer {api_key}")
                    # No redirected request reaches the second origin.
                    self.assertEqual(target.requests, [])

    def test_https_to_http_downgrade_never_reaches_redirect_target(self):
        target = _TargetServer()
        redirector = _RedirectServer()
        redirector.redirect_status = 302
        redirector.location = f"http://127.0.0.1:{target.server_port}/downgrade-target"
        api_key = "synthetic-downgrade-regression-key"
        settings = ai_filter.AISettings(
            provider="openai_compatible",
            base_url=f"https://127.0.0.1:{redirector.server_port}",
            model="synthetic-test-model",
            api_key=api_key,
            timeout=2,
            max_retries=0,
        )
        item = {
            "asset_type": "housing",
            "facts_scope": "current_listing_entity",
            "facts": [],
            "prices": {},
        }
        client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_context.check_hostname = False
        client_context.verify_mode = ssl.CERT_NONE
        opener = urllib.request.build_opener(
            ai_filter._RejectProviderRedirects(),
            urllib.request.HTTPSHandler(context=client_context),
        ).open

        with tempfile.TemporaryDirectory(prefix="ai-redirect-tls-") as directory:
            _add_test_tls(redirector, directory)
            with _serving(target), _serving(redirector):
                result = ai_filter.evaluate_item(
                    "synthetic prompt", item, settings=settings, opener=opener
                )

        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_code"], ai_filter.ERROR_RESPONSE)
        self.assertEqual(len(redirector.requests), 1)
        self.assertEqual(redirector.requests[0]["method"], "POST")
        self.assertEqual(redirector.requests[0]["authorization"], f"Bearer {api_key}")
        self.assertEqual(target.requests, [])
