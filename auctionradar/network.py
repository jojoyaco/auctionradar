"""Bounded, public-Internet-only HTTPS transport for user-configured sources.

Resolve and validate *every* hop, then pin curl to that validated address while
retaining TLS hostname verification. Never let curl follow a redirect or use an
environment proxy: either would bypass the address check. The legacy CLI keeps
its original transport; the private product opts into this one.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urljoin, urlsplit, urlunsplit


class PublicNetworkError(ValueError):
    """A destination is not safe for a server-side public-source request."""


_SENSITIVE_QUERY = re.compile(
    r"password|passwd|secret|authorization|token|"
    r"cookie|session.?id|csrf|api.?key|signature|credential",
    re.I,
)
_TRANSLATION_PREFIXES = (
    ipaddress.ip_network("64:ff9b::/96"),
    ipaddress.ip_network("64:ff9b:1::/48"),
)
_DNS_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="radar-public-dns")
_DNS_SLOTS = threading.BoundedSemaphore(4)


def _lookup(host: str, timeout: float):
    # getaddrinfo has no timeout argument. Bound caller latency *and* the number
    # of outstanding lookups; timed-out calls keep their slot until the system
    # resolver actually returns. Never accumulate an unbounded executor queue.
    if timeout <= 0 or not _DNS_SLOTS.acquire(blocking=False):
        raise PublicNetworkError("public_dns_busy")
    try:
        future = _DNS_POOL.submit(socket.getaddrinfo, host, 443, type=socket.SOCK_STREAM)
    except Exception:
        _DNS_SLOTS.release()
        raise
    future.add_done_callback(lambda _: _DNS_SLOTS.release())
    try:
        return future.result(timeout=min(timeout, 5.0))
    except FutureTimeout as exc:
        raise PublicNetworkError("public_dns_timeout") from exc


@lru_cache(maxsize=1)
def _check_curl() -> None:
    # Before 8.4, curl's size cap did not stop unknown-length downloads while
    # transferring. The post-read check alone would not bound temporary disk.
    try:
        process = subprocess.run(["curl", "-q", "--version"], capture_output=True, timeout=2)
        version = re.match(rb"curl (\d+)\.(\d+)\.(\d+)", process.stdout)
        if process.returncode or not version:
            raise PublicNetworkError("curl_version_unavailable")
        if tuple(int(part) for part in version.groups()) < (8, 4, 0):
            raise PublicNetworkError("curl_8_4_or_newer_required")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PublicNetworkError("curl_version_unavailable") from exc


def validate_url(value: str) -> str:
    """Validate syntax without network I/O; credentials are never accepted."""
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise PublicNetworkError("invalid_public_url")
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise PublicNetworkError("invalid_public_url")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (
            parsed.scheme != "https"
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in (None, 443)
        ):
            raise PublicNetworkError("https_public_url_required")
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError) as exc:
        raise PublicNetworkError("invalid_public_url") from exc
    if not host or "%" in host or any(c in host for c in "/\\@?#"):
        raise PublicNetworkError("invalid_public_host")
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".home.arpa")):
        raise PublicNetworkError("non_public_destination")
    if any(_SENSITIVE_QUERY.search(unquote(key)) for key, _ in parse_qsl(parsed.query)):
        raise PublicNetworkError("credentials_in_url")
    netloc = "[" + host + "]" if ":" in host else host
    return urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))


def _public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not address.is_global or address.is_multicast or address.is_unspecified:
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return _public_address(str(address.ipv4_mapped))
        if address.sixtofour is not None or address.teredo is not None:
            return False
        if any(address in network for network in _TRANSLATION_PREFIXES):
            return False
    return True


def resolve_public(value: str, *, timeout: float = 5.0) -> tuple[str, str, list[str]]:
    """Reject a hostname if *any* DNS answer is not public, including IPv6."""
    url = validate_url(value)
    host = urlsplit(url).hostname
    try:
        answers = _lookup(host, timeout)
    except OSError as exc:
        raise PublicNetworkError("public_dns_unavailable") from exc
    addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
    if not addresses or len(addresses) > 32 or not all(_public_address(a) for a in addresses):
        raise PublicNetworkError("non_public_destination")
    return url, host, addresses


def fetch_public(
    url: str, *, timeout: float, max_body: int, user_agent: str, request_body: bytes | None = None
) -> dict:
    """Return public response bytes and metadata, never raw response headers.

    Redirects are individually resolved/pinned. A POST is not replayed to a
    different origin. Error messages intentionally omit peer-controlled text.
    """
    result = {
        "http_status": None,
        "content_type": None,
        "final_url": None,
        "body_bytes": b"",
        "error": None,
        "truncated": False,
    }
    deadline = time.monotonic() + max(0.1, min(float(timeout), 120.0))
    current = url
    try:
        _check_curl()
        with tempfile.TemporaryDirectory(prefix="radar-public-") as directory:
            target = Path(directory) / "body"
            for hop in range(5):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PublicNetworkError("public_request_timeout")
                current, host, addresses = resolve_public(current, timeout=remaining)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PublicNetworkError("public_request_timeout")
                address = addresses[0]
                pinned = "[" + address + "]" if ":" in address else address
                resolve_host = "[" + host + "]" if ":" in host else host
                command = [
                    "curl",
                    "-q",
                    "--silent",
                    "--show-error",
                    "--globoff",
                    "--proto",
                    "=https",
                    "--proto-redir",
                    "=https",
                    "--noproxy",
                    "*",
                    "--proxy",
                    "",
                    "--max-redirs",
                    "0",
                    "--disallow-username-in-url",
                    "--connect-timeout",
                    str(min(remaining, 6)),
                    "--max-time",
                    str(remaining),
                    "--max-filesize",
                    str(max_body),
                    "--user-agent",
                    user_agent,
                    "--output",
                    str(target),
                    "--resolve",
                    f"{resolve_host}:443:{pinned}",
                    "--write-out",
                    "%{http_code}\n%{content_type}\n%{redirect_url}\n%{remote_ip}",
                    "--url",
                    current,
                ]
                if request_body is not None:
                    command.extend(
                        ["--header", "Content-Type: application/json", "--data-binary", "@-"]
                    )
                target.unlink(missing_ok=True)
                proc = subprocess.run(
                    command, input=request_body, capture_output=True, timeout=remaining + 1
                )
                metadata = proc.stdout.decode("utf-8", "replace").split("\n")
                if len(metadata) < 4 or not metadata[0].isdigit():
                    raise PublicNetworkError("public_transport_error")
                status = int(metadata[0]) or None
                result.update(
                    http_status=status, content_type=metadata[1] or None, final_url=current
                )
                if proc.returncode:
                    result["truncated"] = proc.returncode == 63
                    raise PublicNetworkError(
                        "public_response_too_large"
                        if proc.returncode == 63
                        else "public_transport_error"
                    )
                # Defence in depth: curl must have connected to the address pinned above.
                try:
                    connected = ipaddress.ip_address(metadata[3])
                except ValueError as exc:
                    raise PublicNetworkError("public_transport_error") from exc
                if connected != ipaddress.ip_address(address):
                    raise PublicNetworkError("public_peer_mismatch")
                if status in (301, 302, 303, 307, 308) and metadata[2]:
                    if hop == 4:
                        raise PublicNetworkError("public_redirect_limit")
                    following = validate_url(urljoin(current, metadata[2]))
                    if request_body is not None:
                        if urlsplit(following).netloc != urlsplit(current).netloc:
                            raise PublicNetworkError("cross_origin_post_redirect")
                        if status in (301, 302, 303):
                            request_body = None
                    current = following
                    continue
                with target.open("rb") as handle:
                    body = handle.read(max_body + 1)
                if len(body) > max_body:
                    result["truncated"] = True
                    raise PublicNetworkError("public_response_too_large")
                result["body_bytes"] = body
                return result
    except PublicNetworkError as exc:
        result["error"] = str(exc)
    except subprocess.TimeoutExpired:
        result["error"] = "public_request_timeout"
    except OSError:
        result["error"] = "public_transport_error"
    return result
