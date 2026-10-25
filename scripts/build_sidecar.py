"""Builds the GConnect daemon sidecar with PyInstaller and stages it under
src-tauri/binaries/ with the Rust target triple suffix Tauri requires.

Usage:
    python scripts/build_sidecar.py            # build + stage
    python scripts/build_sidecar.py --clean    # remove build artifacts
"""

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC_FILE = REPO_ROOT / "packaging" / "gconnect_backend.spec"
BINARIES_DIR = REPO_ROOT / "src-tauri" / "binaries"
EXE_NAME = "gconnect-backend"


def rust_target_triple() -> str:
    try:
        out = subprocess.run(
            ["rustc", "-vV"], capture_output=True, text=True, check=True
        ).stdout
        for line in out.splitlines():
            if line.startswith("host:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    # Fallback guesses
    if platform.system() == "Windows":
        return "x86_64-pc-windows-msvc"
    if platform.system() == "Darwin":
        return "aarch64-apple-darwin" if platform.machine() == "arm64" else "x86_64-apple-darwin"
    return "x86_64-unknown-linux-gnu"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    if args.clean:
        for d in [REPO_ROOT / "build", REPO_ROOT / "dist"]:
            if d.exists():
                shutil.rmtree(d)
        BINARIES_DIR.mkdir(parents=True, exist_ok=True)
        for f in BINARIES_DIR.glob(f"{EXE_NAME}*"):
            f.unlink()
        print("Cleaned sidecar build artifacts.")
        return 0

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "pyinstaller"],
        check=True,
    )

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC_FILE),
         "--noconfirm",
         "--distpath", str(REPO_ROOT / "dist"),
         "--workpath", str(REPO_ROOT / "build")],
        cwd=REPO_ROOT,
        check=True,
    )

    triple = rust_target_triple()
    ext = ".exe" if platform.system() == "Windows" else ""
    built = REPO_ROOT / "dist" / f"{EXE_NAME}{ext}"
    if not built.exists():
        print(f"ERROR: expected build output missing: {built}", file=sys.stderr)
        return 1

    BINARIES_DIR.mkdir(parents=True, exist_ok=True)
    staged = BINARIES_DIR / f"{EXE_NAME}-{triple}{ext}"
    shutil.copy2(built, staged)
    print(f"Sidecar staged: {staged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
