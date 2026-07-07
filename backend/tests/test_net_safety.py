"""SSRF guard tests (Direction 021)."""

import pytest

from app.core.net_safety import UnsafeUrlError, assert_public_url


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
    "http://127.0.0.1:8000/api/health",           # loopback literal
    "http://localhost/admin",                      # loopback name
    "http://10.0.0.5/internal",                    # RFC1918
    "http://192.168.1.1/",                         # RFC1918
    "http://[::1]/",                               # IPv6 loopback
    "ftp://example.com/x",                         # bad scheme
    "file:///etc/passwd",                          # bad scheme
])
def test_blocks_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com/api/health",
    "https://api.github.com",
    "http://93.184.216.34/",  # public IP literal
])
def test_allows_public_urls(url):
    # Should not raise (unresolvable hosts are allowed through by design).
    assert_public_url(url)


def test_unresolvable_host_is_allowed_through():
    # No internal service is reachable if DNS fails; keeps tests hermetic.
    assert_public_url("https://nonexistent.invalid.tld.example/x")
