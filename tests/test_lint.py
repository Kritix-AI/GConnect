"""Tests for the /api/lint endpoint (editor diagnostics for PROBLEMS)."""


def test_lint_syntax_error(authed_client):
    res = authed_client.post(
        "/api/lint",
        json={"path": "broken.py", "content": "def broken(:\n    pass\n"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["path"] == "broken.py"
    diags = data["diagnostics"]
    assert len(diags) == 1
    assert diags[0]["severity"] == "error"
    assert "SyntaxError" in diags[0]["message"]
    assert diags[0]["line"] == 1


def test_lint_clean_code(authed_client):
    res = authed_client.post(
        "/api/lint",
        json={"path": "ok.py", "content": "x = 1\nprint(x)\n"},
    )
    assert res.status_code == 200
    assert res.json()["diagnostics"] == []


def test_lint_undefined_name_is_error(authed_client):
    """pyflakes-backed check (skipped cleanly if pyflakes is unavailable)."""
    from backend.linter import HAS_PYFLAKES

    if not HAS_PYFLAKES:
        import pytest

        pytest.skip("pyflakes not installed")

    res = authed_client.post(
        "/api/lint",
        json={"path": "u.py", "content": "print(undefined_var)\n"},
    )
    diags = res.json()["diagnostics"]
    assert any(
        d["severity"] == "error" and "undefined_var" in d["message"] for d in diags
    )


def test_lint_unused_import_is_warning(authed_client):
    from backend.linter import HAS_PYFLAKES

    if not HAS_PYFLAKES:
        import pytest

        pytest.skip("pyflakes not installed")

    res = authed_client.post(
        "/api/lint",
        json={"path": "imp.py", "content": "import os\nprint('hi')\n"},
    )
    diags = res.json()["diagnostics"]
    assert any(
        d["severity"] == "warning" and "os" in d["message"] for d in diags
    )


def test_lint_rejects_oversized_content(authed_client):
    from backend.linter import MAX_LINT_BYTES

    res = authed_client.post(
        "/api/lint",
        json={"path": "big.py", "content": "x = 1\n" * (MAX_LINT_BYTES // 4 + 10)},
    )
    assert res.status_code == 413
