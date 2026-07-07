"""SSRF protection for outbound fetches of user-supplied URLs.

Goal verification lets users point the server at an arbitrary URL (api_endpoint
criteria, repo URLs, etc.) and can echo the response back through the
verification-status endpoint. Without a guard that is a server-side request
forgery + exfiltration primitive: ``http://169.254.169.254/...`` (cloud
metadata), ``http://localhost:8000/...``, or any RFC1918 host (Direction 021).

``assert_public_url`` rejects non-http(s) schemes and any host that is — or
resolves to — a loopback / private / link-local / reserved / multicast address.

Residual risk: DNS rebinding (host resolves public here, private at connect
time) is not fully closed without pinning the resolved IP into the transport;
callers should also disable redirects. This closes the common metadata/loopback/
private-range vectors.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """The URL targets a non-public / disallowed address."""


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # includes 169.254.0.0/16 (cloud metadata)
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def assert_public_url(url: str) -> None:
    """Raise UnsafeUrlError unless ``url`` is an http(s) URL to a public host.

    A host that cannot be resolved is allowed through: no internal service is
    reachable if DNS fails, and the subsequent request simply errors. This also
    keeps unit tests (which mock the HTTP client) hermetic offline.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"URL scheme '{parsed.scheme}' is not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")

    candidates: list[ipaddress._BaseAddress] = []
    try:
        candidates.append(ipaddress.ip_address(host))  # literal IP
    except ValueError:
        try:
            for info in socket.getaddrinfo(host, parsed.port or None):
                candidates.append(ipaddress.ip_address(info[4][0]))
        except (socket.gaierror, ValueError, UnicodeError):
            return  # unresolvable → cannot reach an internal service

    for ip in candidates:
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"URL host resolves to a non-public address ({ip}); refusing to fetch"
            )
