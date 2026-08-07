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
import re
import socket
from urllib.parse import urlparse, urlsplit


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


def _assert_public_host(host: str, port: int | None = None) -> None:
    """Raise UnsafeUrlError if ``host`` is — or resolves to — a non-public address.

    A host that cannot be resolved is allowed through: no internal service is
    reachable if DNS fails, and the subsequent connection simply errors. This
    also keeps unit tests (which mock the transport) hermetic offline.
    """
    candidates: list[ipaddress._BaseAddress] = []
    try:
        candidates.append(ipaddress.ip_address(host))  # literal IP
    except ValueError:
        try:
            for info in socket.getaddrinfo(host, port or None):
                candidates.append(ipaddress.ip_address(info[4][0]))
        except (socket.gaierror, ValueError, UnicodeError):
            return  # unresolvable → cannot reach an internal service

    for ip in candidates:
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(
                f"host '{host}' resolves to a non-public address ({ip}); refusing"
            )


def assert_public_url(url: str) -> None:
    """Raise UnsafeUrlError unless ``url`` is an http(s) URL to a public host."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError(f"URL scheme '{parsed.scheme}' is not allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")
    _assert_public_host(host, parsed.port)


# ── git remotes ────────────────────────────────────────────────────────────
#
# A repo URL is not an http(s) fetch: it is handed to `git clone`, which runs on
# the WORKER HOST and inherits the worker's environment — including its SSH
# agent and ~/.ssh keys. So `ssh://` and scp-style (``git@host:owner/repo``)
# remotes genuinely authenticate and are a supported feature. **The control is
# the HOST, never the scheme**: banning ssh here has already broken a live
# feature once, and it would not have closed anything, because
# ``https://10.0.0.5/x`` is just as internal as ``ssh://10.0.0.5/x``.
#
# Two things are refused on top of the host check, and neither is scheme
# policing for its own sake:
#
# * transports that name no network host at all — ``file://`` reads the worker's
#   own filesystem, and ``ext::<command>`` makes git execute an arbitrary
#   command. There is no host to validate, so there is nothing that could make
#   them safe.
# * strings that are neither a URL nor an scp-style remote. Those are local
#   paths (same exposure as ``file://``) and, because the remote sits in git's
#   argv, anything starting with ``-`` would be read as an option rather than a
#   remote.

# git transports that carry a real network host. `git://` is unauthenticated and
# unencrypted but it is still a remote host, and the host check is what matters.
_NETWORK_GIT_SCHEMES = frozenset({"http", "https", "ssh", "git", "git+ssh"})

_URL_FORM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*://")

# Transports with no network host. `ext::` additionally makes git execute an
# arbitrary command, so it is a remote-code path and not merely a local read.
_HOSTLESS_GIT_SCHEMES = frozenset({"file", "ext", "git+file", "local"})
_SCHEME_PREFIX_RE = re.compile(r"^([A-Za-z][A-Za-z0-9+.\-]*):")

# scp-style: ``[user@]host:path``. The host is restricted to hostname/IPv6-literal
# characters and the path may not start with another colon, so ``ext::sh -c …``
# cannot masquerade as host ``ext``.
_SCP_FORM_RE = re.compile(
    r"^(?:(?P<user>[A-Za-z0-9._\-]+)@)?"
    r"(?P<host>[A-Za-z0-9._\-]+|\[[0-9A-Fa-f:.]+\]):"
    r"(?P<path>[^:/].*)$"
)


def assert_public_git_remote(url: str) -> None:
    """Raise UnsafeUrlError unless ``url`` is a git remote on a public host.

    Accepts URL-form remotes on a network transport (``https://``, ``ssh://``,
    ``git://``, ``git+ssh://``) and scp-style remotes (``git@github.com:o/r``).
    Refuses ``file://``, ``ext::``, bare local paths, and any remote whose host
    is — or resolves to — a loopback / private / link-local / reserved address
    (169.254.169.254 and friends).

    Residual risk, as with ``assert_public_url``: DNS rebinding, and a host that
    does not resolve at validation time is allowed through.
    """
    raw = (url or "").strip()
    if not raw:
        raise UnsafeUrlError("repo URL is empty")

    prefix = _SCHEME_PREFIX_RE.match(raw)
    if prefix and prefix.group(1).lower() in _HOSTLESS_GIT_SCHEMES:
        raise UnsafeUrlError(
            f"git transport '{prefix.group(1).lower()}' names no remote host; "
            "refusing to clone"
        )

    if _URL_FORM_RE.match(raw):
        parsed = urlsplit(raw)
        scheme = parsed.scheme.lower()
        if scheme not in _NETWORK_GIT_SCHEMES:
            raise UnsafeUrlError(
                f"git transport '{scheme}' names no remote host; refusing to clone"
            )
        try:
            host = parsed.hostname
        except ValueError as exc:  # malformed IPv6 literal / bad port
            raise UnsafeUrlError(f"repo URL is not parseable: {exc}") from exc
        if not host:
            raise UnsafeUrlError("repo URL has no host")
        try:
            port = parsed.port
        except ValueError as exc:
            raise UnsafeUrlError(f"repo URL has an invalid port: {exc}") from exc
        _assert_public_host(host, port)
        return

    match = _SCP_FORM_RE.match(raw)
    if not match:
        raise UnsafeUrlError(
            "repo URL must be a git remote on a public host "
            "(https://host/owner/repo, ssh://user@host/owner/repo, "
            "or user@host:owner/repo)"
        )
    host = match.group("host")
    if host.startswith("["):
        host = host[1:-1]
    _assert_public_host(host)
