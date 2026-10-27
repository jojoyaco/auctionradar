"""Per-workspace encrypted storage for optional BOE personal certificates.

The encryption key is loaded from ``AUCTIONRADAR_CREDENTIALS_KEY_FILE`` and is never
derived from Flask's session key. The only persisted credential payload is
AES-GCM ciphertext; plaintext is returned only to an explicit in-process
worker caller and is never exposed by an HTTP endpoint.
"""

from __future__ import annotations

import base64
import fcntl
import json
import os
import re
import secrets
import stat
import uuid
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Optional

from cryptography.exceptions import InvalidTag, UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.serialization import pkcs12

MAX_P12_BYTES = 128 * 1024
MAX_PASSPHRASE_BYTES = 256
MAX_ENVELOPE_BYTES = 220 * 1024
ENVELOPE_NAME = "boe-identity.v1.enc"
KEY_ENV_NAME = "AUCTIONRADAR_CREDENTIALS_KEY_FILE"


class CredentialStoreError(Exception):
    """Base error with a stable, non-sensitive public code."""

    code = "credential_store_failed"

    def __init__(self, code: Optional[str] = None) -> None:
        self.code = code or type(self).code
        super().__init__(self.code)


class CredentialStoreUnavailable(CredentialStoreError):
    code = "credential_store_unavailable"


class CredentialNotFound(CredentialStoreError):
    code = "credential_not_found"


class InvalidCredential(CredentialStoreError):
    code = "invalid_certificate"


class CredentialDisabled(CredentialStoreError):
    code = "certificate_disabled"


class BoeIdentity:
    """Transient decrypted identity. Its representation deliberately redacts."""

    __slots__ = ("p12", "passphrase", "expires_at")

    def __init__(self, p12: bytes, passphrase: bytes, expires_at: str) -> None:
        self.p12 = p12
        self.passphrase = passphrase
        self.expires_at = expires_at

    def __repr__(self) -> str:
        return "BoeIdentity(<redacted>)"


def _workspace_root(workspace: os.PathLike[str] | str) -> Path:
    root = Path(workspace)
    try:
        if root.is_symlink() or not root.is_dir():
            raise CredentialStoreUnavailable()
        resolved = root.resolve(strict=True)
        if resolved.stat().st_uid != os.geteuid():
            raise CredentialStoreUnavailable()
        return resolved
    except (OSError, RuntimeError):
        raise CredentialStoreUnavailable() from None


def _credentials_dir(workspace: os.PathLike[str] | str, *, create: bool) -> Path:
    root = _workspace_root(workspace)
    directory = root / "credentials"
    try:
        if directory.is_symlink():
            raise CredentialStoreUnavailable()
        if not create and not directory.exists():
            raise CredentialNotFound()
        if create:
            directory.mkdir(mode=0o700, exist_ok=True)
        if not directory.is_dir() or directory.stat().st_uid != os.geteuid():
            raise CredentialStoreUnavailable()
        os.chmod(directory, 0o700)
        return directory
    except (OSError, RuntimeError):
        raise CredentialStoreUnavailable() from None


def _serialized_mutation(function):
    """Order replace/consent/delete across workers; a stale toggle cannot
    resurrect an identity concurrently removed by its owner."""

    @wraps(function)
    def wrapped(workspace, *args, **kwargs):
        root = _workspace_root(workspace)
        try:
            descriptor = os.open(
                root / ".credentials.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
        except OSError as exc:
            raise CredentialStoreUnavailable() from exc
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or info.st_mode & 0o077
            ):
                raise CredentialStoreUnavailable()
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            return function(root, *args, **kwargs)
        finally:
            os.close(descriptor)

    return wrapped


def _key_file(path: Optional[os.PathLike[str] | str] = None) -> Path:
    configured = path if path is not None else os.environ.get(KEY_ENV_NAME)
    if not configured:
        raise CredentialStoreUnavailable()
    candidate = Path(configured)
    try:
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size != 32
            ):
                raise CredentialStoreUnavailable()
        finally:
            os.close(descriptor)
        resolved = candidate.resolve(strict=True)
        project_root = Path(__file__).resolve().parents[2]
        try:
            resolved.relative_to(project_root)
        except ValueError:
            return resolved
        raise CredentialStoreUnavailable()
    except CredentialStoreUnavailable:
        raise
    except (OSError, RuntimeError):
        raise CredentialStoreUnavailable() from None


def _read_limited(path: Path, limit: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or not 0 < info.st_size <= limit
        ):
            raise CredentialStoreUnavailable()
        data = os.read(descriptor, limit + 1)
        if len(data) > limit:
            raise CredentialStoreUnavailable()
        return data
    finally:
        os.close(descriptor)


def _atomic_write(path: Path, data: bytes) -> None:
    directory = path.parent
    temporary = directory / ("." + path.name + "." + secrets.token_hex(12))
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _load_valid_pkcs12(p12: bytes, passphrase: bytes):
    """Parse and validate the primary PKCS#12 key/certificate identity."""
    if (
        not isinstance(p12, bytes)
        or not 0 < len(p12) <= MAX_P12_BYTES
        or not isinstance(passphrase, bytes)
        or not 0 < len(passphrase) <= MAX_PASSPHRASE_BYTES
        or b"\x00" in passphrase
        or b"\n" in passphrase
        or b"\r" in passphrase
    ):
        raise InvalidCredential()

    try:
        private_key, certificate, additional_certificates = pkcs12.load_key_and_certificates(
            p12, passphrase
        )
        if private_key is None or certificate is None:
            raise InvalidCredential()

        key_public = private_key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        certificate_public = certificate.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if key_public != certificate_public:
            raise InvalidCredential()

        not_before = getattr(certificate, "not_valid_before_utc", None)
        not_after = getattr(certificate, "not_valid_after_utc", None)
        if not_before is None:
            not_before = certificate.not_valid_before.replace(tzinfo=timezone.utc)
        if not_after is None:
            not_after = certificate.not_valid_after.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if not_before is None or not_after is None or not_before > now or not_after <= now:
            raise InvalidCredential()
        return private_key, certificate, tuple(additional_certificates or ()), not_after
    except InvalidCredential:
        raise
    except (ValueError, TypeError, AttributeError, UnsupportedAlgorithm):
        raise InvalidCredential() from None


def _expiry_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_pkcs12(p12: bytes, passphrase: bytes) -> str:
    """Validate a paired, currently valid PKCS#12 identity; return its expiry."""
    _private_key, _certificate, _additional, not_after = _load_valid_pkcs12(p12, passphrase)
    return _expiry_text(not_after)


def pkcs12_tls_pem(p12: bytes, passphrase: bytes) -> bytes:
    """Serialize a validated identity for immediate in-memory TLS setup only."""
    try:
        private_key, certificate, additional_certificates, _not_after = _load_valid_pkcs12(
            p12, passphrase
        )
        key_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return (
            key_pem
            + certificate.public_bytes(serialization.Encoding.PEM)
            + b"".join(
                extra.public_bytes(serialization.Encoding.PEM) for extra in additional_certificates
            )
        )
    except InvalidCredential:
        raise
    except (ValueError, TypeError, AttributeError, UnsupportedAlgorithm):
        raise InvalidCredential() from None


def _aad(workspace: Path, envelope: dict[str, Any]) -> bytes:
    return (
        f"auctionradar-boe-p12:v1:{workspace}:{envelope['credential_id']}:{int(envelope['enabled'])}:"
        f"{envelope['expires_at']}"
    ).encode("utf-8")


@_serialized_mutation
def save_identity(
    workspace: os.PathLike[str] | str,
    p12: bytes,
    passphrase: bytes,
    *,
    enabled: bool,
    key_file: Optional[os.PathLike[str] | str] = None,
) -> dict[str, Any]:
    """Validate and atomically encrypt a user's BOE identity."""
    if not isinstance(enabled, bool):
        raise InvalidCredential()
    root = _workspace_root(workspace)
    key_path = _key_file(key_file)
    key = _read_key(key_path)
    expires_at = validate_pkcs12(p12, passphrase)
    credential_id = str(uuid.uuid4())
    nonce = secrets.token_bytes(12)
    plaintext = bytearray(
        json.dumps(
            {
                "p12": base64.b64encode(p12).decode("ascii"),
                "passphrase": base64.b64encode(passphrase).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode("ascii")
    )
    envelope = {
        "version": 1,
        "credential_id": credential_id,
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "enabled": enabled,
        "expires_at": expires_at,
    }
    try:
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, _aad(root, envelope))
    finally:
        plaintext[:] = b"\x00" * len(plaintext)
    envelope["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    encoded = json.dumps(envelope, separators=(",", ":")).encode("ascii")
    if len(encoded) > MAX_ENVELOPE_BYTES:
        raise InvalidCredential()
    directory = _credentials_dir(root, create=True)
    _atomic_write(directory / ENVELOPE_NAME, encoded)
    return {"configured": True, "enabled": enabled, "expires_at": expires_at}


def _read_key(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size != 32
            ):
                raise CredentialStoreUnavailable()
            key = os.read(descriptor, 33)
            if len(key) != 32:
                raise CredentialStoreUnavailable()
            return key
        finally:
            os.close(descriptor)
    except CredentialStoreUnavailable:
        raise
    except OSError:
        raise CredentialStoreUnavailable() from None


def _read_envelope(workspace: os.PathLike[str] | str) -> tuple[Path, dict[str, Any]]:
    root = _workspace_root(workspace)
    path = _credentials_dir(root, create=False) / ENVELOPE_NAME
    try:
        raw = _read_limited(path, MAX_ENVELOPE_BYTES)
        envelope = json.loads(raw)
        if not isinstance(envelope, dict):
            raise CredentialStoreUnavailable()
        try:
            uuid.UUID(envelope.get("credential_id", ""))
        except (ValueError, TypeError, AttributeError):
            raise CredentialStoreUnavailable()
        if (
            not isinstance(envelope, dict)
            or set(envelope)
            != {"version", "credential_id", "nonce", "ciphertext", "enabled", "expires_at"}
            or envelope.get("version") != 1
            or not isinstance(envelope.get("credential_id"), str)
            or not re.fullmatch(r"[0-9a-f-]{36}", envelope["credential_id"])
            or not isinstance(envelope.get("enabled"), bool)
            or not isinstance(envelope.get("expires_at"), str)
        ):
            raise CredentialStoreUnavailable()
        return root, envelope
    except FileNotFoundError:
        raise CredentialNotFound() from None
    except CredentialStoreError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise CredentialStoreUnavailable() from None


def identity_status(workspace: os.PathLike[str] | str) -> dict[str, Any]:
    """Return safe status metadata only; no credential or key material."""
    try:
        _, envelope = _read_envelope(workspace)
    except CredentialNotFound:
        return {
            "configured": False,
            "enabled": False,
            "expires_at": None,
            "key_available": _key_available(),
        }
    return {
        "configured": True,
        "enabled": envelope["enabled"],
        "expires_at": envelope["expires_at"],
        "key_available": _key_available(),
    }


def _key_available() -> bool:
    try:
        path = _key_file()
        _read_key(path)
        return True
    except CredentialStoreUnavailable:
        return False


def load_identity(
    workspace: os.PathLike[str] | str,
    *,
    require_enabled: bool = True,
    key_file: Optional[os.PathLike[str] | str] = None,
) -> BoeIdentity:
    """Decrypt one identity for a caller already scoped to this workspace."""
    root, envelope = _read_envelope(workspace)
    if require_enabled and not envelope["enabled"]:
        raise CredentialDisabled()
    path = _key_file(key_file)
    key = _read_key(path)
    try:
        nonce = base64.b64decode(envelope["nonce"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        if len(nonce) != 12 or not 16 < len(ciphertext) <= MAX_P12_BYTES * 2:
            raise CredentialStoreUnavailable()
        plaintext = bytearray(AESGCM(key).decrypt(nonce, ciphertext, _aad(root, envelope)))
    except (ValueError, InvalidTag, TypeError):
        raise CredentialStoreUnavailable() from None
    try:
        payload = json.loads(plaintext)
        if not isinstance(payload, dict) or set(payload) != {"p12", "passphrase"}:
            raise CredentialStoreUnavailable()
        p12 = bytearray(base64.b64decode(payload["p12"], validate=True))
        passphrase = bytearray(base64.b64decode(payload["passphrase"], validate=True))
        if not 0 < len(p12) <= MAX_P12_BYTES or not 0 < len(passphrase) <= MAX_PASSPHRASE_BYTES:
            raise CredentialStoreUnavailable()
        actual_expiry = validate_pkcs12(bytes(p12), bytes(passphrase))
        if actual_expiry != envelope["expires_at"]:
            raise CredentialStoreUnavailable()
        return BoeIdentity(bytes(p12), bytes(passphrase), actual_expiry)
    except CredentialStoreError:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise CredentialStoreUnavailable() from None
    finally:
        plaintext[:] = b"\x00" * len(plaintext)
        if "p12" in locals() and isinstance(p12, bytearray):
            p12[:] = b"\x00" * len(p12)
        if "passphrase" in locals() and isinstance(passphrase, bytearray):
            passphrase[:] = b"\x00" * len(passphrase)


@_serialized_mutation
def set_enabled(
    workspace: os.PathLike[str] | str,
    enabled: bool,
) -> dict[str, Any]:
    """Revoke or explicitly re-consent to use of an already stored identity."""
    if not isinstance(enabled, bool):
        raise InvalidCredential()
    root, envelope = _read_envelope(workspace)
    # Re-authenticate and bind the mutable consent state into the new AEAD tag.
    identity = load_identity(root, require_enabled=False)
    try:
        envelope["enabled"] = enabled
        nonce = secrets.token_bytes(12)
        key = _read_key(_key_file())
        plaintext = bytearray(
            json.dumps(
                {
                    "p12": base64.b64encode(identity.p12).decode("ascii"),
                    "passphrase": base64.b64encode(identity.passphrase).decode("ascii"),
                },
                separators=(",", ":"),
            ).encode("ascii")
        )
        try:
            encrypted = AESGCM(key).encrypt(nonce, plaintext, _aad(root, envelope))
        finally:
            plaintext[:] = b"\x00" * len(plaintext)
        envelope["nonce"] = base64.b64encode(nonce).decode("ascii")
        envelope["ciphertext"] = base64.b64encode(encrypted).decode("ascii")
        directory = _credentials_dir(root, create=False)
        encoded = json.dumps(envelope, separators=(",", ":")).encode("ascii")
        _atomic_write(directory / ENVELOPE_NAME, encoded)
        return {"configured": True, "enabled": enabled, "expires_at": envelope["expires_at"]}
    finally:
        identity.p12 = b""
        identity.passphrase = b""


@_serialized_mutation
def delete_identity(workspace: os.PathLike[str] | str) -> None:
    """Delete this workspace's encrypted credential, without following links."""
    directory = _credentials_dir(workspace, create=False)
    path = directory / ENVELOPE_NAME
    try:
        path.unlink()
    except FileNotFoundError:
        raise CredentialNotFound() from None
    except OSError:
        raise CredentialStoreUnavailable() from None
    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
