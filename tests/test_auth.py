"""Security regression tests: authentication, origin policy, static exposure."""

from tests.conftest import TEST_TOKEN


def test_api_requires_token(client, auth_settings):
    res = client.get("/api/files")
    assert res.status_code == 401


def test_wrong_token_rejected(client, auth_settings):
    res = client.get("/api/files", headers={"Authorization": "Bearer wrong-token"})
    assert res.status_code == 401


def test_query_param_token_accepted(client, auth_settings):
    """Downloads and media elements authenticate via ?token= (SEC-01 fix)."""
    res = client.get(f"/api/files?token={TEST_TOKEN}")
    assert res.status_code == 200


def test_token_in_header_accepted(authed_client, auth_settings):
    res = authed_client.get("/api/files")
    assert res.status_code == 200


def test_health_public(client, auth_settings):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_cross_origin_rejected(client, auth_settings):
    """A browser request from a foreign Origin must never reach the API
    (CSRF / DNS-rebinding defense, SEC-04 fix)."""
    res = client.get(
        "/api/files",
        headers={"Origin": "https://evil.example.com"},
    )
    assert res.status_code == 403


def test_same_origin_allowed(client, auth_settings):
    res = client.get(
        "/api/files",
        headers={
            "Origin": "http://testserver",
            "Host": "testserver",
            "Authorization": f"Bearer {TEST_TOKEN}",
        },
    )
    assert res.status_code == 200


def test_tauri_origin_allowed(client, auth_settings):
    res = client.get(
        "/api/telemetry/snapshot",
        headers={
            "Origin": "tauri://localhost",
            "Host": "testserver",
            "Authorization": f"Bearer {TEST_TOKEN}",
        },
    )
    assert res.status_code == 200


def test_websocket_requires_token(client, auth_settings):
    """Unauthenticated websocket connects are rejected (closed by server)."""
    import pytest
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/telemetry"):
            pass


def test_docs_disabled(client, auth_settings):
    """OpenAPI surface must not be exposed (SEC-06 hardening)."""
    assert client.get("/docs").status_code in (404, 401)
    assert client.get("/openapi.json").status_code in (404, 401)


def test_loopback_default():
    """Default bind must be loopback unless LAN mode is explicitly enabled
    (SEC-01 fix)."""
    from backend.config import Settings

    s = Settings(ALLOW_LAN=False, HOST="0.0.0.0")
    assert s.effective_host == "127.0.0.1"

    s2 = Settings(ALLOW_LAN=True, HOST="0.0.0.0")
    assert s2.effective_host == "0.0.0.0"
