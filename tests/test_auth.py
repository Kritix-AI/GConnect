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


def test_tauri_origin_gets_cors_headers(client, auth_settings):
    """Desktop app mode: the webview origin is cross-origin with a remote
    daemon, so responses must carry CORS headers or WebView2 blocks them."""
    res = client.get(
        "/api/telemetry/snapshot",
        headers={
            "Origin": "http://tauri.localhost",
            "Host": "testserver",
            "Authorization": f"Bearer {TEST_TOKEN}",
        },
    )
    assert res.status_code == 200
    assert res.headers["Access-Control-Allow-Origin"] == "http://tauri.localhost"
    assert "Authorization" in res.headers.get("Access-Control-Allow-Headers", "")
    assert "X-Client-Id" in res.headers.get("Access-Control-Allow-Headers", "")


def test_tauri_origin_preflight_204(client, auth_settings):
    """Preflight (Authorization and X-Client-Id headers force one) must be answerable without
    a token - browsers send OPTIONS before the authenticated request."""
    res = client.options(
        "/api/files",
        headers={
            "Origin": "tauri://localhost",
            "Host": "testserver",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization, content-type, x-client-id",
        },
    )
    assert res.status_code == 204
    assert res.headers["Access-Control-Allow-Origin"] == "tauri://localhost"
    assert "X-Client-Id" in res.headers.get("Access-Control-Allow-Headers", "")


def test_tauri_origin_still_needs_token(client, auth_settings):
    """CORS grant must not become an auth bypass."""
    res = client.get("/api/files", headers={"Origin": "tauri://localhost"})
    assert res.status_code == 401


def test_same_origin_no_cors_headers(authed_client, auth_settings):
    """Browser-tab usage is same-origin: no CORS headers should be emitted."""
    res = authed_client.get("/api/files", headers={"Origin": "http://testserver"})
    assert res.status_code == 200
    assert "Access-Control-Allow-Origin" not in res.headers


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
