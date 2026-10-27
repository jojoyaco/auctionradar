"""Encrypted personal-certificate storage tests using synthetic identities."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from auctionradar.web import credential_store as store


def make_synthetic_pkcs12(
    _directory: Path | None = None,
    passphrase: bytes = b"synthetic-test-pass",
    *,
    validity: str = "valid",
) -> bytes:
    """Create a test-only identity in memory; no private PEM or P12 hits disk."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    certificate_key = key
    now = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    if validity == "expired":
        not_before, not_after = now - timedelta(days=45), now - timedelta(days=1)
    elif validity == "future":
        not_before, not_after = now + timedelta(days=1), now + timedelta(days=31)
    else:
        not_before, not_after = now - timedelta(days=1), now + timedelta(days=30)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "synthetic-test-only.invalid")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(certificate_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(certificate_key, hashes.SHA256())
    )
    return serialization.pkcs12.serialize_key_and_certificates(
        b"synthetic-test-only",
        key,
        certificate,
        None,
        serialization.BestAvailableEncryption(passphrase),
    )


class CredentialStoreTests(unittest.TestCase):
    def test_concurrent_delete_cannot_be_undone_by_a_stale_consent_toggle(self):
        store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=True)
        read = threading.Event()
        release = threading.Event()
        load = store.load_identity

        def paused_load(*args, **kwargs):
            identity = load(*args, **kwargs)
            read.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test synchronization timeout")
            return identity

        with ThreadPoolExecutor(max_workers=2) as pool:
            with patch.object(store, "load_identity", side_effect=paused_load):
                toggled = pool.submit(store.set_enabled, self.workspace_a, False)
                self.assertTrue(read.wait(timeout=5))
                removed = pool.submit(store.delete_identity, self.workspace_a)
                release.set()
                toggled.result(timeout=5)
                removed.result(timeout=5)
        self.assertFalse(store.identity_status(self.workspace_a)["configured"])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="auctionradar-credential-tests-")
        self.root = Path(self.temp.name)
        self.workspace_a = self.root / "account-a"
        self.workspace_b = self.root / "account-b"
        self.workspace_a.mkdir(mode=0o700)
        self.workspace_b.mkdir(mode=0o700)
        self.key_file = self.root / "credential-key.bin"
        self.key_file.write_bytes(os.urandom(32))
        self.key_file.chmod(0o600)
        self.passphrase = b"synthetic-test-pass"
        self.p12 = make_synthetic_pkcs12(self.root, self.passphrase)
        self.env = patch.dict(os.environ, {store.KEY_ENV_NAME: str(self.key_file)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_encrypts_at_rest_and_loads_only_for_workspace_owner(self):
        result = store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=True)
        self.assertTrue(result["configured"])
        self.assertTrue(result["enabled"])
        credential_dir = self.workspace_a / "credentials"
        encrypted = credential_dir / store.ENVELOPE_NAME
        self.assertEqual(credential_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(encrypted.stat().st_mode & 0o777, 0o600)
        saved = encrypted.read_bytes()
        self.assertNotIn(self.p12, saved)
        self.assertNotIn(self.passphrase, saved)
        self.assertEqual(
            store.identity_status(self.workspace_a)["expires_at"], result["expires_at"]
        )
        identity = store.load_identity(self.workspace_a)
        self.assertEqual(identity.p12, self.p12)
        self.assertEqual(identity.passphrase, self.passphrase)
        self.assertEqual(repr(identity), "BoeIdentity(<redacted>)")

        self.workspace_b.joinpath("credentials").mkdir(mode=0o700)
        copied = self.workspace_b / "credentials" / store.ENVELOPE_NAME
        shutil.copyfile(encrypted, copied)
        copied.chmod(0o600)
        with self.assertRaises(store.CredentialStoreUnavailable):
            store.load_identity(self.workspace_b)

    def test_wrong_passphrase_and_invalid_p12_have_one_generic_error(self):
        for payload, password in ((self.p12, b"wrong-passphrase"), (b"not-a-p12", self.passphrase)):
            with self.subTest(payload=payload[:3]):
                with self.assertRaises(store.InvalidCredential) as error:
                    store.validate_pkcs12(payload, password)
                self.assertEqual(error.exception.code, "invalid_certificate")

    def test_expired_future_and_mismatched_identities_are_rejected(self):
        payloads = (
            make_synthetic_pkcs12(self.root, self.passphrase, validity="expired"),
            make_synthetic_pkcs12(self.root, self.passphrase, validity="future"),
        )
        for payload in payloads:
            with (
                self.subTest(payload_size=len(payload)),
                self.assertRaises(store.InvalidCredential),
            ):
                store.validate_pkcs12(payload, self.passphrase)

        _matching_key, certificate, _additional = serialization.pkcs12.load_key_and_certificates(
            self.p12, self.passphrase
        )
        different_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with patch.object(
            store.pkcs12,
            "load_key_and_certificates",
            return_value=(different_key, certificate, []),
        ):
            with self.assertRaises(store.InvalidCredential):
                store.validate_pkcs12(self.p12, self.passphrase)

    def test_expired_identity_is_revalidated_and_rejected_on_use(self):
        expired = make_synthetic_pkcs12(self.root, self.passphrase, validity="expired")
        _key, certificate, _additional = serialization.pkcs12.load_key_and_certificates(
            expired, self.passphrase
        )
        expires_at = certificate.not_valid_after_utc.isoformat().replace("+00:00", "Z")
        # Simulate an older encrypted envelope whose upload validation
        # predates expiry checks. Use-time parsing must still reject it.
        with patch.object(store, "validate_pkcs12", return_value=expires_at):
            store.save_identity(self.workspace_a, expired, self.passphrase, enabled=True)
        with self.assertRaises(store.InvalidCredential):
            store.load_identity(self.workspace_a)

    def test_missing_and_insecure_key_files_fail_closed(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(store.KEY_ENV_NAME, None)
            with self.assertRaises(store.CredentialStoreUnavailable):
                store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=True)

        insecure = self.root / "insecure-key.bin"
        insecure.write_bytes(os.urandom(32))
        insecure.chmod(0o644)
        with patch.dict(os.environ, {store.KEY_ENV_NAME: str(insecure)}):
            with self.assertRaises(store.CredentialStoreUnavailable):
                store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=True)

    def test_key_symlinks_and_invalid_passphrases_are_rejected(self):
        linked = self.root / "key-link"
        linked.symlink_to(self.key_file)
        with self.assertRaises(store.CredentialStoreUnavailable):
            store.save_identity(
                self.workspace_a, self.p12, self.passphrase, enabled=True, key_file=linked
            )
        with self.assertRaises(store.InvalidCredential):
            store.save_identity(self.workspace_a, self.p12, b"line\nbreak", enabled=True)

    def test_disable_requires_decryption_and_delete_revokes(self):
        store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=True)
        disabled = store.set_enabled(self.workspace_a, False)
        self.assertFalse(disabled["enabled"])
        with self.assertRaises(store.CredentialDisabled):
            store.load_identity(self.workspace_a)
        self.assertEqual(store.load_identity(self.workspace_a, require_enabled=False).p12, self.p12)
        enabled = store.set_enabled(self.workspace_a, True)
        self.assertTrue(enabled["enabled"])
        store.delete_identity(self.workspace_a)
        self.assertEqual(store.identity_status(self.workspace_a)["configured"], False)
        with self.assertRaises(store.CredentialNotFound):
            store.delete_identity(self.workspace_a)

    def test_expiration_is_status_metadata_only(self):
        result = store.save_identity(self.workspace_a, self.p12, self.passphrase, enabled=False)
        status = store.identity_status(self.workspace_a)
        self.assertEqual(set(status), {"configured", "enabled", "expires_at", "key_available"})
        self.assertFalse(status["enabled"])
        self.assertEqual(status["expires_at"], result["expires_at"])
        self.assertNotIn("passphrase", json.dumps(status))
        self.assertNotIn("p12", json.dumps(status))

    def test_non_object_envelope_fails_closed(self):
        credential_dir = self.workspace_a / "credentials"
        credential_dir.mkdir(mode=0o700)
        envelope = credential_dir / store.ENVELOPE_NAME
        envelope.write_text("[]", encoding="utf-8")
        envelope.chmod(0o600)
        with self.assertRaises(store.CredentialStoreUnavailable):
            store.identity_status(self.workspace_a)


if __name__ == "__main__":
    unittest.main()
