import io
import mimetypes
import os
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])

IGNORED_NAMES = {".git", ".hf_cache", "__pycache__", ".venv", ".pytest_cache", ".vscode", ".gconnect"}

# Hard limits protecting the scan from pathological trees.
MAX_SCAN_DEPTH = 24
MAX_SCAN_ENTRIES = 20000


class FileSaveRequest(BaseModel):
    path: str
    content: str
    expected_mtime: Optional[float] = None  # optimistic-concurrency guard


class FileCreateRequest(BaseModel):
    path: str
    is_directory: bool = False


class FileDeleteRequest(BaseModel):
    path: str


class FileRenameRequest(BaseModel):
    old_path: str
    new_path: str


def _resolve_safe_path(rel_path: str) -> Path:
    """
    Resolves relative path against WORKSPACE_DIR and ensures strict traversal prevention.
    Throws HTTP 403 if path escapes the workspace root or attempts absolute path access.
    """
    base = settings.WORKSPACE_DIR.resolve()
    if not rel_path or rel_path.strip() in ("", "."):
        return base

    # Reject absolute paths, leading slashes, and Windows drive letters
    if rel_path.startswith(("/", "\\")) or Path(rel_path).is_absolute() or ":" in rel_path:
        raise HTTPException(status_code=403, detail="Access denied: absolute path forbidden.")

    target = (base / rel_path).resolve()

    try:
        common = os.path.commonpath([str(base), str(target)])
        if common != str(base):
            raise HTTPException(status_code=403, detail="Access denied: path traversal detected.")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: invalid path location.")

    return target


def _file_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _scan_directory(current_dir: Path, base_dir: Path, depth: int = 0, state: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Builds the hierarchical tree. Guards against symlink/junction cycles
    (visited set on realpath), skips symlinked directories entirely (they can
    point outside the workspace), and caps depth/total entries."""
    if state is None:
        state = {"visited": set(), "count": 0}

    items: List[Dict[str, Any]] = []
    if depth > MAX_SCAN_DEPTH:
        return items

    try:
        real = os.path.realpath(current_dir)
        if real in state["visited"]:
            return items
        state["visited"].add(real)
    except Exception:
        return items

    try:
        entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return items

    for entry in entries:
        if state["count"] >= MAX_SCAN_ENTRIES:
            return items
        if entry.name in IGNORED_NAMES:
            continue

        # Symlinked directories are skipped: cycles and out-of-workspace leaks.
        if entry.is_dir() and entry.is_symlink():
            continue

        rel_path = str(entry.relative_to(base_dir)).replace("\\", "/")
        state["count"] += 1

        if entry.is_dir():
            items.append({
                "name": entry.name,
                "path": rel_path,
                "type": "directory",
                "children": _scan_directory(entry, base_dir, depth + 1, state)
            })
        else:
            try:
                size_bytes = entry.stat().st_size
            except OSError:
                size_bytes = 0
            items.append({
                "name": entry.name,
                "path": rel_path,
                "type": "file",
                "size": size_bytes,
                "extension": entry.suffix.lower()
            })

    return items


@router.get("")
def list_files() -> Dict[str, Any]:
    """Returns the full hierarchical file tree of the workspace."""
    base = settings.WORKSPACE_DIR.resolve()
    tree = _scan_directory(base, base)
    return {
        "workspace": str(base),
        "files": tree
    }


@router.get("/content")
async def get_file_content(path: str = Query(..., description="Relative path in workspace")) -> Dict[str, Any]:
    """Reads file text content from the workspace (size-capped)."""
    target = _resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Target is a directory.")

    try:
        if target.stat().st_size > settings.MAX_CONTENT_READ_BYTES:
            raise HTTPException(
                status_code=413,
                detail="File is too large to open in the editor. Use the download button instead.",
            )
        async with aiofiles.open(target, mode="r", encoding="utf-8", errors="replace") as f:
            content = await f.read()
        return {
            "path": path,
            "content": content,
            "mtime": _file_mtime(target),
        }
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read file.")


@router.post("/save")
async def save_file(req: FileSaveRequest) -> Dict[str, Any]:
    """Saves file content atomically (temp file + rename). When expected_mtime
    is provided and the file on disk is newer, responds 409 so the client can
    ask the user before overwriting external changes."""
    target = _resolve_safe_path(req.path)

    if req.expected_mtime is not None and target.exists():
        if _file_mtime(target) > req.expected_mtime + 0.001:
            raise HTTPException(
                status_code=409,
                detail="File changed on disk after it was loaded. Overwrite?",
            )

    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = target.parent / f".{target.name}.gconnect-tmp-{uuid.uuid4().hex[:8]}"
    try:
        async with aiofiles.open(tmp_path, mode="w", encoding="utf-8") as f:
            await f.write(req.content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Failed to write file.")

    return {
        "status": "success",
        "path": req.path,
        "bytes_written": len(req.content.encode("utf-8")),
        "mtime": _file_mtime(target),
    }


@router.post("/create")
def create_item(req: FileCreateRequest) -> Dict[str, Any]:
    """Creates a new empty file or directory."""
    target = _resolve_safe_path(req.path)

    if target.exists():
        raise HTTPException(status_code=409, detail="File or directory already exists.")

    try:
        if req.is_directory:
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
        return {
            "status": "success",
            "path": req.path,
            "is_directory": req.is_directory
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to create item.")


@router.delete("/delete")
def delete_item(req: FileDeleteRequest) -> Dict[str, Any]:
    """Deletes a file or directory from the workspace."""
    target = _resolve_safe_path(req.path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="Item not found.")

    try:
        if target.is_dir():
            import shutil
            shutil.rmtree(target)
        else:
            target.unlink()
        return {
            "status": "success",
            "path": req.path
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete item.")


@router.post("/rename")
def rename_item(req: FileRenameRequest) -> Dict[str, Any]:
    """Renames or moves a file/directory within the workspace."""
    old_target = _resolve_safe_path(req.old_path)
    new_target = _resolve_safe_path(req.new_path)

    if not old_target.exists():
        raise HTTPException(status_code=404, detail="Source item not found.")
    if new_target.exists():
        raise HTTPException(status_code=409, detail="Destination path already exists.")

    try:
        new_target.parent.mkdir(parents=True, exist_ok=True)
        old_target.rename(new_target)
        return {
            "status": "success",
            "old_path": req.old_path,
            "new_path": req.new_path
        }
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to rename item.")


@router.get("/raw")
def get_raw_file(path: str = Query(..., description="Relative path in workspace")):
    """Serves raw file binary (e.g. audio files) with appropriate MIME type."""
    target = _resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Target is a directory.")

    ext = target.suffix.lower()
    mime_map = {
        ".wav": "audio/wav",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".flac": "audio/flac",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
    }
    media_type = mime_map.get(ext, mimetypes.guess_type(str(target))[0] or "application/octet-stream")

    return FileResponse(target, media_type=media_type)


@router.get("/download")
def download_file_or_zip(path: str = Query("", description="Relative path in workspace")):
    """Downloads a single file as an attachment, or zips a folder/entire
    workspace to a temp file and streams it (memory-safe for large workspaces;
    the temp file is removed after the response is sent)."""
    target = _resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File or folder not found.")

    if target.is_file():
        return FileResponse(
            target,
            filename=target.name,
            headers={"Content-Disposition": f'attachment; filename="{target.name}"'}
        )

    # Target is directory -> Zip on disk (not in RAM) excluding IGNORED_NAMES
    base = settings.WORKSPACE_DIR.resolve()
    zip_name = "gconnect_workspace.zip" if target == base else f"{target.name}.zip"

    tmp_fd, tmp_name = tempfile.mkstemp(prefix="gconnect-zip-", suffix=".zip")
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)

    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(target, followlinks=False):
                dirs[:] = [d for d in dirs if d not in IGNORED_NAMES]
                for file in files:
                    if file in IGNORED_NAMES:
                        continue
                    file_path = Path(root) / file
                    arcname = file_path.relative_to(target)
                    zf.write(file_path, arcname)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail="Failed to archive workspace.")

    from fastapi.responses import StreamingResponse
    from starlette.background import BackgroundTask

    def _cleanup():
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    file_like = open(tmp_path, "rb")

    def _iter_file():
        with file_like:
            while True:
                chunk = file_like.read(1024 * 1024)
                if not chunk:
                    break
                yield chunk

    return StreamingResponse(
        _iter_file(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
        background=BackgroundTask(_cleanup),
    )
