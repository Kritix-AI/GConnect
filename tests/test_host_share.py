import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend import auth


@pytest.fixture
def client():
    return TestClient(app)


def test_host_share_info_loopback(client):
    """Loopback clients (127.0.0.1) can query host share info."""
    res = client.get("/api/host/share-info")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "network" in data
    assert "hostname" in data["network"]
    assert "recommended_ip" in data["network"]
    assert "token" in data
    assert "port" in data
    assert "inference_port" in data
    assert "telemetry" in data


def test_host_configure_token(client):
    """Machine owner can customize the token."""
    res = client.post("/api/host/configure-token", json={"token": "KritixShared2026"})
    assert res.status_code == 200
    assert res.json()["token"] == "KritixShared2026"
    assert auth.get_token() == "KritixShared2026"

    # Verify short token is rejected
    err = client.post("/api/host/configure-token", json={"token": "ab"})
    assert err.status_code == 400


def test_host_firewall_rule_endpoint(client):
    """Firewall rule trigger returns status."""
    res = client.post("/api/host/firewall-rule")
    assert res.status_code == 200
    assert res.json()["status"] in ("success", "skipped")


def test_host_firewall_status_endpoint(client):
    """Firewall status endpoint returns valid response."""
    res = client.get("/api/host/firewall-status")
    assert res.status_code == 200
    assert "allowed" in res.json()
