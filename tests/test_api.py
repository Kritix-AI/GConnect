import pytest
from fastapi.testclient import TestClient

from backend.main import app

client = TestClient(app)


def test_list_files():
    response = client.get("/api/files")
    assert response.status_code == 200
    data = response.json()
    assert "workspace" in data
    assert "files" in data
    assert isinstance(data["files"], list)


def test_file_lifecycle_and_content():
    test_path = "unit_test_voice_agent.py"
    sample_content = "# PyTorch Voice Pipeline Test\nprint('Testing voice agent')"

    # 1. Create file
    create_res = client.post("/api/files/create", json={"path": test_path, "is_directory": False})
    assert create_res.status_code == 200

    # 2. Save content
    save_res = client.post("/api/files/save", json={"path": test_path, "content": sample_content})
    assert save_res.status_code == 200

    # 3. Read content
    read_res = client.get(f"/api/files/content?path={test_path}")
    assert read_res.status_code == 200
    assert read_res.json()["content"] == sample_content

    # 4. Clean up / Delete file
    del_res = client.request("DELETE", "/api/files/delete", json={"path": test_path})
    assert del_res.status_code == 200

    # 5. Verify deleted
    verify_res = client.get(f"/api/files/content?path={test_path}")
    assert verify_res.status_code == 404


def test_path_traversal_prevention():
    traversal_paths = [
        "../../system_file.py",
        "../..\\evil.py",
        "/etc/passwd",
        "C:\\Windows\\system32\\cmd.exe",
        "..\\..\\..\\malicious.txt",
    ]

    for bad_path in traversal_paths:
        # Attempt save
        save_res = client.post("/api/files/save", json={"path": bad_path, "content": "malicious"})
        assert save_res.status_code == 403, f"Path traversal allowed on save: {bad_path}"

        # Attempt read
        read_res = client.get(f"/api/files/content?path={bad_path}")
        assert read_res.status_code == 403, f"Path traversal allowed on read: {bad_path}"

        # Attempt delete
        del_res = client.request("DELETE", "/api/files/delete", json={"path": bad_path})
        assert del_res.status_code == 403, f"Path traversal allowed on delete: {bad_path}"


def test_telemetry_snapshot():
    response = client.get("/api/telemetry/snapshot")
    assert response.status_code == 200
    data = response.json()
    assert data["type"] == "gpu_stat"
    assert "gpu_name" in data
    assert "vram_total_mb" in data
    assert "vram_used_mb" in data
    assert "temperature_c" in data
    assert "gpu_utilization_percent" in data


def test_container_status():
    response = client.get("/api/container/status")
    assert response.status_code == 200
    data = response.json()
    assert "docker_available" in data
    assert "container_status" in data
    assert data["inference_port"] == 8001
    assert data["shm_size"] == "8g"
    assert "/workspace/.hf_cache" in data["hf_cache"] or "ephemeral" in data["hf_cache"]


def test_wipe_model_cache():
    from backend.config import settings

    # 1. Create a simulated model cache file in workspace/.hf_cache
    cache_dir = settings.WORKSPACE_DIR.resolve() / settings.HF_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    test_model_file = cache_dir / "simulated_weights.bin"
    test_model_file.write_bytes(b"0" * (1024 * 1024))  # 1 MB

    # 2. Call wipe endpoint
    response = client.post("/api/cache/wipe")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["freed_mb"] >= 1.0
    assert not test_model_file.exists()


