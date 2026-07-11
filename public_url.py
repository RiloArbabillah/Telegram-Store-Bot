"""Public URL validation helpers."""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def is_public_https_url(base_url: str) -> bool:
    """Return True when a URL is suitable for externally opened HTTPS links."""
    parsed = urlparse(base_url or "")
    hostname = parsed.hostname
    if parsed.scheme != "https" or not hostname:
        return False

    normalized_host = hostname.lower().rstrip(".")
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        return False

    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        return True

    return not (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )
