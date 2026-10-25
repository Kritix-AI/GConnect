"""Static Python diagnostics for the PROBLEMS panel.

Two sources, no heavy language server:
- ast.parse for syntax errors (always available, exact line/col)
- pyflakes for semantic warnings (undefined names, unused imports, ...)

Runs against the EDITOR CONTENT (not the file on disk), so unsaved edits
are checked too. Results feed both the editor's red/yellow squiggles
(Monaco markers) and the Problems list.
"""

import ast
import asyncio
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("connect.linter")

router = APIRouter(prefix="/api/lint", tags=["lint"])

MAX_LINT_BYTES = 512 * 1024

try:
    from pyflakes.api import check as pyflakes_check
    HAS_PYFLAKES = True
except ImportError:
    HAS_PYFLAKES = False
    logger.warning("pyflakes not installed - linting falls back to syntax errors only.")

# pyflakes findings that indicate broken code (vs style/unused warnings).
PYFLAKES_ERROR_CLASSES = {
    "UndefinedName",
    "UndefinedLocal",
    "UndefinedExport",
    "DuplicateArgument",
    "ReturnOutsideFunction",
    "YieldOutsideFunction",
    "ContinueOutsideLoop",
    "BreakOutsideLoop",
    "TwoStarredExpressions",
    "RaiseNotImplemented",
}


class LintRequest(BaseModel):
    path: str = ""
    content: str


class _Collector:
    """Duck-typed pyflakes Reporter that collects structured diagnostics."""

    def __init__(self) -> None:
        self.diagnostics: List[Dict[str, Any]] = []

    def unexpectedError(self, filename: str, msg: str) -> None:  # noqa: N802
        self.diagnostics.append({
            "line": 1, "col": 1, "severity": "error",
            "message": msg, "source": "pyflakes",
        })

    def syntaxError(self, filename: str, msg: str, lineno, offset, text) -> None:  # noqa: N802
        self.diagnostics.append({
            "line": lineno or 1, "col": offset or 1, "severity": "error",
            "message": f"SyntaxError: {msg}", "source": "pyflakes",
        })

    def flake(self, message: Any) -> None:
        severity = "error" if type(message).__name__ in PYFLAKES_ERROR_CLASSES else "warning"
        self.diagnostics.append({
            "line": message.lineno or 1,
            "col": (message.col or 0) + 1,  # pyflakes cols are 0-based
            "severity": severity,
            "message": message.message % message.message_args,
            "source": "pyflakes",
        })


def lint_python(content: str) -> List[Dict[str, Any]]:
    diagnostics: List[Dict[str, Any]] = []
    try:
        ast.parse(content)
    except SyntaxError as e:
        diagnostics.append({
            "line": e.lineno or 1,
            "col": e.offset or 1,
            "severity": "error",
            "message": f"SyntaxError: {e.msg}",
            "source": "python",
        })
        return diagnostics  # pyflakes would only repeat the syntax error

    if HAS_PYFLAKES:
        collector = _Collector()
        try:
            pyflakes_check(content, "editor.py", reporter=collector)
            diagnostics.extend(collector.diagnostics)
        except Exception as e:
            logger.warning("pyflakes run failed: %s", e)

    return diagnostics


@router.post("")
async def lint_code(req: LintRequest) -> Dict[str, Any]:
    if len(req.content.encode("utf-8")) > MAX_LINT_BYTES:
        raise HTTPException(status_code=413, detail="File too large to lint.")
    loop = asyncio.get_running_loop()
    diagnostics = await loop.run_in_executor(None, lint_python, req.content)
    return {"path": req.path, "diagnostics": diagnostics}
