"""Run the extracted product under real TLS/Gunicorn with disposable identities.

Only loopback connections are used; no auction portal or production service is
contacted. This test also runs as an ordinary Unix user (no root requirement).
"""

import http.cookiejar
import json
import os
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tests.test_credential_store import make_synthetic_pkcs12

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("openssl"), "OpenSSL is required for the synthetic TLS fixture")
class ProductRuntimeTests(unittest.TestCase):
    def test_extracted_product_tls_admin_certificate_and_restart_registration(self):
        with tempfile.TemporaryDirectory(prefix="auctionradar-runtime-") as directory:
            root = Path(directory)
            package = ROOT / "deploy/product/package.py"
            archive = root / "product.tar.gz"
            built = subprocess.run(
                [sys.executable, str(package), "build", "--output", str(archive)],
                check=True,
                capture_output=True,
                text=True,
            )
            digest = built.stdout.split("sha256=")[1].splitlines()[0]
            subprocess.run(
                [
                    sys.executable,
                    str(package),
                    "verify",
                    "--archive",
                    str(archive),
                    "--sha256",
                    digest,
                    "--destination",
                    str(root / "extracted"),
                ],
                check=True,
                capture_output=True,
            )
            code = root / "extracted/auctionradar-product"
            state = root / "state"
            session = root / "session.key"
            key = root / "credential.key"
            session.write_text(secrets.token_urlsafe(48))
            key.write_bytes(os.urandom(32))
            session.chmod(0o600)
            key.chmod(0o600)
            tls_key, tls_cert = root / "tls.key", root / "tls.crt"
            subprocess.run(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "1",
                    "-subj",
                    "/CN=localhost",
                    "-addext",
                    "subjectAltName=IP:127.0.0.1",
                    "-keyout",
                    str(tls_key),
                    "-out",
                    str(tls_cert),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            env = {
                name: value
                for name, value in os.environ.items()
                if not name.startswith("AUCTIONRADAR_")
                and name not in {"CREDENTIALS_DIRECTORY", "PYTHONPATH"}
            }
            env.update(
                AUCTIONRADAR_ENV="production",
                AUCTIONRADAR_WORKSPACE_MODE="private",
                AUCTIONRADAR_REGISTRATION_ENABLED="false",
                AUCTIONRADAR_DATA_DIR=str(state),
                AUCTIONRADAR_SECRET_FILE=str(session),
                AUCTIONRADAR_CREDENTIALS_KEY_FILE=str(key),
                AUCTIONRADAR_SESSION_SECURE="true",
                AUCTIONRADAR_TRUSTED_HOSTS="127.0.0.1",
                PYTHONDONTWRITEBYTECODE="1",
            )
            password = "synthetic-runtime-admin-password"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auctionradar.web.manage",
                    "create-user",
                    "--username",
                    "runtime-admin",
                    "--admin",
                    "--password-stdin",
                ],
                input=password + "\n",
                text=True,
                env=env,
                cwd=code,
                check=True,
                capture_output=True,
            )
            with socket.socket() as candidate:
                candidate.bind(("127.0.0.1", 0))
                port = candidate.getsockname()[1]
            base = f"https://127.0.0.1:{port}"
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(
                    context=ssl.create_default_context(cafile=str(tls_cert))
                ),
                urllib.request.HTTPCookieProcessor(jar),
            )
            log_path = root / "server.log"
            process = None

            def stop():
                nonlocal process
                if process is not None and process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
                process = None

            def start():
                nonlocal process
                with log_path.open("ab") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "gunicorn",
                            "--workers",
                            "2",
                            "--bind",
                            f"127.0.0.1:{port}",
                            "--certfile",
                            str(tls_cert),
                            "--keyfile",
                            str(tls_key),
                            "--logger-class",
                            "auctionradar.web.gunicorn_logging.PrivateLogger",
                            "auctionradar.web:create_app()",
                        ],
                        cwd=code,
                        env=env,
                        stdout=log,
                        stderr=log,
                        start_new_session=True,
                    )
                for _ in range(100):
                    if process.poll() is not None:
                        self.fail("Gunicorn could not start: " + log_path.read_text()[-1500:])
                    try:
                        with opener.open(base + "/healthz", timeout=1) as response:
                            if response.status == 200:
                                return
                    except (urllib.error.URLError, TimeoutError):
                        pass
                    time.sleep(0.05)
                self.fail("TLS runtime did not become healthy")

            def get(path):
                with opener.open(base + path, timeout=10) as response:
                    return response.read().decode()

            def csrf(html):
                match = re.search(
                    r'name="(?:csrf-token|csrf_token)" (?:content|value)="([^"]+)"', html
                )
                self.assertIsNotNone(match)
                return match.group(1)

            def post(path, data, content_type, token):
                request = urllib.request.Request(
                    base + path,
                    data=data,
                    headers={
                        "Content-Type": content_type,
                        "X-CSRFToken": token,
                        "Referer": base + "/",
                    },
                )
                with opener.open(request, timeout=20) as response:
                    return response.read().decode()

            try:
                start()
                with self.assertRaises(urllib.error.HTTPError) as disabled:
                    get("/register")
                self.assertEqual(disabled.exception.code, 404)
                token = csrf(get("/login"))
                html = post(
                    "/login",
                    urllib.parse.urlencode(
                        {"username": "runtime-admin", "password": password, "csrf_token": token}
                    ).encode(),
                    "application/x-www-form-urlencoded",
                    token,
                )
                token = csrf(html)
                me = json.loads(get("/api/me"))
                self.assertTrue(me["user"]["is_admin"])
                self.assertTrue(me["user"]["is_workspace_owner"])
                self.assertEqual(json.loads(get("/api/sources"))["sources"], [])
                cookie = next(cookie for cookie in jar if cookie.name == "session")
                self.assertTrue(cookie.secure)
                self.assertTrue(cookie.has_nonstandard_attr("HttpOnly"))

                fixtures = root / "certificate-fixture"
                fixtures.mkdir()
                phrase = b"synthetic-runtime-certificate-pass"
                p12 = make_synthetic_pkcs12(fixtures, phrase)
                boundary = "auctionradar-synthetic-upload"
                pieces = []
                for name, value in [("passphrase", phrase), ("consent", b"yes")]:
                    pieces.append(
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
                        + value
                        + b"\r\n"
                    )
                pieces.append(
                    f'--{boundary}\r\nContent-Disposition: form-data; name="certificate"; filename="synthetic.p12"\r\nContent-Type: application/x-pkcs12\r\n\r\n'.encode()
                    + p12
                    + b"\r\n"
                )
                pieces.append(f"--{boundary}--\r\n".encode())
                saved = json.loads(
                    post(
                        "/api/connections/certificate",
                        b"".join(pieces),
                        f"multipart/form-data; boundary={boundary}",
                        token,
                    )
                )
                self.assertTrue(saved["configured"])
                self.assertTrue(saved["enabled"])
                ciphertext = (
                    state / "workspaces" / str(me["user"]["id"]) / "credentials/boe-identity.v1.enc"
                )
                self.assertTrue(ciphertext.is_file())
                self.assertNotIn(phrase, ciphertext.read_bytes())
                self.assertNotIn(p12, ciphertext.read_bytes())
                self.assertEqual(ciphertext.stat().st_mode & 0o777, 0o600)
                self.assertNotIn(phrase.decode(), get("/api/connections/certificate"))

                stop()
                env["AUCTIONRADAR_REGISTRATION_ENABLED"] = "true"
                start()
                # A persistent shared signing key works across both workers and restarts.
                self.assertEqual(json.loads(get("/api/me"))["user"]["username"], "runtime-admin")
                anonymous_jar = http.cookiejar.CookieJar()
                opener = urllib.request.build_opener(
                    urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(
                        context=ssl.create_default_context(cafile=str(tls_cert))
                    ),
                    urllib.request.HTTPCookieProcessor(anonymous_jar),
                )
                token = csrf(get("/register"))
                post(
                    "/register",
                    urllib.parse.urlencode(
                        {
                            "username": "runtime-visitor",
                            "password": "synthetic-runtime-visitor-password",
                            "csrf_token": token,
                        }
                    ).encode(),
                    "application/x-www-form-urlencoded",
                    token,
                )
                self.assertFalse(json.loads(get("/api/me"))["user"]["is_admin"])
                self.assertFalse(json.loads(get("/api/connections/certificate"))["configured"])
                with self.assertRaises(urllib.error.HTTPError) as forbidden:
                    get("/api/users")
                self.assertEqual(forbidden.exception.code, 403)
            finally:
                stop()
