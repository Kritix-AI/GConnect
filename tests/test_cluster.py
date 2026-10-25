"""Cluster registry + status tests (multi-PC GPU aggregation)."""

import json
import time

import pytest

from backend.cluster_manager import ClusterManager


@pytest.fixture()
def cluster(tmp_path, monkeypatch):
    """Fresh registry persisted inside a temp runtime dir."""
    from backend.config import settings

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setattr(settings, "RUNTIME_DIR", runtime, raising=False)
    return ClusterManager()


def test_add_list_remove_node_masks_token(cluster):
    cluster.add_node("pc2", "http://192.168.1.50:8000/", "secret-token-123")

    nodes = cluster.list_nodes()
    assert len(nodes) == 1
    assert nodes[0]["name"] == "pc2"
    assert nodes[0]["url"] == "http://192.168.1.50:8000"
    # Token must never leak into API-visible views or the raw JSON... the raw
    # file DOES contain it (it must, to authenticate) - only views mask it.
    assert "token" not in nodes[0]

    assert cluster.remove_node("pc2") is True
    assert cluster.list_nodes() == []
    assert cluster.remove_node("pc2") is False


def test_registry_persists_across_instances(cluster, tmp_path):
    from backend.config import settings

    cluster.add_node("pc2", "http://192.168.1.50:8000", "secret-token-123")
    again = ClusterManager()
    assert [n["name"] for n in again.list_nodes()] == ["pc2"]


def test_add_node_validation(cluster):
    with pytest.raises(ValueError):
        cluster.add_node("", "http://x:8000", "t" * 20)
    with pytest.raises(ValueError):
        cluster.add_node("pc", "ftp://bad-scheme:8000", "t" * 20)
    with pytest.raises(ValueError):
        cluster.add_node("pc", "http://192.168.1.50:8000", "  ")
    # duplicate names rejected
    cluster.add_node("pc", "http://192.168.1.50:8000", "t" * 20)
    with pytest.raises(ValueError):
        cluster.add_node("pc", "http://192.168.1.51:8000", "t" * 20)


def test_cluster_status_local_node_and_offline_remote(cluster):
    status = cluster.get_cluster_status()

    # Local node always present and first.
    assert status["nodes"][0]["name"] == "this-pc"
    assert status["nodes"][0]["local"] is True
    assert status["nodes"][0]["online"] is True
    assert status["online_count"] == 1
    assert status["pooled_vram_total_mb"] >= 0

    # Unreachable remote node shows up as offline with an error, no crash.
    cluster.add_node("ghost", "http://127.0.0.1:59999", "t" * 20)
    status = cluster.get_cluster_status()
    assert status["node_count"] == 2
    assert status["online_count"] == 1
    ghost = [n for n in status["nodes"] if n["name"] == "ghost"][0]
    assert ghost["online"] is False
    assert ghost["error"]


def test_cluster_api_endpoints(authed_client, isolated_workspace, monkeypatch, tmp_path):
    from backend.config import settings
    from backend import main as main_mod

    runtime = tmp_path / "rt"
    runtime.mkdir()
    monkeypatch.setattr(settings, "RUNTIME_DIR", runtime, raising=False)
    monkeypatch.setattr(main_mod.cluster_manager, "_nodes", [], raising=False)
    main_mod.cluster_manager._save()

    # Add a node through the API
    res = authed_client.post(
        "/api/cluster/nodes",
        json={"name": "laptop", "url": "http://127.0.0.1:59999", "token": "x" * 24},
    )
    assert res.status_code == 200
    assert "token" not in res.json()["node"]

    # List masks tokens
    res = authed_client.get("/api/cluster/nodes")
    assert res.status_code == 200
    assert all("token" not in n for n in res.json()["nodes"])

    # Status includes local + the offline remote
    res = authed_client.get("/api/cluster/status")
    assert res.status_code == 200
    data = res.json()
    names = [n["name"] for n in data["nodes"]]
    assert "this-pc" in names and "laptop" in names

    # Unauthenticated access denied
    from tests.conftest import TEST_TOKEN  # noqa: F401

    # Remove
    res = authed_client.request(
        "DELETE", "/api/cluster/nodes", json={"name": "laptop"}
    )
    assert res.status_code == 200
    res = authed_client.request(
        "DELETE", "/api/cluster/nodes", json={"name": "laptop"}
    )
    assert res.status_code == 404


def test_telemetry_no_gpu_fallback_is_honest():
    """The old fallback fabricated an 'RTX 4090 (Simulated)' with fake numbers.
    Now it must report zeros and an explicit label."""
    from backend.gpu_telemetry import GPUTelemetryService

    svc = GPUTelemetryService.__new__(GPUTelemetryService)
    svc.nvml_initialized = False
    svc.handles = []
    svc.gpu_count = 0
    svc.gpu_name = "No NVIDIA GPU detected"
    svc._last_good = None
    svc._last_good_ts = 0.0
    import threading
    svc._nvml_lock = threading.Lock()
    svc._nvml_last_init_attempt = time.time()

    snap = svc.get_snapshot()
    assert snap["is_mock"] is True
    assert snap["vram_total_mb"] == 0.0
    assert snap["vram_used_mb"] == 0.0
    assert snap["gpu_count"] == 0
    assert "Simulated RTX 4090" not in snap["gpu_name"]
    assert snap["gpu_name"] == "No NVIDIA GPU detected"


def test_telemetry_last_good_cache_and_degraded():
    """Regression (screenshot bug): concurrent callers made NVML sampling
    fail intermittently and the UI flipped to zeros + 'NO GPU' badge while a
    real GPU existed. Fixes: serialized sampling + 5s last-good cache +
    'degraded' flag instead of 'no GPU'."""
    import threading

    from backend.gpu_telemetry import GPUTelemetryService

    svc = GPUTelemetryService.__new__(GPUTelemetryService)
    svc.nvml_initialized = True
    svc.handles = ["fake-handle"]
    svc.gpu_count = 1
    svc.gpu_name = "Real GPU"
    svc._nvml_lock = threading.Lock()
    svc._nvml_last_init_attempt = time.time()
    svc._last_good = None
    svc._last_good_ts = 0.0

    calls = {"n": 0}

    def fake_sample(handle):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "name": "Real GPU", "index": 0,
                "vram_total_mb": 12227.0, "vram_used_mb": 3000.0,
                "vram_free_mb": 9227.0, "vram_percent": 24.5,
                "gpu_utilization_percent": 17,
                "sm_utilization_percent": 10,
                "memory_controller_percent": 17,
                "temperature_c": 54,
            }
        raise RuntimeError("transient NVML error")

    svc._sample_gpu = fake_sample

    # 1st call succeeds and populates the cache
    good = svc.get_snapshot()
    assert good["is_mock"] is False
    assert good["vram_total_mb"] == 12227.0

    # 2nd call fails transiently -> last-good values, still not mock
    stale = svc.get_snapshot()
    assert stale["is_mock"] is False
    assert stale["vram_used_mb"] == 3000.0

    # Cache expired -> honest zeros + degraded=True (GPU exists, signal lost)
    svc._last_good_ts = 0.0
    zeros = svc.get_snapshot()
    assert zeros["is_mock"] is True
    assert zeros["degraded"] is True
    assert zeros["gpu_name"] == "Real GPU"  # name stays truthful


def test_telemetry_load_metric_matches_task_manager_convention():
    """Regression (screenshot bug): the pill showed SM-only utilization
    (4%) while Task Manager showed 14% for the same moment. LOAD must be
    max(SM, memory-controller) - Task Manager-comparable - with the pure SM
    figure still available for detail."""
    import threading

    from backend.gpu_telemetry import GPUTelemetryService

    svc = GPUTelemetryService.__new__(GPUTelemetryService)
    svc.nvml_initialized = True
    svc.handles = ["fake"]
    svc.gpu_count = 1
    svc.gpu_name = "Real GPU"
    svc._nvml_lock = threading.Lock()
    svc._nvml_last_init_attempt = time.time()
    svc._last_good = None
    svc._last_good_ts = 0.0

    def fake_sample(handle):
        return {
            "name": "Real GPU", "index": 0,
            "vram_total_mb": 12227.0, "vram_used_mb": 3000.0,
            "vram_free_mb": 9227.0, "vram_percent": 24.5,
            "gpu_utilization_percent": 14,    # max(SM, MC), as _sample_gpu now returns
            "sm_utilization_percent": 4,
            "memory_controller_percent": 14,  # DWM/copy traffic
            "temperature_c": 54,
        }

    svc._sample_gpu = fake_sample
    snap = svc.get_snapshot()

    assert snap["gpu_utilization_percent"] == 14  # max(SM, MC)
    assert snap["sm_utilization_percent"] == 4    # pure compute preserved


def test_telemetry_broadcaster_never_abandons_slow_subscriber():
    """Regression: a full queue used to discard the subscriber forever,
    freezing the UI at old values. Now the oldest snapshot is dropped and
    the newest delivered."""
    import asyncio
    import threading

    from backend.gpu_telemetry import GPUTelemetryService

    async def main():
        # A queue at capacity, as when the webview fell behind.
        q = asyncio.Queue(maxsize=2)
        subscribers = {q}

        for seq in range(1, 6):
            snapshot = {"type": "gpu_stat", "seq": seq, "is_mock": False}
            for target in subscribers:
                try:
                    target.put_nowait(snapshot)
                except asyncio.QueueFull:
                    while True:
                        try:
                            target.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    try:
                        target.put_nowait(snapshot)
                    except asyncio.QueueFull:
                        pass

        return q, len(subscribers)

    q, subscriber_count = asyncio.run(main())
    assert subscriber_count == 1, "subscriber was abandoned"
    assert q.qsize() == 1, "queue should hold only the newest snapshot"
    assert q.get_nowait()["seq"] == 5, "newest snapshot must win"


def test_telemetry_stream_self_heals_when_broadcaster_dead():
    """Regression (screenshot bug): a dead broadcaster froze the UI at old
    values. The stream must now sample directly when no broadcast arrives
    within 2s, and subscribe() must restart a dead broadcaster."""
    import asyncio
    import threading
    from unittest.mock import AsyncMock, MagicMock

    from backend.gpu_telemetry import GPUTelemetryService

    async def main():
        svc = GPUTelemetryService.__new__(GPUTelemetryService)
        svc.nvml_initialized = True
        svc.handles = ["fake"]
        svc.gpu_count = 1
        svc.gpu_name = "Real GPU"
        svc._nvml_lock = threading.Lock()
        svc._last_good = None
        svc._last_good_ts = 0.0
        svc._subscribers = set()
        svc._broadcast_task = None  # broadcaster never started (dead)
        svc._nvml_last_init_attempt = time.time()

        def fake_snapshot():
            return {"type": "gpu_stat", "gpu_utilization_percent": 42, "is_mock": False}

        # get_snapshot is a sync method - patch it wholesale.
        svc.get_snapshot = fake_snapshot

        ws = AsyncMock()
        sent = []

        async def send_json(payload):
            sent.append(payload)

        ws.send_json = send_json

        # subscribe() must restart the dead broadcaster.
        q = svc.subscribe()
        assert svc._broadcast_task is not None and not svc._broadcast_task.done()

        # Run stream_telemetry briefly; the fallback path (broadcaster quiet)
        # must still deliver snapshots every <=2s.
        task = asyncio.create_task(svc.stream_telemetry(ws))
        await asyncio.sleep(0.3)
        # Kill the freshly restarted broadcaster to force the fallback path.
        broadcaster = svc._broadcast_task
        broadcaster.cancel()
        svc._broadcast_task = None
        await asyncio.sleep(2.5)  # > timeout 2.0 -> fallback sampling kicks in
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # First snapshot + at least one fallback snapshot must have been sent.
        assert len(sent) >= 2, f"stream froze: only {len(sent)} snapshots sent"
        assert sent[-1]["gpu_utilization_percent"] == 42

    asyncio.run(main())


def test_container_reconcile_upgrades_labelless_container(monkeypatch, tmp_path):
    """Regression: a container created before the config-fingerprint label
    existed (label missing) used to be reused forever without reconciliation.
    It must now be removed and recreated so the desired config applies."""
    from unittest.mock import MagicMock

    from backend.config import settings
    from backend.docker_manager import DockerSandboxManager

    mgr = DockerSandboxManager.__new__(DockerSandboxManager)
    mgr.docker_available = True
    mgr.client = MagicMock()
    mgr.gpu_passthrough_available = False
    mgr.gpu_degraded_reason = "test"

    legacy = MagicMock()
    legacy.status = "running"
    legacy.attrs = {"Config": {"Labels": {}}}  # no dev.gconnect.config label
    mgr.client.containers.get.return_value = legacy

    monkeypatch.setattr(settings, "WORKSPACE_DIR", tmp_path / "ws", raising=False)
    monkeypatch.setattr(settings, "EPHEMERAL_MODE", False, raising=False)
    monkeypatch.setattr(settings, "CONTAINER_IMAGE", "pytorch/pytorch:latest", raising=False)
    monkeypatch.setattr(settings, "SHM_SIZE", "8g", raising=False)
    monkeypatch.setattr(settings, "INFERENCE_PORT", 8001, raising=False)
    monkeypatch.setattr(settings, "CONTAINER_NAME", "connect_dev_sandbox", raising=False)

    mgr.get_or_create_container()

    # The label-less container must have been removed (recreated with labels).
    legacy.remove.assert_called_once()
    assert mgr.client.containers.run.called


def test_telemetry_concurrent_sampling_never_flips_to_mock():
    """The actual reported failure mode: many concurrent get_snapshot calls
    (broadcaster + snapshot endpoint + cluster status) must never produce a
    mock payload while NVML is healthy."""
    import threading

    from backend.gpu_telemetry import gpu_telemetry_service as svc

    if not svc.nvml_initialized:
        import pytest
        pytest.skip("no real NVML on this machine")

    mock_count = []
    lock = threading.Lock()

    def hammer():
        for _ in range(100):
            s = svc.get_snapshot()
            with lock:
                mock_count.append(s["is_mock"])

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not any(mock_count), "concurrent sampling flipped to mock"

