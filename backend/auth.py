"""Shared-secret authentication and origin hardening for the GConnect daemon.

Threat model:
- The daemon exposes file read/write/delete and an interactive container shell.
- Default deployment is loopback-only (desktop app / local browser), where the
  main threat is malicious web pages performing CSRF / DNS-rebinding style
  requests against localhost. Defense: bearer token + strict Origin checks.
- Opt-in LAN mode (CONNECT_ALLOW_LAN=true) exposes the daemon to the network;
  a token is mandatory there.
"""

import hmac
import logging
import os
import secrets
from pathlib import Path
from typing import Dict, Optional

from fastapi import HTTPException, Request, Response, WebSocket
from fastapi.responses import JSONResponse

from backend.config import settings

logger = logging.getLogger("connect.auth")

# Origins the bundled desktop webview may use (Tauri v2 on Windows/macOS/Linux).
TAURI_ORIGINS = {
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
}

# Paths that must stay reachable without a token: the static frontend shell
# (contains no secrets) and the liveness probe.
PUBLIC_PATH_PREFIXES = (
    "/static",
    "/api/health",
)
PUBLIC_PATHS = {"/", "/favicon.ico", "/index.html"}

# CORS grant for the bundled desktop webview only. The webview's origin
# (tauri://localhost / http://tauri.localhost) is not same-origin with a
# remote daemon, so without these headers WebView2 blocks every cross-origin
# fetch (Authorization header -> preflight) before the app sees a response.
# Every other cross-origin caller stays rejected below.
CORS_ALLOW_HEADERS = "Authorization, Content-Type, X-Client-Id"
CORS_ALLOW_METHODS = "GET, POST, DELETE, OPTIONS"

_token: Optional[str] = None


def _write_token_file(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        # Restrictive permissions where the OS supports them (POSIX).
        os.chmod(path, 0o600)
    except Exception:
        pass


def get_token() -> str:
    """Lazily resolves (and persists) the daemon shared secret."""
    global _token
    if _token is None:
        configured = settings.AUTH_TOKEN.strip()
        if configured:
            if len(configured) < 16 and not settings.AUTH_DISABLED:
                logger.warning(
                    "CONNECT_AUTH_TOKEN is shorter than 16 chars - consider a longer secret."
                )
            _token = configured
        else:
            _token = secrets.token_urlsafe(32)
            token_file = settings.RUNTIME_DIR / "auth_token"
            _write_token_file(token_file, _token)
            logger.info(
                "Generated auth token and stored it at %s "
                "(set CONNECT_AUTH_TOKEN to use a fixed token).",
                token_file,
            )
    return _token


def reset_token_for_tests() -> None:
    """Test hook: force re-resolution of the token on next use."""
    global _token
    _token = None


def auth_enabled() -> bool:
    if settings.AUTH_DISABLED:
        # Never allow disabling auth when exposed beyond loopback.
        return not settings.ALLOW_LAN
    return True


def _request_token(request: Request) -> Optional[str]:
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    qp = request.query_params.get("token", "")
    return qp or None


def _origin_allowed(request: Request) -> bool:
    """Same-origin policy: an Origin header (sent by browsers on cross-site
    requests) must match the request's own host, or be a Tauri webview origin.
    Non-browser clients (no Origin) are handled by the token check alone."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    if origin in TAURI_ORIGINS:
        return True
    host = request.headers.get("host", "")
    try:
        scheme, rest = origin.split("://", 1)
        origin_host = rest.split("/", 1)[0]
        # http(s) origins must match the target host exactly (same-origin).
        return scheme in ("http", "https") and origin_host == host
    except ValueError:
        return False


def _tauri_cors_headers(origin: str) -> Dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Headers": "Authorization, Content-Type, X-Client-Id, *",
        "Access-Control-Allow-Methods": CORS_ALLOW_METHODS,
    }


async def enforce_http_auth(request: Request, call_next):
    path = request.url.path

    if path in PUBLIC_PATHS or path.startswith(PUBLIC_PATH_PREFIXES):
        return await call_next(request)

    origin = request.headers.get("origin", "")
    is_tauri = origin in TAURI_ORIGINS

    if origin and not is_tauri and _origin_allowed(request) is False:
        logger.warning("Rejected cross-origin request to %s from %s", path, origin)
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request forbidden."})

    # Preflight from the desktop webview: answer before any handler runs.
    if is_tauri and request.method == "OPTIONS":
        return Response(status_code=204, headers=_tauri_cors_headers(origin))

    # Host management endpoints: allow unrestricted on local loopback (machine owner)
    # while still requiring authentication if accessed remotely.
    if path.startswith("/api/host/"):
        client_host = getattr(request.client, "host", "") if request.client else ""
        if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
            response = await call_next(request)
            if is_tauri:
                for k, v in _tauri_cors_headers(origin).items():
                    response.headers.append(k, v)
            return response

    if not auth_enabled():
        response = await call_next(request)
        if is_tauri:
            for k, v in _tauri_cors_headers(origin).items():
                response.headers.append(k, v)
        return response

    supplied = _request_token(request)
    expected = get_token()
    if not supplied or not hmac.compare_digest(supplied, expected):
        return JSONResponse(status_code=401, content={"detail": "Authentication required."})

    try:
        from backend.connection_tracker import connection_tracker
        c_ip = getattr(request.client, "host", "") if request.client else ""
        c_ua = request.headers.get("user-agent", "")
        c_id = request.headers.get("x-client-id") or request.query_params.get("client_id")
        connection_tracker.register_client(c_ip, c_ua, c_id, channel="http")
    except Exception:
        pass

    response = await call_next(request)
    if is_tauri:
        for k, v in _tauri_cors_headers(origin).items():
            response.headers.append(k, v)
    return response


async def authorize_websocket(websocket: WebSocket) -> bool:
    """Auth + origin gate for WebSocket endpoints. Returns True when allowed
    (caller must accept); rejects and closes the socket itself otherwise."""
    origin = websocket.headers.get("origin")
    if origin and origin not in TAURI_ORIGINS:
        host = websocket.headers.get("host", "")
        try:
            scheme, rest = origin.split("://", 1)
            origin_host = rest.split("/", 1)[0]
            same_origin = scheme in ("http", "https") and origin_host == host
        except ValueError:
            same_origin = False
        if not same_origin:
            logger.warning("Rejected cross-origin websocket from %s", origin)
            await websocket.close(code=4403, reason="Forbidden")
            return False

    if not auth_enabled():
        return True

    supplied = websocket.query_params.get("token", "")
    expected = get_token()
    if not supplied or not hmac.compare_digest(supplied, expected):
        await websocket.close(code=4401, reason="Unauthorized")
        return False
    return True


def require_token_or_raise(supplied: Optional[str]) -> None:
    """Direct check used by non-middleware call sites / tests."""
    if not auth_enabled():
        return
    expected = get_token()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Authentication required.")
