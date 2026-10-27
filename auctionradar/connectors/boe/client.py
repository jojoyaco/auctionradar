"""Authenticated BOE asset access (mTLS + session cookies).

Boundary: cert/passphrase only from a parent-supplied directory; PEM
in-memory only; ``SSLKEYLOGFILE`` purged; foreign origins refused;
errors expose only :data:`ERR_CODES` codes.

Public: :class:`BoeAccessError`, :func:`load_client_context`,
:class:`BoeClient`, :meth:`BoeClient.iter_asset_photos`.
"""

from __future__ import annotations

import hashlib
import http.cookiejar
import os
import re
import resource
import ssl
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

TransportResponse = Tuple[int, Dict[str, str], bytes]


# Tunables — each one corresponds to a validation rule below.
MAX_P12_BYTES = 128 * 1024
MAX_PASS_BYTES = 256
MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024
MAX_LOGIN_REDIRECTS = 6
MAX_ASSET_REDIRECTS = 4
MAX_IMAGE_REDIRECTS = 2
DEFAULT_TIMEOUT = 20.0


ASSET_HOST = "subastas.boe.es"

# Path + host pairs the mTLS hand-off is allowed to touch.
AUTH_PATH_HOSTS: Dict[str, str] = {
    "/id/checkCert.php": ASSET_HOST,
    "/id/loginCert.php": ASSET_HOST,
    "/id/check-cert/index.php": ASSET_HOST,
    "/": "check-cert.subastas.boe.es",
}

ALLOWED_IMAGE_PATHS = (
    "/reg/verDocumento.php",
    "/reg/verThumbnail.php",
)
ALLOWED_ASSET_PATHS = (
    "/reg/detalleSubasta.php",
    "/detalleSubasta.php",
)
ALLOWED_ASSET_QUERY_KEYS = ("idSub", "idLote", "ver")

ID_SUB_RE = re.compile(r"SUB-[A-Z]+-\d{4}-[A-Z0-9]{1,40}\Z", re.I)
ID_LOTE_RE = re.compile(r"\d{1,5}\Z")
ID_DOC_RE = re.compile(r"[A-Za-z0-9._\-]{1,128}\Z")

LOGIN_MARKER_RE = re.compile(
    r"para\s+consultar\s+(?:la\s+)?informaci[oó]n\s+complementaria.{0,400}"
    r"iniciar\s+sesi[oó]n",
    re.I | re.S,
)


# Stable codes; consumers match on these, never on exception text.
ERR_CODES: Dict[str, str] = {
    "certfile_missing": "PKCS12/identity material is absent or unreadable.",
    "certfile_too_large": "PKCS12 exceeds the bounded size limit.",
    "passphrase_missing": "Passphrase file is absent or unreadable.",
    "passphrase_too_large": "Passphrase exceeds the bounded size limit.",
    "credential_decode_failed": "OpenSSL refused to decode the PKCS12 keystore.",
    "tls_context_failed": "ssl.SSLContext.load_cert_chain could not ingest the PEM.",
    "auth_origin_not_allowed": "Refusing an origin outside the BOE auth namespace.",
    "auth_redirect_blocked": "Authentication handshake tried to leave its scope.",
    "auth_redirect_limit": "Authentication redirect chain exceeded the cap.",
    "asset_origin_not_allowed": "Refusing an origin outside the BOE asset namespace.",
    "asset_redirect_blocked": "Asset GET tried to leave its scope.",
    "asset_redirect_limit": "Asset redirect chain exceeded the cap.",
    "asset_redirect_loop": "Asset GET visited the same path twice.",
    "html_too_large": "Auction detail HTML exceeded the bounded cap.",
    "image_oversize": "Image body exceeded MAX_IMAGE_BYTES.",
    "image_mime_rejected": "Response is not a recognised image (JPEG/PNG/WEBP/GIF).",
    "image_magic_rejected": "Image bytes do not match declared MIME.",
    "asset_login_required": "Auction page still requires authentication.",
    "asset_unparseable": "Auction page had no parseable Bien document anchors.",
    "multiple_assets_ambiguous": "Several Bien blocks visible, none identified.",
    "asset_doc_mismatch": "Thumbnail idDoc does not match any document idDoc.",
    "transport_failure": "HTTP transport failed before returning a status.",
    "tls_failure": "TLS handshake failed.",
    "unexpected_status": "Unexpected HTTP status from the BOE portal.",
    "invalid_argument": "Argument failed validation before being issued.",
}


class BoeAccessError(Exception):
    """Generic error surface — exposes only ``code``."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in ERR_CODES:
            code = "unexpected_status"
        super().__init__(ERR_CODES[code])
        self.code = code


def _bail(code: str) -> None:
    raise BoeAccessError(code)


def _memory_identity(pem_bytes: bytes) -> Tuple[str, Callable[[], None]]:
    """Linux only: never fall back to a PEM file on disk."""
    if not callable(getattr(os, "memfd_create", None)):
        _bail("tls_context_failed")
    descriptor = os.memfd_create("boe-identity", os.MFD_CLOEXEC)
    try:
        with os.fdopen(os.dup(descriptor), "wb") as memory:
            memory.write(pem_bytes)
            memory.flush()
        os.lseek(descriptor, 0, os.SEEK_SET)
    except Exception:
        os.close(descriptor)
        raise
    return f"/proc/self/fd/{descriptor}", lambda: os.close(descriptor)


def _bounded_credential(path, limit):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("credential_unreadable")
        return os.read(descriptor, limit + 1)
    finally:
        os.close(descriptor)


def _build_tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.keylog_filename = None
    if hasattr(ctx, "post_handshake_auth"):
        try:
            ctx.post_handshake_auth = True
        except AttributeError:
            pass
    return ctx


def _harden_process() -> None:
    os.environ.pop("SSLKEYLOGFILE", None)
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ValueError, OSError):
        pass


def load_client_context(credentials_dir: Path) -> ssl.SSLContext:
    """Return an :class:`ssl.SSLContext` carrying the BOE personal cert.

    ``credentials_dir`` must contain ``boe-identity.p12`` (≤
    :data:`MAX_P12_BYTES`) and ``boe-passphrase`` (≤
    :data:`MAX_PASS_BYTES`). The decrypted PEM never touches disk.
    """
    if not isinstance(credentials_dir, Path):
        credentials_dir = Path(credentials_dir)
    if not credentials_dir.is_dir():
        _bail("certfile_missing")
    _harden_process()

    p12_path = credentials_dir / "boe-identity.p12"
    pass_path = credentials_dir / "boe-passphrase"

    try:
        p12_bytes = _bounded_credential(p12_path, MAX_P12_BYTES)
    except OSError:
        _bail("certfile_missing")
    if not (0 < len(p12_bytes) <= MAX_P12_BYTES):
        _bail("certfile_too_large")

    try:
        pass_blob = _bounded_credential(pass_path, MAX_PASS_BYTES)
    except OSError:
        _bail("passphrase_missing")
    if not (0 < len(pass_blob) <= MAX_PASS_BYTES):
        _bail("passphrase_too_large")

    read_fd, write_fd = os.pipe()
    os.write(write_fd, pass_blob + b"\n")
    os.close(write_fd)

    try:
        try:
            decoded = subprocess.run(
                [
                    "openssl",
                    "pkcs12",
                    "-in",
                    str(p12_path),
                    "-passin",
                    "fd:" + str(read_fd),
                    "-noenc",
                ],
                pass_fds=(read_fd,),
                capture_output=True,
                timeout=20,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            _bail("credential_decode_failed")
        if decoded.returncode:
            _bail("credential_decode_failed")
    finally:
        try:
            os.close(read_fd)
        except OSError:
            pass

    loader_path, cleanup = _memory_identity(decoded.stdout)
    try:
        context = _build_tls_context()
        context.load_cert_chain(loader_path)
    except (ssl.SSLError, OSError, ValueError):
        _bail("tls_context_failed")
    finally:
        cleanup()
    return context


def _split(url: str) -> urllib.parse.SplitResult:
    return urllib.parse.urlsplit(url)


def _qs(parsed: urllib.parse.SplitResult) -> Dict[str, List[str]]:
    return urllib.parse.parse_qs(parsed.query, keep_blank_values=True)


def _is_https_clean(parsed: urllib.parse.SplitResult) -> bool:
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and port in (None, 443)
        and not parsed.username
        and not parsed.password
    )


def _auth_origin_ok(url: str) -> bool:
    """Allow only auth handshake URLs."""
    parsed = _split(url)
    if not _is_https_clean(parsed):
        return False
    if parsed.hostname == ASSET_HOST:
        return parsed.path in AUTH_PATH_HOSTS and AUTH_PATH_HOSTS[parsed.path] == ASSET_HOST
    if parsed.hostname == "check-cert.subastas.boe.es":
        return parsed.path == "/"
    return False


def _origin_ok(
    url: str,
    current_id_sub: str,
    *,
    paths: Tuple[str, ...],
    allowed_keys: set,
) -> bool:
    """Common filter for asset + image URLs."""
    parsed = _split(url)
    if not _is_https_clean(parsed) or parsed.hostname != ASSET_HOST:
        return False
    if parsed.path not in paths:
        return False
    qs = _qs(parsed)
    if any(len(values) != 1 for values in qs.values()):
        return False
    if set(qs) - allowed_keys:
        return False
    idsub = (qs.get("idSub") or [""])[0]
    if not ID_SUB_RE.match(idsub):
        return False
    if current_id_sub and idsub.upper() != current_id_sub.upper():
        return False
    return True


def _asset_origin_ok(url: str, current_id_sub: str) -> bool:
    return _origin_ok(
        url,
        current_id_sub,
        paths=ALLOWED_ASSET_PATHS,
        allowed_keys=set(ALLOWED_ASSET_QUERY_KEYS),
    )


def _image_origin_ok(url: str, current_id_sub: str) -> bool:
    if not _origin_ok(
        url,
        current_id_sub,
        paths=ALLOWED_IMAGE_PATHS,
        allowed_keys={"idSub", "idDoc"},
    ):
        return False
    return bool(ID_DOC_RE.match((_qs(_split(url)).get("idDoc") or [""])[0]))


def canonical_asset_page_url(asset_url: str) -> Optional[str]:
    """Public canonical auction page URL for ``asset_url``.

    Only ``idSub`` (and optionally ``idLote``) is preserved; every
    other query key — opaque document pointers, tokens, etc. — is
    dropped. Returns ``None`` outside the BOE asset namespace.
    """
    if not isinstance(asset_url, str):
        return None
    try:
        parsed = _split(asset_url)
    except ValueError:
        return None
    if not _is_https_clean(parsed) or parsed.hostname != ASSET_HOST:
        return None
    if parsed.path not in ALLOWED_ASSET_PATHS:
        return None
    qs = _qs(parsed)
    if len(qs.get("idSub", [])) != 1 or len(qs.get("idLote", [""])) != 1:
        return None
    idsub = (qs.get("idSub") or [""])[0]
    if not ID_SUB_RE.match(idsub):
        return None
    idlote = (qs.get("idLote") or [""])[0]
    if idlote and not ID_LOTE_RE.match(idlote):
        return None
    parts = [f"idSub={idsub}", "ver=3"]
    if idlote:
        parts.append(f"idLote={idlote}")
    return urllib.parse.urlunsplit(
        ("https", ASSET_HOST, "/reg/detalleSubasta.php", "&".join(parts), "")
    )


TransportFn = Callable[[str, bool, int, Optional[str]], "TransportResponse"]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Don't auto-follow 30x — :class:`BoeClient` walks redirects itself."""

    def redirect_request(self, *_args, **_kwargs) -> None:
        return None


_REQUEST_HEADERS = {
    "User-Agent": "AuctionRadar read-only BOE photo verification",
    "Accept": "text/html,image/*;q=0.9,*/*;q=0.5",
}


def _default_transport(
    url,
    authenticated,
    max_bytes,
    referer,
    *,
    jar,
    auth_ctx,
    plain_ctx,
    timeout,
):
    request = urllib.request.Request(url, headers=dict(_REQUEST_HEADERS))
    if referer:
        request.add_header("Referer", referer)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(
            context=auth_ctx if authenticated else plain_ctx,
        ),
        urllib.request.HTTPCookieProcessor(jar),
    )
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    try:
        return (
            response.code,
            dict(response.headers.items()),
            response.read(max_bytes + 1),
        )
    finally:
        try:
            response.close()
        except Exception:
            pass


class BoeClient:
    """One BOE auction session.

    The :class:`http.cookiejar.CookieJar` is in-memory; never persisted
    to disk or logs. The cert context is only attached to *authenticated*
    requests — image and HTML GETs flow through plain TLS.
    """

    def __init__(
        self,
        context: ssl.SSLContext,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Optional[TransportFn] = None,
    ) -> None:
        if not isinstance(context, ssl.SSLContext):
            _bail("tls_context_failed")
        self._auth_ctx = context
        self._plain_ctx = _build_tls_context()
        self._timeout = max(0.1, float(timeout))
        self._jar = http.cookiejar.CookieJar()
        self._transport = transport

    def _request(
        self,
        url: str,
        authenticated: bool,
        max_bytes: int,
        referer: Optional[str] = None,
    ) -> "TransportResponse":
        if self._transport is not None:
            try:
                return self._transport(url, authenticated, max_bytes, referer)
            except Exception:
                _bail("transport_failure")
        try:
            return _default_transport(
                url,
                authenticated,
                max_bytes,
                referer,
                jar=self._jar,
                auth_ctx=self._auth_ctx,
                plain_ctx=self._plain_ctx,
                timeout=self._timeout,
            )
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLError):
                _bail("tls_failure")
            _bail("transport_failure")
        except (TimeoutError, ssl.SSLError):
            _bail("tls_failure")

    # -- login -------------------------------------------------------------

    def login(self) -> bool:
        """Drive the mTLS handshake until the portal issues a session cookie.

        Stops following after the first BOE-auth or ``/reg/...`` landing.
        Never reads subsequent account pages.
        """
        url = "https://subastas.boe.es/id/checkCert.php?referer=/id/loginCert.php"
        hops = 0
        while True:
            if not _auth_origin_ok(url):
                _bail("auth_origin_not_allowed")
            hops += 1
            if hops > MAX_LOGIN_REDIRECTS:
                _bail("auth_redirect_limit")
            response = self._request(url, authenticated=True, max_bytes=8 * 1024 * 1024)
            if response[0] not in (200, 301, 302, 303, 307, 308):
                _bail("unexpected_status")
            location = _header(response[1], "Location")
            if not location:
                return True
            next_url = urllib.parse.urljoin(url, location)
            parsed = urllib.parse.urlsplit(next_url)
            if _auth_origin_ok(next_url):
                url = next_url
                continue
            if (
                _is_https_clean(parsed)
                and parsed.hostname == ASSET_HOST
                and parsed.path.startswith("/reg/")
            ):
                return True
            _bail("auth_redirect_blocked")

    # -- asset GET ---------------------------------------------------------

    def _read_with_bounded_redirect(
        self,
        url: str,
        current_id_sub: str,
        authenticated: bool,
        max_bytes: int,
        max_redirects: int,
        *,
        image_mode: bool,
    ) -> "TransportResponse":
        seen: set = set()
        expected_lot = (_qs(_split(url)).get("idLote") or [""])[0]
        expected_document = (_qs(_split(url)).get("idDoc") or [""])[0]
        check = _image_origin_ok if image_mode else _asset_origin_ok
        for hop in range(max_redirects + 1):
            if not check(url, current_id_sub):
                _bail("asset_origin_not_allowed")
            if not image_mode and (_qs(_split(url)).get("idLote") or [""])[0] != expected_lot:
                _bail("asset_redirect_blocked")
            if image_mode and (_qs(_split(url)).get("idDoc") or [""])[0] != expected_document:
                _bail("asset_redirect_blocked")
            if url in seen:
                _bail("asset_redirect_loop")
            seen.add(url)
            response = self._request(url, authenticated=authenticated, max_bytes=max_bytes)
            location = _header(response[1], "Location")
            if not location:
                return response
            if hop == max_redirects:
                _bail("asset_redirect_limit")
            url = urllib.parse.urljoin(url, location)
        _bail("asset_redirect_limit")

    # -- public iter -------------------------------------------------------

    def iter_asset_photos(
        self,
        asset_url: str,
        *,
        max_photos: int = 50,
    ) -> Iterator[Dict[str, Any]]:
        """Yield image bytes for the current Bien inside ``asset_url``.

        Each yielded dict: ``data`` (bytes), ``content_type`` (image/jpeg
        | png | webp | gif), ``caption`` (``<img alt|title>`` or
        ``None``), ``source_url`` (public canonical auction page, no
        document id), ``document_key`` (sha256 of auction||doc_id).
        """
        canonical = canonical_asset_page_url(asset_url)
        if canonical is None:
            _bail("invalid_argument")
        current_id_sub = (_qs(_split(canonical)).get("idSub") or [""])[0]
        if not current_id_sub:
            _bail("invalid_argument")
        html_response = self._read_with_bounded_redirect(
            canonical,
            current_id_sub,
            authenticated=False,
            max_bytes=MAX_HTML_BYTES,
            max_redirects=MAX_ASSET_REDIRECTS,
            image_mode=False,
        )
        if html_response[0] != 200:
            _bail("unexpected_status")
        html_body = html_response[2]
        if len(html_body) > MAX_HTML_BYTES:
            _bail("html_too_large")
        html_text = html_body.decode("utf-8", errors="replace")
        if LOGIN_MARKER_RE.search(html_text):
            _bail("asset_login_required")
        anchors = _parse_bien_anchors(html_text, canonical, current_id_sub)
        if anchors is None:
            _bail("multiple_assets_ambiguous")
        if not anchors:
            _bail("asset_unparseable")
        for yielded, (document_key, doc_url, caption) in enumerate(anchors):
            if yielded >= max_photos:
                break
            image_response = self._read_with_bounded_redirect(
                doc_url,
                current_id_sub,
                authenticated=False,
                max_bytes=MAX_IMAGE_BYTES,
                max_redirects=MAX_IMAGE_REDIRECTS,
                image_mode=True,
            )
            if image_response[0] != 200:
                _bail("unexpected_status")
            blob = image_response[2]
            if len(blob) > MAX_IMAGE_BYTES:
                _bail("image_oversize")
            content_type = _verify_image(blob, _header(image_response[1], "Content-Type") or "")
            yield {
                "data": blob,
                "content_type": content_type,
                "caption": caption,
                "source_url": canonical,
                "document_key": document_key,
            }


def _extract_bien_block(html_text: str) -> Optional[Dict[str, Any]]:
    """Lone Bien block, or ``None`` when several are ambiguous.

    * No ``<h4>Bien N</h4>`` headers → ``{'number': None, ...}``.
    * Exactly one → its slice.
    * Two or more → ``None`` (caller raises ``multiple_assets_ambiguous``).
    """
    from auctionradar.connectors.boe.detail import _bien_blocks  # trusted module in this repo

    blocks = _bien_blocks(html_text)
    if not blocks:
        return {"number": None, "inner_html": html_text}
    if len(blocks) > 1:
        return None
    number, inner = blocks[0]
    return {"number": number, "inner_html": inner}


def _document_key(idsub: str, iddoc: str) -> str:
    return hashlib.sha256(f"{idsub}/{iddoc}".encode("utf-8")).hexdigest()


class _AnchorStackParser(HTMLParser):
    """Pair each ``<a href=verDocumento>`` with the ``<img>`` inside it."""

    _INST_PATH = re.compile(r"^/?(?:imagenes|sprite|css|js)/", re.I)
    _REJECT_HINTS = re.compile(
        r"logo|icon|spinner|banner|placeholder|avatar|pixel|portal_subastas"
        r"|pujar|deposit|pagos|login|registro|cuenta|salir",
        re.I,
    )

    def __init__(self, base_url: str, current_id_sub: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._current_id_sub = current_id_sub.upper()
        self._a_stack: List[Tuple[str, List[Tuple[str, str]]]] = []
        self._results: List[Tuple[str, str, Optional[str]]] = []

    def handle_starttag(self, tag, attrs) -> None:
        attr_dict = {k: v for k, v in attrs if k}
        if tag == "a":
            href = attr_dict.get("href") or ""
            absolute = urllib.parse.urljoin(self._base_url, href) if href else ""
            self._a_stack.append((absolute, []))
        elif tag == "img" and self._a_stack:
            src = (
                attr_dict.get("data-src")
                or attr_dict.get("data-original")
                or attr_dict.get("src")
                or ""
            )
            cap = (attr_dict.get("alt") or attr_dict.get("title") or "").strip()
            self._a_stack[-1][1].append((src, cap))

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._a_stack:
            return
        href, imgs = self._a_stack.pop()
        if not href or not imgs:
            return
        if (
            not _image_origin_ok(href, self._current_id_sub)
            or self._INST_PATH.search(href)
            or self._REJECT_HINTS.search(href)
        ):
            return
        parsed = _split(href)
        if parsed.path != "/reg/verDocumento.php":
            return  # thumbnail-only anchors are intentionally dropped
        qs = _qs(parsed)
        idsub = (qs.get("idSub") or [""])[0].upper()
        iddoc = (qs.get("idDoc") or [""])[0]
        if not iddoc:
            return
        # Correlate doc-href idDoc with the thumbnail idDoc inside <img>.
        for img_src, _caption in imgs:
            thumb_url = urllib.parse.urljoin(self._base_url, img_src)
            if (
                not _image_origin_ok(thumb_url, self._current_id_sub)
                or _split(thumb_url).path != "/reg/verThumbnail.php"
            ):
                return
            thumb = _qs(_split(thumb_url))
            thumb_doc = (thumb.get("idDoc") or [""])[0]
            thumb_sub = (thumb.get("idSub") or [""])[0].upper()
            if not thumb_doc or thumb_doc != iddoc:
                return
            if thumb_sub and thumb_sub != idsub:
                return
        caption = " | ".join(c for _, c in imgs if c) or None
        self._results.append((_document_key(idsub, iddoc), href, caption))

    def collect(self) -> List[Tuple[str, str, Optional[str]]]:
        return list(self._results)


def _parse_bien_anchors(html_text, base_url, current_id_sub):
    block = _extract_bien_block(html_text)
    if block is None:
        return None
    parser = _AnchorStackParser(base_url, current_id_sub)
    parser.feed(block["inner_html"])
    return parser.collect()


def _verify_image(blob, declared):
    """Strictly check bytes against the declared ``Content-Type``."""
    if not blob:
        _bail("image_mime_rejected")
    declared = (declared or "").split(";", 1)[0].strip().lower()
    # BOE serves some original JPEGs with the non-canonical image/jpg alias.
    # Normalize only that alias and retain the binary check.
    if declared == "image/jpg":
        declared = "image/jpeg"
    if declared == "image/jpeg":
        return "image/jpeg" if blob.startswith(b"\xff\xd8\xff") else _bail("image_magic_rejected")
    if declared == "image/png":
        return (
            "image/png" if blob.startswith(b"\x89PNG\r\n\x1a\n") else _bail("image_magic_rejected")
        )
    if declared == "image/gif":
        return "image/gif" if blob[:6] in (b"GIF87a", b"GIF89a") else _bail("image_magic_rejected")
    if declared == "image/webp":
        ok = len(blob) >= 12 and blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
        return "image/webp" if ok else _bail("image_magic_rejected")
    _bail("image_mime_rejected")


def _header(headers, name):
    return next((value for key, value in headers.items() if key.lower() == name.lower()), None)


__all__ = [
    "AUTH_PATH_HOSTS",
    "ALLOWED_ASSET_PATHS",
    "ALLOWED_ASSET_QUERY_KEYS",
    "ALLOWED_IMAGE_PATHS",
    "ASSET_HOST",
    "BoeAccessError",
    "BoeClient",
    "DEFAULT_TIMEOUT",
    "MAX_HTML_BYTES",
    "MAX_IMAGE_BYTES",
    "MAX_PASS_BYTES",
    "MAX_P12_BYTES",
    "canonical_asset_page_url",
    "load_client_context",
]
