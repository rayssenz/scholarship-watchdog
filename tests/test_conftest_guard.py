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


def test_version_is_exposed():
    import scholarship_watchdog

    assert scholarship_watchdog.__version__ == "0.1.0"
