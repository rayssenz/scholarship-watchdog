"""Shared test fixtures.

The network denial is not a convenience. SPEC.md section 4 requires CI to run
on every push, and a test suite that reaches thirteen live scholarship sites
would be slow, flaky, and impolite to the hosts this project depends on.
"""

from __future__ import annotations

import socket

import pytest

DENIED = "network access is not allowed in tests; use httpx.MockTransport or a fixture"


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deny every outbound socket connection for the duration of a test."""

    def deny(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(DENIED)

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
