import socket

import httpx
import pytest


def test_real_network_access_is_denied():
    """The autouse no_network fixture must break a genuine outbound call.

    Verifying the fixture exists proves nothing; a monkeypatch applied to the
    wrong attribute installs cleanly and denies nothing. This asserts on the
    behaviour CI depends on.
    """
    with pytest.raises(RuntimeError, match="network access is not allowed"):
        httpx.get("https://example.com", timeout=1)


def test_dns_resolution_is_denied():
    """The autouse no_network fixture must also break hostname resolution.

    httpx's sync backend dials through socket.create_connection, which the
    fixture replaces wholesale, so this sync client never reaches
    getaddrinfo at all and the connect-level test above cannot stand in for
    this one. An async client resolves a hostname before it dials it, so a
    future test on that path would still leak a real DNS query if this
    patch ever regressed. This pins down getaddrinfo directly, independent
    of which call path a caller uses to reach it.
    """
    with pytest.raises(RuntimeError, match="network access is not allowed"):
        socket.getaddrinfo("example.com", 443)


def test_version_is_exposed():
    import scholarship_watchdog

    assert scholarship_watchdog.__version__ == "0.1.0"
