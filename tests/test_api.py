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



def test_upload_files_and_traversal_prevention(authed_client, isolated_workspace):
    # Test uploading multiple files to root
    files = [
        ("files", ("test1.py", b"print('file1')", "text/x-python")),
        ("files", ("test2.txt", b"hello world", "text/plain")),
    ]
    res = authed_client.post("/api/files/upload", files=files, data={"target_dir": ""})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["count"] == 2
    assert (isolated_workspace / "test1.py").read_text(encoding="utf-8") == "print('file1')"
    assert (isolated_workspace / "test2.txt").read_text(encoding="utf-8") == "hello world"

    # Test uploading to a subfolder
    sub_files = [
        ("files", ("nested.py", b"print('nested')", "text/x-python")),
    ]
    res2 = authed_client.post("/api/files/upload", files=sub_files, data={"target_dir": "subfolder"})
    assert res2.status_code == 200
    assert (isolated_workspace / "subfolder" / "nested.py").read_text(encoding="utf-8") == "print('nested')"

    # Test path traversal in target_dir is rejected (403)
    bad_res = authed_client.post("/api/files/upload", files=sub_files, data={"target_dir": "../escape"})
    assert bad_res.status_code == 403

    # Test filename traversal sanitization (e.g. ../evil.py is saved safely as evil.py)
    traversal_files = [
        ("files", ("../../evil.py", b"evil", "text/x-python")),
    ]
    res3 = authed_client.post("/api/files/upload", files=traversal_files, data={"target_dir": ""})
    assert res3.status_code == 200
    assert (isolated_workspace / "evil.py").exists()


def test_workspace_info(authed_client, isolated_workspace):
    # Authenticated request returns workspace_name and workspace_path
    res = authed_client.get("/api/files/workspace-info")
    assert res.status_code == 200
    data = res.json()
    assert "workspace_name" in data
    assert "workspace_path" in data
    assert data["workspace_name"] == isolated_workspace.name
    assert data["workspace_path"] == str(isolated_workspace.resolve())

    # Unauthenticated request must return 401
    from backend.main import app
    from fastapi.testclient import TestClient
    unauth_client = TestClient(app)
    unauth_res = unauth_client.get("/api/files/workspace-info")
    assert unauth_res.status_code == 401


def test_directory_creation_single_and_nested(authed_client, isolated_workspace):
    # Single directory creation
    single_res = authed_client.post("/api/files/create", json={"path": "models", "is_directory": True})
    assert single_res.status_code == 200
    single_data = single_res.json()
    assert single_data["status"] == "success"
    assert single_data["is_directory"] is True
    assert (isolated_workspace / "models").is_dir()

    # Nested subdirectories creation (e.g. models/nlp/transformers)
    nested_res = authed_client.post(
        "/api/files/create", json={"path": "models/nlp/transformers", "is_directory": True}
    )
    assert nested_res.status_code == 200
    nested_data = nested_res.json()
    assert nested_data["status"] == "success"
    assert (isolated_workspace / "models" / "nlp" / "transformers").is_dir()

    # Attempting to re-create an existing directory raises 409 Conflict
    recreate_res = authed_client.post(
        "/api/files/create", json={"path": "models/nlp", "is_directory": True}
    )
    assert recreate_res.status_code == 409
    assert (isolated_workspace / "models" / "nlp").is_dir()

    # Directory creation attempting path traversal outside workspace is blocked (403)
    traversal_res = authed_client.post(
        "/api/files/create", json={"path": "../escaped_dir", "is_directory": True}
    )
    assert traversal_res.status_code == 403
    assert not (isolated_workspace.parent / "escaped_dir").exists()


def test_relative_path_directory_uploads(authed_client, isolated_workspace):
    # Upload folder tree preserving nested relative paths (from webkitdirectory / folder import)
    folder_files = [
        ("files", ("src/models/nlp/bert.py", b"class BertModel: pass\n", "text/x-python")),
        ("files", ("src/models/nlp/config.json", b'{"vocab_size": 30522}', "application/json")),
        ("files", ("src/utils/tokenization.py", b"def tokenize(text): return []\n", "text/x-python")),
        ("files", ("docs/readme.txt", b"Documentation notes", "text/plain")),
    ]
    res = authed_client.post("/api/files/upload", files=folder_files, data={"target_dir": ""})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["count"] == 4

    # Verify nested folder hierarchy on disk
    bert_path = isolated_workspace / "src" / "models" / "nlp" / "bert.py"
    config_path = isolated_workspace / "src" / "models" / "nlp" / "config.json"
    tok_path = isolated_workspace / "src" / "utils" / "tokenization.py"
    doc_path = isolated_workspace / "docs" / "readme.txt"

    assert bert_path.is_file()
    assert bert_path.read_text(encoding="utf-8") == "class BertModel: pass\n"
    assert config_path.is_file()
    assert config_path.read_text(encoding="utf-8") == '{"vocab_size": 30522}'
    assert tok_path.is_file()
    assert tok_path.read_text(encoding="utf-8") == "def tokenize(text): return []\n"
    assert doc_path.is_file()
    assert doc_path.read_text(encoding="utf-8") == "Documentation notes"

    # Upload directory tree with Windows-style backslash paths into target_dir
    win_files = [
        ("files", ("deep\\layers\\dense.py", b"class Dense: pass\n", "text/x-python")),
    ]
    res2 = authed_client.post("/api/files/upload", files=win_files, data={"target_dir": "extra_module"})
    assert res2.status_code == 200
    assert (isolated_workspace / "extra_module" / "deep" / "layers" / "dense.py").is_file()

    # Upload relative path with traversal segments neutralized safely
    traversal_files = [
        ("files", ("safe_dir/../../traversal_attempt.py", b"data", "text/x-python")),
    ]
    res3 = authed_client.post("/api/files/upload", files=traversal_files, data={"target_dir": ""})
    assert res3.status_code == 200
    # Must remain safely inside isolated_workspace and not escape into parent
    assert (isolated_workspace / "traversal_attempt.py").exists() or (isolated_workspace / "safe_dir" / "traversal_attempt.py").exists()
    assert not (isolated_workspace.parent / "traversal_attempt.py").exists()


def test_upload_with_rel_path_form_field(authed_client, isolated_workspace):
    """Verifies POST /api/files/upload explicitly passing the rel_path form field."""
    # 1. Single file upload with nested rel_path form field preserving folder structure
    file_data = [("files", ("upload_source.py", b"x = 42\n", "text/x-python"))]
    res = authed_client.post(
        "/api/files/upload",
        files=file_data,
        data={"target_dir": "", "rel_path": "deep/nested/subfolder/custom_script.py"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["count"] == 1
    assert data["files"][0]["name"] == "custom_script.py"
    assert data["files"][0]["path"] == "deep/nested/subfolder/custom_script.py"

    dest_file = isolated_workspace / "deep" / "nested" / "subfolder" / "custom_script.py"
    assert dest_file.is_file()
    assert dest_file.read_text(encoding="utf-8") == "x = 42\n"

    # 2. Upload with rel_path combined with target_dir
    res2 = authed_client.post(
        "/api/files/upload",
        files=[("files", ("util.py", b"def helper(): pass\n", "text/x-python"))],
        data={"target_dir": "my_package", "rel_path": "core/utils/util.py"},
    )
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "success"
    assert data2["files"][0]["path"] == "my_package/core/utils/util.py"
    dest_file2 = isolated_workspace / "my_package" / "core" / "utils" / "util.py"
    assert dest_file2.is_file()
    assert dest_file2.read_text(encoding="utf-8") == "def helper(): pass\n"

    # 3. Upload with rel_path as directory with trailing slash
    res3 = authed_client.post(
        "/api/files/upload",
        files=[("files", ("data.csv", b"a,b,c\n1,2,3\n", "text/csv"))],
        data={"target_dir": "", "rel_path": "datasets/tabular/"},
    )
    assert res3.status_code == 200
    data3 = res3.json()
    assert data3["status"] == "success"
    assert data3["files"][0]["path"] == "datasets/tabular/data.csv"
    dest_file3 = isolated_workspace / "datasets" / "tabular" / "data.csv"
    assert dest_file3.is_file()
    assert dest_file3.read_text(encoding="utf-8") == "a,b,c\n1,2,3\n"

    # 4. Traversal attack via rel_path is sanitized safely within workspace
    res4 = authed_client.post(
        "/api/files/upload",
        files=[("files", ("payload.py", b"exploit = False\n", "text/x-python"))],
        data={"target_dir": "", "rel_path": "../../escaped.py"},
    )
    assert res4.status_code == 200
    assert not (isolated_workspace.parent / "escaped.py").exists()
    assert (isolated_workspace / "escaped.py").exists()


