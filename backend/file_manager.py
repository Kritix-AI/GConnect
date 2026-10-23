import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.config import settings

router = APIRouter(prefix="/api/files", tags=["files"])

IGNORED_NAMES = {".git", ".hf_cache", "__pycache__", ".venv", ".pytest_cache", ".vscode"}


class FileSaveRequest(BaseModel):
    path: str
    content: str


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
    # Reject absolute paths, leading slashes, and Windows drive letters
    if rel_path.startswith(("/", "\\")) or Path(rel_path).is_absolute() or ":" in rel_path:
        raise HTTPException(status_code=403, detail="Access denied: absolute path forbidden.")

    base = settings.WORKSPACE_DIR.resolve()
    target = (base / rel_path).resolve()

    try:
        common = os.path.commonpath([str(base), str(target)])
        if common != str(base):
            raise HTTPException(status_code=403, detail="Access denied: path traversal detected.")
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied: invalid path location.")

    return target


def _scan_directory(current_dir: Path, base_dir: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    try:
        entries = sorted(list(current_dir.iterdir()), key=lambda e: (not e.is_dir(), e.name.lower()))
    except Exception:
        return items

    for entry in entries:
        if entry.name in IGNORED_NAMES:
            continue

        rel_path = str(entry.relative_to(base_dir)).replace("\\", "/")

        if entry.is_dir():
            items.append({
                "name": entry.name,
                "path": rel_path,
                "type": "directory",
                "children": _scan_directory(entry, base_dir)
            })
        else:
            size_bytes = entry.stat().st_size
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
    """Reads file text content from the workspace."""
    target = _resolve_safe_path(path)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Target is a directory.")

    try:
        async with aiofiles.open(target, mode="r", encoding="utf-8", errors="replace") as f:
            content = await f.read()
        return {
            "path": path,
            "content": content
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {e}")


@router.post("/save")
async def save_file(req: FileSaveRequest) -> Dict[str, Any]:
    """Saves file content to the workspace safely."""
    target = _resolve_safe_path(req.path)

    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with aiofiles.open(target, mode="w", encoding="utf-8") as f:
            await f.write(req.content)
        return {
            "status": "success",
            "path": req.path,
            "bytes_written": len(req.content.encode("utf-8"))
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create item: {e}")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to delete item: {e}")


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
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to rename item: {e}")
