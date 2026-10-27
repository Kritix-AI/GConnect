"""Core API lifecycle tests (auth + workspace isolation aware)."""

import zipfile
import io


def test_list_files(authed_client, isolated_workspace):
    response = authed_client.get("/api/files")
    assert response.status_code == 200
    data = response.json()
    assert "workspace" in data
    assert "files" in data
    assert isinstance(data["files"], list)


def test_file_lifecycle_and_content(authed_client, isolated_workspace):
    test_path = "unit_test_script.py"
    sample_content = "# Universal PyTorch Test\nprint('Testing script execution')"

    create_res = authed_client.post("/api/files/create", json={"path": test_path, "is_directory": False})
    assert create_res.status_code == 200

    save_res = authed_client.post("/api/files/save", json={"path": test_path, "content": sample_content})
    assert save_res.status_code == 200
    assert "mtime" in save_res.json()

    read_res = authed_client.get(f"/api/files/content?path={test_path}")
    assert read_res.status_code == 200
    assert read_res.json()["content"] == sample_content

    del_res = authed_client.request("DELETE", "/api/files/delete", json={"path": test_path})
    assert del_res.status_code == 200

    verify_res = authed_client.get(f"/api/files/content?path={test_path}")
    assert verify_res.status_code == 404


def test_container_status(authed_client):
    response = authed_client.get("/api/container/status")
    assert response.status_code == 200
    data = response.json()
    assert "docker_available" in data
    assert "container_status" in data
    assert data["inference_port"] == 8001
    assert data["shm_size"] == "8g"
    assert "/workspace/.hf_cache" in data["hf_cache"] or "ephemeral" in data["hf_cache"]


def test_wipe_model_cache_isolated(authed_client, isolated_workspace, monkeypatch):
    """BUG-04 regression: the wipe test must run against an ISOLATED temp
    workspace, never the real model cache."""
    from backend.config import settings
    from backend import docker_manager as dm

    cache_dir = isolated_workspace / settings.HF_CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    test_model_file = cache_dir / "simulated_weights.bin"
    test_model_file.write_bytes(b"0" * (1024 * 1024))  # 1 MB

    class FakeMgr:
        docker_available = False
        client = None

        def wipe_model_cache(self):
            return dm.DockerSandboxManager.wipe_model_cache(FakeMgr)

    fake = FakeMgr()
    monkeypatch.setattr(dm, "docker_manager", fake, raising=False)
    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "docker_manager", fake, raising=False)

    response = authed_client.post("/api/cache/wipe")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["freed_mb"] >= 1.0
    assert not test_model_file.exists()


def test_raw_audio_file(authed_client, isolated_workspace):
    audio_path = isolated_workspace / "test_synth.wav"
    audio_path.write_bytes(
        b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00D\xac\x00\x00"
        b"\x88X\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"
    )

    try:
        res = authed_client.get("/api/files/raw?path=test_synth.wav")
        assert res.status_code == 200
        assert "audio/wav" in res.headers.get("content-type", "")
        assert len(res.content) > 0

        res_404 = authed_client.get("/api/files/raw?path=non_existent.wav")
        assert res_404.status_code == 404
    finally:
        if audio_path.exists():
            audio_path.unlink()


def test_download_file_and_workspace_zip(authed_client, isolated_workspace):
    test_file = isolated_workspace / "test_export.py"
    test_file.write_text("# Download test", encoding="utf-8")

    try:
        file_res = authed_client.get("/api/files/download?path=test_export.py")
        assert file_res.status_code == 200
        assert 'attachment; filename="test_export.py"' in file_res.headers.get("content-disposition", "")
        assert file_res.text == "# Download test"

        zip_res = authed_client.get("/api/files/download?path=")
        assert zip_res.status_code == 200
        assert "application/zip" in zip_res.headers.get("content-type", "")
        assert 'attachment; filename="gconnect_workspace.zip"' in zip_res.headers.get(
            "content-disposition", ""
        )

        with zipfile.ZipFile(io.BytesIO(zip_res.content)) as zf:
            namelist = zf.namelist()
            assert any("test_export.py" in name for name in namelist)
            assert not any(".hf_cache" in name for name in namelist)
            assert not any(".git" in name for name in namelist)
    finally:
        if test_file.exists():
            test_file.unlink()
