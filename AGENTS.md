# GConnect Multi-Agent Development Guide (AGENTS.md)

This repository is **GConnect**, a self-hosted universal remote GPU development platform featuring a FastAPI/Uvicorn backend sidecar, a Tauri (Rust + WebView2) desktop application, and a Monaco/Xterm.js Web IDE.

---

## 1. Project Architecture Overview

```
GConnect/
├── frontend/               # Single-page Web IDE (HTML5, Vanilla JS ES6+, CSS3)
│   ├── js/                 # Modular managers: app.js, auth.js, filetree.js, terminal.js, share.js, audio.js
│   ├── css/                # Dark-themed IDE styles matching VS Code aesthetics
│   └── index.html          # Viewport: Workspace sidebar, Monaco editor, Xterm terminal, Meet-style modals
├── backend/                # Python FastAPI daemon / PyInstaller standalone sidecar
│   ├── main.py             # FastAPI entrypoint, telemetry broadcaster, cluster endpoints
│   ├── auth.py             # Bearer authentication, token persistence, and Tauri CORS preflight whitelist
│   ├── docker_manager.py   # Container lifecycle, physical GPU hardware probe, self-healing reconnect
│   ├── file_manager.py     # Safe filesystem access, traversal protection, atomic saves, directory tree
│   ├── audio_router.py     # Port 8001 WebSocket hub, 50ms jitter buffer, duplex echo ducking gate
│   └── config.py           # Runtime settings, directories, and environment variables
├── src-tauri/              # Desktop application shell (Rust + Tauri v2)
│   ├── src/main.rs         # Sidecar lifecycle process manager, Windows firewall helper, IPC commands
│   ├── tauri.conf.json     # Window dimensions, sidecar binary packaging, security permissions
│   └── Cargo.toml          # Rust dependencies (serde, tokio, rfd)
├── tests/                  # Automated Pytest suite covering auth, cluster, fixes, and audio
└── packaging/ / scripts/   # PyInstaller sidecar builder and host deployment helpers
```

---

## 2. Team Roles & Specialized Agent Boundaries

Whenever working in **`/teamwork`** or subagent delegation mode, agents must operate within these dedicated specializations:

### 🎨 `frontend-agent`
* **Domain**: `frontend/` (`index.html`, `js/`, `css/`).
* **Core Responsibilities**: Monaco editor integration, Xterm.js terminal sessions, audio controls, file tree explorer, and Google Meet-style modals.
* **Non-Negotiable Rules**:
  1. Pure ES6+ and native Web APIs only—do NOT introduce heavy client-side frameworks (React, Vue, Angular).
  2. Every single HTTP `fetch()` MUST go through `window.authManager.api(path, options)` to guarantee valid Bearer token attachment and `X-Client-Id` header inclusion.
  3. All UI modals must be responsive, keyboard-accessible (Escape to close), and dark-themed.
  4. Audio streaming must downsample to 16 kHz 16-bit mono PCM in an `AudioWorklet` before wire transmission.

### ⚙️ `backend-agent`
* **Domain**: `backend/` (`main.py`, `file_manager.py`, `auth.py`, `audio_router.py`, `config.py`).
* **Core Responsibilities**: REST endpoints, WebSocket streaming hubs, authentication, and file operations.
* **Non-Negotiable Rules**:
  1. All non-public endpoints must enforce `auth.enforce_http_auth`.
  2. `backend/auth.py` must maintain `CORS_ALLOW_HEADERS = "Authorization, Content-Type, X-Client-Id, *"` for desktop WebView2 origins.
  3. Tokens must persist in `settings.RUNTIME_DIR / "auth_token"` across daemon restarts so existing sessions and custom tokens are never lost.
  4. File paths must strictly pass `_resolve_safe_path()` to prevent traversal attacks (`..`).

### 🐳 `docker-agent`
* **Domain**: `backend/docker_manager.py`, container provisioning.
* **Core Responsibilities**: Sandbox container lifecycle, GPU passthrough, and virtual audio devices.
* **Non-Negotiable Rules**:
  1. Always probe actual physical NVIDIA hardware (`pynvml`, `torch.cuda`, `nvidia-smi`) before issuing Docker `DeviceRequest`. Never assume `"nvidia"` in Docker runtime means a physical GPU is present (WSL2 bug).
  2. Implement thread-safe dynamic reconnect (`_ensure_docker`) with a 3-second cooldown to detect late Docker Desktop startups.
  3. Ensure container runs cleanly in CPU mode if no physical NVIDIA GPU exists.
  4. Configure virtual ALSA loopback devices so `speech_recognition`, `sounddevice`, and `pyaudio` discover default recording and playback devices out of the box.

### 🦀 `desktop-agent`
* **Domain**: `src-tauri/` (`src/main.rs`, `Cargo.toml`, `tauri.conf.json`).
* **Core Responsibilities**: Native Windows desktop shell, sidecar process supervision, and native OS dialogs.
* **Non-Negotiable Rules**:
  1. On sidecar restart or mode change, always enforce an `800ms` thread sleep before re-binding to prevent Windows socket `TIME_WAIT` port collisions.
  2. Always coordinate state through the `backend_info` IPC command so the frontend receives verified, online credentials.
  3. All native file/folder pickers must use non-blocking native dialogs (`rfd`).

### 🧪 `qa-release-agent`
* **Domain**: `tests/`, `README.md`, `docs/CHANGELOG.md`, `package.json`, release artifacts.
* **Core Responsibilities**: Regression testing, test suite execution, version synchronization, and build staging.
* **Non-Negotiable Rules**:
  1. All tests in `tests/` must pass with 0 failures before any release build or version tag.
  2. When bumping versions, update all **6 version files** simultaneously:
     - `package.json`
     - `src-tauri/tauri.conf.json`
     - `src-tauri/Cargo.toml`
     - `backend/main.py`
     - `README.md` (badge + download link)
     - `docs/CHANGELOG.md`
  3. Release tags must follow `vX.Y.Z` format on branch `v2`.

---

## 3. Standard Verification Checklist

Before marking any task as complete:
1. `.\.venv\Scripts\python.exe -m pytest tests/ -v` (Must pass with 0 failures).
2. `.\.venv\Scripts\python.exe scripts/build_sidecar.py` (Must generate standalone sidecar executable).
3. `npm run build` (Must produce NSIS installer in `src-tauri/target/release/bundle/nsis/`).
