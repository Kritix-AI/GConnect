"""Shared fixtures: isolated workspace (tests never touch real user data -
regression for BUG-04) and an authenticated API client."""

import pytest
from fastapi.testclient import TestClient

from backend import auth
from backend.config import settings

TEST_TOKEN = "unit-test-token-0123456789abcdef"


@pytest.fixture()
def isolated_workspace(tmp_path, monkeypatch):
    """Point the settings singleton at a temp workspace for the duration of a
    test so file operations and cache wipes can never destroy real data."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    cache = ws / settings.HF_CACHE_DIR_NAME
    cache.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    monkeypatch.setattr(settings, "WORKSPACE_DIR", ws, raising=False)
    monkeypatch.setattr(settings, "RUNTIME_DIR", runtime, raising=False)
    monkeypatch.setattr(settings, "EPHEMERAL_MODE", False, raising=False)
    return ws


@pytest.fixture()
def auth_settings(monkeypatch):
    """Force a known token and enable auth checks."""
    monkeypatch.setattr(settings, "AUTH_TOKEN", TEST_TOKEN, raising=False)
    monkeypatch.setattr(settings, "AUTH_DISABLED", False, raising=False)
    monkeypatch.setattr(settings, "ALLOW_LAN", False, raising=False)
    auth.reset_token_for_tests()
    yield
    auth.reset_token_for_tests()


@pytest.fixture()
def client():
    """Unauthenticated client (for testing the 401 path)."""
    from backend.main import app
    return TestClient(app)


@pytest.fixture()
def authed_client(client, auth_settings):
    """Authenticated client using the shared-secret header."""
    client.headers.update({"Authorization": f"Bearer {TEST_TOKEN}"})
    return client
