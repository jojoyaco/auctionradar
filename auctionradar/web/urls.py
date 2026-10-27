"""URL safety helpers.

We intentionally reject every URL whose scheme is anything other than
``http`` or ``https``. The webapp never embeds URLs that point to a
``javascript:`` / ``data:`` / ``file:`` / ``vbscript:`` target. This
matches the Flask security guidance for ``href`` attributes and is
applied to every URL returned by any API endpoint.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional
from urllib.parse import parse_qsl, urlsplit

ALLOWED_SCHEMES = ("http", "https")
SENSITIVE_QUERY = re.compile(
    r"password|passwd|secret|token|authorization|credential|cookie|session|csrf|api.?key|signature|(?:^|[_-])sig$",
    re.I,
)


def is_safe_url(value: Optional[str]) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate or "\\" in candidate or any(ord(c) < 32 or c.isspace() for c in candidate):
        return False
    try:
        parts = urlsplit(candidate)
        parts.port  # Force malformed-port validation too.
        if any(
            SENSITIVE_QUERY.search(key) for key, _ in parse_qsl(parts.query, max_num_fields=100)
        ):
            return False
    except ValueError:
        return False
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        return False
    if not parts.hostname or parts.username is not None or parts.password is not None:
        return False
    return True


def sanitise(value: Optional[str]) -> Optional[str]:
    return value if is_safe_url(value) else None


def sanitise_many(values: Optional[Iterable]) -> list:
    out = []
    if not values:
        return out
    for value in values:
        if is_safe_url(value):
            out.append(value)
    return out
