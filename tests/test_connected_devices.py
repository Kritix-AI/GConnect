"""Unit tests for real-time connected devices tracking on GPU host."""

import pytest
from backend.connection_tracker import ConnectionTracker, connection_tracker


def test_connection_tracker_basic_registration():
    tracker = ConnectionTracker()
    assert tracker.get_connected_devices()["total_devices"] == 0

    # Register local client
    tracker.register_client(
        ip="127.0.0.1",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        client_id="local-host-1",
        channel="http",
    )

    stats = tracker.get_connected_devices()
    assert stats["total_devices"] == 1
    assert stats["local_devices"] == 1
    assert stats["remote_devices"] == 0
    assert stats["devices"][0]["is_local"] is True
    assert stats["devices"][0]["name"] == "This PC (Host)"

    # Register remote teammate
    tracker.register_client(
        ip="192.168.1.45",
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        client_id="teammate-mac-1",
        channel="telemetry",
    )

    stats = tracker.get_connected_devices()
    assert stats["total_devices"] == 2
    assert stats["local_devices"] == 1
    assert stats["remote_devices"] == 1

    # Remote device is sorted first
    remote = stats["devices"][0]
    assert remote["is_local"] is False
    assert remote["ip"] == "192.168.1.45"
    assert "Mac (Chrome)" in remote["name"]
    assert "telemetry" in remote["channels"]


def test_client_id_deduplication():
    tracker = ConnectionTracker()

    dummy_socket1 = object()
    dummy_socket2 = object()

    # Same client opens telemetry WebSocket and terminal WebSocket
    tracker.register_client(
        ip="100.80.12.5",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        client_id="teammate-laptop",
        channel="telemetry",
        socket_obj=dummy_socket1,
    )
    tracker.register_client(
        ip="100.80.12.5",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
        client_id="teammate-laptop",
        channel="terminal",
        socket_obj=dummy_socket2,
    )

    stats = tracker.get_connected_devices()
    # Must group as 1 device with multiple channels
    assert stats["total_devices"] == 1
    assert stats["remote_devices"] == 1
    dev = stats["devices"][0]
    assert set(dev["channels"]) == {"telemetry", "terminal"}

    # Disconnect one socket
    tracker.unregister_socket(dummy_socket1, channel="telemetry")
    stats = tracker.get_connected_devices()
    assert stats["total_devices"] == 1
    assert stats["devices"][0]["channels"] == ["terminal"]

    # Disconnect remaining socket
    tracker.unregister_socket(dummy_socket2, channel="terminal")
    stats = tracker.get_connected_devices()
    # Still visible during grace period
    assert stats["total_devices"] == 1


def test_device_name_parsing():
    tracker = ConnectionTracker()
    assert tracker._parse_device_name("", is_local=True) == "This PC (Host)"
    assert tracker._parse_device_name("", is_local=False) == "Remote Device"

    ua_mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    assert tracker._parse_device_name(ua_mac, is_local=False) == "Mac (Chrome)"

    ua_win_edge = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    assert tracker._parse_device_name(ua_win_edge, is_local=False) == "Windows PC (Edge)"

    ua_linux_ff = "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/119.0"
    assert tracker._parse_device_name(ua_linux_ff, is_local=False) == "Linux PC (Firefox)"

    ua_python = "python-requests/2.31.0"
    assert tracker._parse_device_name(ua_python, is_local=False) == "Device (Python SDK)"


def test_api_connected_devices_endpoints(authed_client):
    # Test dedicated endpoint
    res = authed_client.get("/api/host/connected-devices")
    assert res.status_code == 200
    data = res.json()
    assert "total_devices" in data
    assert "remote_devices" in data
    assert "local_devices" in data
    assert isinstance(data["devices"], list)

    # Test inclusion in share-info
    share_res = authed_client.get("/api/host/share-info")
    assert share_res.status_code == 200
    share_data = share_res.json()
    assert "connected_devices" in share_data
    assert "remote_devices" in share_data["connected_devices"]
    assert "devices" in share_data["connected_devices"]
