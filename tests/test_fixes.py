"""Functional regression tests for the Phase-2 bug fixes."""

import io
import os
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.config import settings


# ----------------------------------------------------------------------
# BUG-01b: optimistic-concurrency saves (mtime 409)
# ----------------------------------------------------------------------

def test_save_conflict_returns_409(authed_client, isolated_workspace):
    path = "conflict.py"

    r1 = authed_client.post("/api/files/save", json={"path": path, "content": "v1"})
    assert r1.status_code == 200
    mtime = r1.json()["mtime"]

    # Simulate an external modification after the client loaded the file.
    time.sleep(0.01)
    p = isolated_workspace / path
    p.write_text("v2-external", encoding="utf-8")

    # Client saves stale content with the old mtime -> must be refused.
    r2 = authed_client.post(
        "/api/files/save",
        json={"path": path, "content": "v1-overwrite", "expected_mtime": mtime},
    )
    assert r2.status_code == 409
    assert p.read_text(encoding="utf-8") == "v2-external"

    # Forced save (user confirmed the overwrite dialog) succeeds.
    r3 = authed_client.post(
        "/api/files/save",
        json={"path": path, "content": "v1-overwrite", "expected_mtime": None},
    )
    assert r3.status_code == 200
    assert p.read_text(encoding="utf-8") == "v1-overwrite"


# ----------------------------------------------------------------------
# BUG-05 (part): atomic saves leave no temp files behind
# ----------------------------------------------------------------------

def test_atomic_save_no_temp_residue(authed_client, isolated_workspace):
    authed_client.post("/api/files/save", json={"path": "atomic.py", "content": "x" * 100})
    residue = [p.name for p in isolated_workspace.iterdir() if ".gconnect-tmp" in p.name]
    assert residue == []


# ----------------------------------------------------------------------
# BUG-06: size cap on /api/files/content
# ----------------------------------------------------------------------

def test_content_size_cap(authed_client, isolated_workspace, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONTENT_READ_BYTES", 64, raising=False)
    big = isolated_workspace / "big.bin"
    big.write_bytes(b"0" * 128)

    res = authed_client.get("/api/files/content?path=big.bin")
    assert res.status_code == 413


# ----------------------------------------------------------------------
# BUG-07: symlinked directories are skipped by the tree scan
# ----------------------------------------------------------------------

def test_scan_skips_symlinked_dirs(authed_client, isolated_workspace):
    secret_dir = isolated_workspace.parent / "outside"
    secret_dir.mkdir(exist_ok=True)
    (secret_dir / "leaked.txt").write_text("secret", encoding="utf-8")

    link = isolated_workspace / "evil_link"
    try:
        os.symlink(secret_dir, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks not available on this platform/privilege level")

    res = authed_client.get("/api/files")
    assert res.status_code == 200
    names = [f["name"] for f in res.json()["files"]]
    assert "evil_link" not in names

    # Raw read through the symlink must also be blocked (resolve escapes base).
    raw = authed_client.get("/api/files/raw?path=evil_link/leaked.txt")
    assert raw.status_code == 403


# ----------------------------------------------------------------------
# BUG-03: container configuration reconciliation (unit-level)
# ----------------------------------------------------------------------

def test_config_fingerprint_changes_with_settings(monkeypatch):
    from backend.docker_manager import DockerSandboxManager

    mgr = DockerSandboxManager.__new__(DockerSandboxManager)  # no docker needed
    ws = "/tmp/ws"

    monkeypatch.setattr(settings, "CONTAINER_IMAGE", "pytorch/pytorch:latest", raising=False)
    fp1 = mgr._desired_config_fingerprint(ws)

    monkeypatch.setattr(settings, "SHM_SIZE", "16g", raising=False)
    fp2 = mgr._desired_config_fingerprint(ws)

    monkeypatch.setattr(settings, "SHM_SIZE", "8g", raising=False)
    monkeypatch.setattr(settings, "EPHEMERAL_MODE", True, raising=False)
    fp3 = mgr._desired_config_fingerprint(ws)

    assert fp1 != fp2 != fp3
    assert len(fp1) == 16


# ----------------------------------------------------------------------
# SEC-05: GPU capability detection logic
# ----------------------------------------------------------------------

def _mgr_with_info(runtimes):
    from backend.docker_manager import DockerSandboxManager
    mgr = DockerSandboxManager.__new__(DockerSandboxManager)
    mgr.docker_available = True
    mgr.client = MagicMock()
    mgr.gpu_passthrough_available = False
    mgr.gpu_degraded_reason = ""
    info = MagicMock()
    info.get.return_value = runtimes
    mgr.client.info.return_value = info
    return mgr


def test_gpu_check_nvidia_runtime_detected(monkeypatch):
    from backend import docker_manager as dm
    monkeypatch.setattr(dm, "_nvidia_cdi_specs", lambda: [])
    mgr = _mgr_with_info({"nvidia": {"path": "nvidia-container-runtime"}})
    mgr._check_gpu_capability()
    assert mgr.gpu_passthrough_available is True


def test_gpu_check_no_nvidia_means_unavailable(monkeypatch):
    """Regression for SEC-05: the else-branch used to set True as well."""
    from backend import docker_manager as dm
    monkeypatch.setattr(dm, "_nvidia_cdi_specs", lambda: [])
    mgr = _mgr_with_info({"runc": {"path": "runc"}})
    mgr._check_gpu_capability()
    assert mgr.gpu_passthrough_available is False
    assert "NVIDIA Container Toolkit" in mgr.gpu_degraded_reason


def test_gpu_check_cdi_fallback(monkeypatch):
    from backend import docker_manager as dm
    monkeypatch.setattr(dm, "_nvidia_cdi_specs", lambda: ["nvidia-gpu.json"])
    mgr = _mgr_with_info({"runc": {"path": "runc"}})
    mgr._check_gpu_capability()
    assert mgr.gpu_passthrough_available is True


# ----------------------------------------------------------------------
# BUG-04/BUG-06: wipe behavior against an ISOLATED workspace
# ----------------------------------------------------------------------

def test_wipe_isolated_and_reports_partial_failures(authed_client, isolated_workspace, monkeypatch):
    cache = isolated_workspace / settings.HF_CACHE_DIR_NAME
    (cache / "model-a").mkdir()
    (cache / "model-a" / "weights.bin").write_bytes(b"0" * (2 * 1024 * 1024))
    (cache / "solo.bin").write_bytes(b"1" * (1 * 1024 * 1024))

    from backend import docker_manager as dm

    class FakeMgr:
        docker_available = False
        client = None

        def wipe_model_cache(self):
            return dm.DockerSandboxManager.wipe_model_cache(FakeMgr)

    monkeypatch.setattr(dm, "docker_manager", FakeMgr(), raising=False)
    # main.py imported docker_manager by name; patch where it lives too
    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "docker_manager", FakeMgr(), raising=False)

    res = authed_client.post("/api/cache/wipe")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["freed_mb"] >= 3.0
    assert not (cache / "model-a").exists()
    assert not (cache / "solo.bin").exists()


# ----------------------------------------------------------------------
# BUG-08: streaming zip still produces a valid archive without ignored dirs
# ----------------------------------------------------------------------

def test_zip_streaming_excludes_ignored(authed_client, isolated_workspace):
    (isolated_workspace / "export_me.py").write_text("print(1)", encoding="utf-8")
    hf = isolated_workspace / ".hf_cache"
    hf.mkdir(exist_ok=True)
    (hf / "weights.bin").write_bytes(b"x" * 10)

    res = authed_client.get("/api/files/download?path=")
    assert res.status_code == 200
    assert "application/zip" in res.headers.get("content-type", "")
    assert 'filename="gconnect_workspace.zip"' in res.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
        names = zf.namelist()
        assert any("export_me.py" in n for n in names)
        assert not any(".hf_cache" in n for n in names)
        assert zf.testzip() is None


# ----------------------------------------------------------------------
# Telemetry payload contract (mock mode shape kept + gpus field)
# ----------------------------------------------------------------------

def test_telemetry_snapshot_shape(authed_client):
    res = authed_client.get("/api/telemetry/snapshot")
    assert res.status_code == 200
    data = res.json()
    assert data["type"] == "gpu_stat"
    assert "is_mock" in data
    assert "gpus" in data
    assert "gpu_name" in data and "temperature_c" in data


# ----------------------------------------------------------------------
# Traversal matrix (kept from original suite, now with auth)
# ----------------------------------------------------------------------

def test_path_traversal_prevention(authed_client):
    traversal_paths = [
        "../../system_file.py",
        "../..\\evil.py",
        "/etc/passwd",
        "C:\\Windows\\system32\\cmd.exe",
        "..\\..\\..\\malicious.txt",
    ]

    for bad_path in traversal_paths:
        assert authed_client.post(
            "/api/files/save", json={"path": bad_path, "content": "malicious"}
        ).status_code == 403, f"Path traversal allowed on save: {bad_path}"

        assert authed_client.get(
            f"/api/files/content?path={bad_path}"
        ).status_code == 403, f"Path traversal allowed on read: {bad_path}"

        assert authed_client.request(
            "DELETE", "/api/files/delete", json={"path": bad_path}
        ).status_code == 403, f"Path traversal allowed on delete: {bad_path}"

        assert authed_client.get(
            f"/api/files/raw?path={bad_path}"
        ).status_code == 403, f"Path traversal allowed on raw: {bad_path}"

        assert authed_client.get(
            f"/api/files/download?path={bad_path}"
        ).status_code == 403, f"Path traversal allowed on download: {bad_path}"


def test_rename_traversal_prevention(authed_client, isolated_workspace):
    (isolated_workspace / "a.py").write_text("a", encoding="utf-8")
    res = authed_client.post(
        "/api/files/rename",
        json={"old_path": "a.py", "new_path": "../escape.py"},
    )
    assert res.status_code == 403


def test_rename_flow(authed_client, isolated_workspace):
    """Regression for BUG-12: the rename endpoint is part of the API surface."""
    (isolated_workspace / "orig.py").write_text("print('hi')", encoding="utf-8")
    res = authed_client.post(
        "/api/files/rename",
        json={"old_path": "orig.py", "new_path": "renamed.py"},
    )
    assert res.status_code == 200
    assert (isolated_workspace / "renamed.py").exists()
    assert not (isolated_workspace / "orig.py").exists()


# ----------------------------------------------------------------------
# Local fallback disabled by default (SEC-02)
# ----------------------------------------------------------------------

def test_local_fallback_disabled_by_default():
    from backend.config import Settings
    s = Settings()
    assert s.ALLOW_LOCAL_FALLBACK is False


def test_pty_terminal_rejects_when_no_docker_and_no_fallback(authed_client):
    """Without Docker and without the opt-in fallback, the terminal must NOT
    provide a host shell - it closes with a notice."""
    from backend import pty_bridge as pb
    if pb.docker_manager.docker_available:
        import pytest
        pytest.skip("Docker present on this machine; fallback path not reachable")

    # WebSocket auth uses the ?token= query param (browsers cannot set
    # Authorization headers on WebSocket connections).
    from tests.conftest import TEST_TOKEN

    with authed_client.websocket_connect(f"/ws/terminal?token={TEST_TOKEN}") as ws:
        msg = ws.receive_text()
        assert "fallback is disabled" in msg
