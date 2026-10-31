# GConnect Changelog

## 2.2.2 (2026-10-31) - Real-time Connected Devices Tracker, Command Contrast, Status Bar & Editor Caret

### Real-Time Connected Devices Tracker for GPU Sharing
- Added thread-safe `ConnectionTracker` in `backend/connection_tracker.py` that accurately monitors all clients and devices connected to a GPU host.
- Physical device grouping via persistent `gconnect_client_id` in `localStorage` ensures multiple tabs or terminal windows from the same teammate are accurately counted as 1 distinct device.
- Automatic local vs remote categorization: clearly differentiates between **This PC (Host)** and **Remote Teammates** connecting over LAN or Tailscale.
- Parses client platforms and operating systems (e.g., `Mac (Chrome)`, `Windows PC (Edge)`, `Linux (Firefox)`, `Python SDK`).
- Live host dashboard integration: added **"Connected Devices"** card in the "Share My GPU" modal (`#shareDevicesCard`), top header action badge (`#shareBtnBadge`), and bottom status bar indicator (`#statusBarClients`).
- Added endpoints `GET /api/host/connected-devices` and included real-time device stats in `GET /api/host/share-info`.

### Terminal Visual Contrast & Command Echo
- Colorized shell prompt (`PS1`): Bold Cyan hostname (`\e[1;36m`) + Bold Green working directory (`\e[1;32m`) + Bold White command cursor (`\e[1;37m`).
- Output reset: Attached `trap 'printf "\e[0m"' DEBUG` in bash so typed commands display in high-contrast Bold White, while command outputs immediately print in clean, legible gray (`#c9d1d9`).
- Host fallback support: Configured `$E[1;36m` ANSI codes for Windows `cmd.exe` and `PROMPT_COMMAND` for host bash in `backend/pty_bridge.py`.

### Editor Cursor Pulse & Smooth Caret in Monaco
- Enabled `cursorBlinking: "blink"` for a distinct, sharp visual pulse.
- Enabled `cursorSmoothCaretAnimation: "on"` for fluid cursor movement when typing, navigating lines, or placing the caret with the mouse.
- Unobstructed pointer events across Monaco container.

### Workspace File Import Button & Multipart Upload API
- Added **"Import File(s)"** button with `<input type="file" multiple>` in the sidebar header for 1-click workspace ingestion.
- Implemented `POST /api/files/upload` backend endpoint with strict path traversal defense and filename sanitization.

### VS Code-Style Bottom Status Bar (24px Elevation)
- Created permanent 24px bottom status bar (`#appStatusBar`) elevating the terminal container above the Windows taskbar with zero collision.
- Displays live cursor coordinates (`Ln X, Col Y`), encoding (`UTF-8`), language (`Python`), container sandbox status (`Docker Sandbox (Running)`), and problems count.

### Smooth Terminal Scrollbar & Mouse Context Menu
- Enabled `smoothScrollDuration: 150` in xterm.js with custom dark scrollbar styles matching Monaco.
- Floating terminal context menu with **Copy**, **Paste**, **Select All**, and **Clear Terminal**.
- Integrated copy-on-select functionality.

## 2.2.1 (2026-10-26) - Turnkey 1-Click GPU Sharing, Real IP Invites, Single Tray & Non-blocking Docker

### Turnkey GPU Sharing & Invite Generator
- Added **"⚡ Share My GPU"** host dashboard: automatically queries system network adapters
  and resolves the host's actual reachable IP (prioritizing Tailscale `100.x.x.x`
  or primary LAN `192.168.x.x`). Teammate invites never output `127.0.0.1`.
- Live host dashboard displays GPU model, VRAM totals, current network IPs, and active
  token with a 1-click **"📋 Copy Invite for Teammate"** button.
- Custom token support: allows hosts to configure a persistent friendly passcode directly
  from the UI without touching `.env` files.
- Smart Invite auto-parsing: teammates clicking "Connect to GPU" can paste the full multi-line
  invite snippet directly into a single box; GConnect automatically extracts and populates
  the Host URL and Token.

### Mode-Driven Auto-Switching & In-App Navigation
- Added mode-driven auto-switching in the desktop app:
  - Choosing **"Develop on This PC"** keeps `ALLOW_LAN=false` (`127.0.0.1`) for private local development.
  - Clicking **"⚡ Share My GPU"** acts as explicit consent: GConnect automatically launches the backend
    with `CONNECT_ALLOW_LAN=true` and `CONNECT_HOST=0.0.0.0`.
- Added **"Share GPU"** button to the top header actions and as a first-class choice in the
  Connection modal (`#connectionOverlay`), allowing dynamic mode transitions without app restarts.

### Windows Firewall Detection & 1-Click UAC Elevation
- Added `/api/host/firewall-status` endpoint querying `netsh advfirewall` to verify whether inbound
  TCP ports 8000 and 8001 are open.
- Added `/api/host/firewall-rule` endpoint and Tauri `request_firewall_rule` IPC handler spawning
  an elevated PowerShell UAC prompt to add firewall exceptions with one click.

### Single System Tray Fix
- Removed redundant declarative `"trayIcon"` block from `src-tauri/tauri.conf.json`.
- Windows now registers only the single programmatic tray icon in `src-tauri/src/main.rs`,
  resolving the issue of two tray icons appearing on startup.

### Non-blocking Docker Sandbox Initialization
- Container initialization in `backend/main.py` moved to an asynchronous background task.
- Added cached image inspection (`docker.client.images.get`) before triggering pulls.
- Desktop UI and Web IDE load immediately in `< 1s` without freezing during the initial
  9.5 GB PyTorch image download.

### IDE Layout & Terminal Shortcuts
- **Editor Tab Bar & Monaco Layout Pinning**: Fixed editor layout clipping where file tabs were pushed off-screen when scrolling down or covered the top line when scrolling up. Locked `.editor-tabs-header` at `flex: 0 0 35px; z-index: 5` and bounded `.monaco-container` with `flex: 1 1 0%; min-height: 0;` (VS Code layout pattern).
- **Terminal Bottom Safe-Padding & Auto-Scroll**: Sized `.xterm-container` with `flex: 1 1 0%; min-height: 0; padding: 6px 10px 16px 10px;` and `lineHeight: 1.15`, preventing the last line or shell prompt from colliding with the bottom border or Windows taskbar. Automatically calls `term.scrollToBottom()` on incoming WebSocket output.
- **Terminal Keyboard Shortcuts & Mouse Paste**:
  - `Ctrl+C` / `Cmd+C`: Copies selected terminal text to clipboard. Passes through `SIGINT` (`^C`) if no text is selected.
  - `Ctrl+V` / `Ctrl+Shift+V` / `Shift+Insert`: Reads clipboard and pastes directly to the terminal PTY.
  - **Right-Click Paste**: Right-clicking inside the terminal now copies (if selection exists) or pastes clipboard content directly into the prompt (matching Windows Terminal / VS Code).

### Packaging & Test Coverage
- Sidecar binary PyInstaller specification updated with `backend.network_utils`.
- Full standalone NSIS installer re-compiled: `GConnect_2.2.1_x64-setup.exe` (35.18 MiB).
- Expanded backend test suite with `tests/test_host_share.py` (58 passed, 1 skipped, 0 failures).

## 2.2.0 (2026-10-25) - Desktop remote mode, PROBLEMS panel, editor linting, honest host telemetry

### Desktop app: connect to a REMOTE GPU workstation
- The windowed app is no longer limited to the local machine. First launch
  shows a **connection screen**: "This PC" (local sidecar sandbox) or
  "Remote GPU PC" (daemon URL + token). The choice persists in
  `connection.json`; a header **Connection** button switches anytime.
- Rust shell: persisted connection config, `set_connection` IPC, remote
  reachability probe, mode-aware `backend_info`. Fresh installs create the
  app-data dir on first save (the picker previously failed silently there).
- Backend: CORS grant scoped to the Tauri webview origins only (preflight +
  response headers) so the webview can talk to a remote daemon; every other
  cross-origin caller stays rejected. CSP widened to remote http/ws hosts +
  media streaming.

### PROBLEMS tab (VS Code-style) + editor linting
- New bottom-panel tabs: TERMINAL / **PROBLEMS** with a red count badge.
- **Editor diagnostics**: `/api/lint` runs `ast.parse` (syntax errors) and
  **pyflakes** (undefined names, unused imports, ...) on the editor content.
  Results drive red/yellow Monaco squiggles AND clickable Problems entries
  (file:line click-through jumps to the line). Debounced on typing and tab
  switch; non-Python files are skipped.
- **Runtime errors**: Python tracebacks are parsed from the terminal stream
  (ANSI-stripped, chained frames handled) into the same list under
  "Terminal output". A new `python` run clears only runtime entries.
- Grouped per file, warning vs error icons, entries sync on file
  rename/delete.

### Honest host telemetry (no-GPU machines)
- Machines without an NVIDIA GPU now show **CPU + RAM** in the telemetry bar
  (psutil; CPU model from registry/procfs) instead of a zeroed GPU panel.
  TEMP slot hidden; NO GPU badge stays. Real GPUs still show GPU stats.
- Cluster panel: GPU-less nodes list CPU/RAM instead of "No GPU detected".

### Docker-unavailable handling
- Yellow banner under the header when Docker/sandbox is down (10s polling,
  Retry + dismiss, remote-vs-local wording); **Run Script** pops a clear
  "Sandbox Not Available" dialog instead of silently sending commands into a
  dead terminal. Terminal WS closes with code 4500 so the client stops
  reconnect-looping and shows a "No Sandbox" badge.

### IDE polish
- **Resizable sidebar** (150-520px, double-click resets) with VS Code-style
  divider.
- Scroll containment (`overscroll-behavior`): scrolling the terminal or file
  tree can no longer push the editor tabs/header out of view.
- Immutable-lifecycle fixes: connection errors are now surfaced in the modal
  instead of a hidden div.

### Repo / packaging
- Build files classified: `backend_entry.py` + `gconnect_backend.spec` moved
  to `packaging/` (SPECPATH-relative, CWD-independent build).
- Ready-made Windows installer published at the repo root
  (`GConnect_2.2.0_x64-setup.exe`).
- Legacy tray app removed (`scripts/tray_app.py`, `start_tray.bat`,
  `make_icon.py`; `pystray`/`Pillow` dropped) - superseded by the desktop app.
- Playwright **E2E suite** added (`e2e/`, `npm run test:e2e`): auth flows,
  IDE file lifecycle, telemetry mode, Docker-banner warning, PROBLEMS,
  cluster panel, sidebar resize. Backend suite grew to **53 tests** (lint +
  CORS + host telemetry), E2E **15 tests**.
- Dependencies: `psutil`, `pyflakes` added; `pystray`/`Pillow` removed.


## 2.1.3 (2026-10-24) — Realtime self-healing telemetry stream

**Reported:** "abhi bhi correct data show nahi kar raha aur realtime mein bhi
nahi hai" — the UI sat at zeros with a red dot while Task Manager showed
6% / 3.0 GB.

- **Root cause:** the telemetry **WebSocket** stream could freeze if the
  background broadcaster task died or wedged — the socket stayed open but no
  snapshots arrived, and the UI never recovered. (All previous verification
  had exercised the HTTP endpoints; the WS path is now soak-tested directly.)
- **Self-healing stream:** if no broadcast arrives within 2s, the stream
  samples NVML directly and pushes it — a dead broadcaster can no longer
  freeze the UI, ever. `subscribe()` also auto-restarts a dead broadcaster.
- **NVML re-init:** if NVML failed to initialize at startup (driver not ready
  during boot), it is retried every 30s instead of showing "NO GPU" forever.
- **Realtime feel:** telemetry cadence doubled to **2 Hz** (0.5s). Verified
  with a 20s WebSocket soak: 41 snapshots, 0 mock, max gap 0.52s.
- **Broadcast hardening:** subscribers iterated over a copy (kills the
  "Set changed size during iteration" failure mode); failures logged as
  warnings, not silently debugged.
- **UI:** "LOAD" renamed to **"UTILIZATION**" (per user request); STALE
  detection now also arms at connect time (server-silent connections are
  flagged instead of looking normal).

## 2.1.2 (2026-10-24) — GPU LOAD metric + freshness indicator

**Reported:** "GPU ka correct usage show nahi kar raha ya bahut time lag raha
hai" — screenshot showed GConnect LOAD 4% while Task Manager showed 14% and a
GPU overlay ~10% for the same moment.

- **Root cause (usage mismatch):** NVML's `utilizationRates.gpu` is SM
  (compute-shader) utilization only. Desktop workloads (DWM compositing,
  copies, video) barely use SMs, so SM-only numbers read far below Task
  Manager, which reports the busiest engine. **Fix:** LOAD is now
  `max(SM, memory-controller)` — the same convention GPU overlay tools use —
  with the pure SM figure preserved (`sm_utilization_percent`) and shown in
  hover tooltips ("Load 14% (SM 4% · MemCtl 14%)"). Verified live in both
  directions (MC-dominated and SM-dominated moments).
- **Frozen-telemetry bug (the "time lag"):** a subscriber whose queue filled
  (webview busy >4s) was discarded **forever** — the WebSocket stayed open
  but the UI froze at old values. **Fix:** the broadcaster now drops the
  oldest snapshot and always delivers the newest; subscribers are never
  abandoned.
- **Freshness indicator:** a pulsing green dot next to the GPU name shows
  data is flowing; if no snapshot arrives for >3s it stops pulsing and a red
  "STALE" badge appears — lag is now instantly visible instead of guessable.
- Telemetry cadence remains 1 Hz with instant first snapshot on connect.

## 2.1.1 (2026-10-24) — Screenshot-bug fixes: telemetry flip-flop & port conflicts

**Reported via screenshots:** the telemetry pill intermittently showed the
real GPU name together with a "NO GPU" badge and 0.0/0.0 GB, while the
Cluster button simultaneously reported 11.9 GB; the Cluster modal sometimes
showed "No GPU detected / 0.0 GB pooled" for the same machine.

- **Root cause 1 — NVML is not thread-safe:** `get_snapshot()` was called
  concurrently from the telemetry broadcaster, `/api/telemetry/snapshot` and
  `/api/cluster/status` (all different threads). Concurrent NVML calls fail
  intermittently → honest-fallback zeros → flip-flop. **Fix:** all NVML
  sampling serialized behind a lock. Verified with 1,600-call and 6,780-call
  concurrent stress runs: zero mock flips.
- **Fix 2 — last-good cache (5s TTL):** a transient NVML error now serves the
  last real values instead of flipping the UI to zeros. When the cache
  expires without recovery the payload is honest zeros with a new
  `degraded: true` flag → UI shows "NO SIGNAL" (GPU exists, sensor lost)
  instead of the misleading "NO GPU".
- **Root cause 2 — stale orphaned daemon on port 8000:** an orphan from a
  pre-watchdog build was still serving port 8000 with an old token, while the
  current app's sidecar had correctly moved to another port — leading to 401s
  and contradictory UI states. **Fix:** the desktop app now writes
  `runtime/backend.json` (`{"port", "pid"}`) so the actual daemon location is
  always discoverable; one-time cleanup of orphans.
- **Fix 3 — port 8001 reservation:** the daemon's free-port search now skips
  8001 (reserved for the container's published voice-inference stream), so
  the daemon can never collide with Docker's port publish.

## 2.1.0 (2026-10-24) — Real GPU telemetry + multi-PC GPU Cluster

### Real telemetry (dummy data removed)
- Root cause of the "RTX 4090 (Simulated)" dummy: `pynvml` was never installed
  in the sidecar build environment, so NVML never initialized. Now the
  maintained `nvidia-ml-py` package is bundled — the desktop app reports the
  machine's **real GPU** (verified live: RTX 5070 Ti Laptop, per-GPU VRAM/util/temp).
- The fake sine-wave fallback is gone. Without an NVIDIA GPU the app now
  honestly shows zeros + a "NO GPU" badge instead of fabricated numbers.
- Multi-GPU machines: summary fields are aggregates across ALL GPUs; full
  per-GPU detail is sent in `gpus[]` and surfaced in the UI (name ×N, hover
  details). The sandbox already passes every GPU to the container
  (`DeviceRequest(count=-1)`).

### GPU Cluster (the core mission: pool real GPUs from multiple PCs)
- New **Cluster panel** in the app: register remote GConnect daemons
  (name + URL + token) running on your other PCs (Tailscale/LAN); live
  per-node status, per-GPU stats, and **pooled VRAM across all nodes**.
- Backend: `backend/cluster_manager.py` + `GET /api/cluster/status`,
  `GET/POST/DELETE /api/cluster/nodes`. Node tokens are stored in
  `.gconnect/nodes.json` (owner-only) and never returned by the API.
- Verified live: registering a second daemon node → cluster showed 2 nodes /
  2 GPUs / 23.9 GB pooled VRAM; offline nodes degrade gracefully.
- Distributed compute helpers: `workspace/cluster/` — `ray_node.py`
  (`--head` / `--join` to pool GPUs across PCs via Ray), `train_ddp.py`
  (torchrun DDP for multi-GPU on one PC), plus a README explaining honest
  pooling options (Ray / torchrun / accelerate `device_map="auto"`).
- Header now shows a live cluster summary button (`N/M PCs · GB`).

### Tests
- 38 tests (6 new: registry CRUD + persistence + validation + offline-node
  status + honest no-GPU fallback + API endpoint flows).

Project renamed **Connect → GConnect** and converted into a desktop
application (Tauri v2) while keeping the original host-daemon mode.

### Security fixes
| ID | Fix |
|---|---|
| SEC-01 | Token authentication on every `/api/*` and `/ws/*` route (Bearer header or `?token=`). Default bind changed `0.0.0.0` → `127.0.0.1`; LAN exposure is opt-in (`CONNECT_ALLOW_LAN=true`). Auto-generated secrets persisted to `.gconnect/auth_token` with owner-only permissions; `CONNECT_AUTH_TOKEN` env var supported (`.env.example` added). |
| SEC-02 | Host-shell fallback (`cmd.exe`/`bash` on the HOST when Docker is down) now disabled by default; opt-in via `CONNECT_ALLOW_LOCAL_FALLBACK=true`. Feature retained, gated. |
| SEC-03 | XSS fixed: file/tab names escaped (`escapeHtml`) in `filetree.js` and `editor.js`. |
| SEC-04 | `CORSMiddleware(allow_origins=["*"])` removed; same-origin + Tauri-origin enforcement added in the auth middleware (CSRF/DNS-rebinding defense). Tests cover evil-origin rejection. |
| SEC-05 | GPU capability check fixed (the else-branch previously set `True` unconditionally): nvidia runtime **or** NVIDIA CDI specs; GPU-enabled container creation now falls back to CPU with a loud surfaced reason instead of failing opaquely. |
| SEC-06 | `/api/files/content` size cap (HTTP 413); generic 500 messages (no internal path leakage); OpenAPI/docs endpoints disabled; uvicorn access logs disabled (they would leak `?token=`). |
| SEC-07 | Terminal control protocol redefined: binary WebSocket frames = JSON control (resize), text frames = raw PTY input. No more content sniffing. |
| SEC-08 | Daemon logs never include query strings. |
| SEC-09 | `.gconnect/` runtime dir git-ignored; no secrets in code; `.env.example` provided. |

### Bug fixes
| ID | Fix |
|---|---|
| BUG-01 | Stale-editor data loss: non-dirty tabs refresh from disk on re-select; saves carry `expected_mtime` and the backend answers HTTP 409 on external modification (user chooses overwrite/reload). |
| BUG-02 | WebSocket/PTY session leak: `terminalManager.connect()` is idempotent (tears down the previous socket); telemetry client likewise; restart flow no longer stacks sessions. |
| BUG-03 | Container config reconciliation: image/shm/ports/cache-mode fingerprint stored as a container label; mismatch triggers automatic recreate (workspace volume preserved). |
| BUG-04 | `test_wipe_model_cache` no longer deletes the user's real 9 GB model cache — all tests run against an isolated temp workspace (`tests/conftest.py`). |
| BUG-05 | Telemetry: single background broadcaster samples NVML via `run_in_executor` (never blocks the event loop); per-client fan-out queues; multi-GPU `gpus[]` array added. |
| BUG-06 | Cache wipe reports bytes actually deleted (not pre-computed), returns per-entry errors with `status: "partial"`, warns when the container is running; Python 3.12 `onexc` used when available. |
| BUG-07 | Tree scan: symlink-cycle guard (visited set on realpath), symlinked directories skipped entirely, depth (24) and entry (20k) caps. |
| BUG-08 | Workspace ZIP streamed from a temp file (background cleanup) instead of an in-RAM `BytesIO`. |
| BUG-09 | Atomic file writes (temp + `os.replace`); no `.gconnect-tmp` residue on success. |
| BUG-10 | Blank-editor Monaco model leak fixed (single reused model). |
| BUG-11 | `is_mock` telemetry now renders a visible "SIMULATED" badge (dead ternary removed). |
| BUG-12 | Rename UI added to the file tree (uses the previously dead `/api/files/rename`). |
| BUG-13 | Deleting a dirty open file no longer double-confirms (`closeTab(force)`). |
| BUG-14 | All frontend libraries vendored locally at pinned versions (Monaco 0.45.0, xterm 5.3.0, fit 0.8.0, web-links 0.9.0, lucide 0.454.0); `lucide@latest` unpinned CDN reference removed. App works fully offline. |
| BUG-15 | Setup scripts and tray app honor `CONNECT_PORT`/`CONNECT_HOST`/`CONNECT_ALLOW_LAN`. |
| BUG-16 | Tray-spawned daemon logs to `.gconnect/logs/tray-daemon.log` instead of `/dev/null`. |
| BUG-17 | Windows fallback shell notice explains why no shell is provided (fallback gated per SEC-02). |
| BUG-18 | Run Script refuses non-`.py` files. |

### Dependency changes
- `python-multipart` **removed** (unused; had 6 open advisories).
- `Pillow` floor raised to `>=13.0.0` (60+ CVEs in the 11.x line; tray icon only).
- `pytest` floor raised to `>=9.0.3` (PYSEC-2026-1845).
- Floors refreshed: fastapi ≥0.115, uvicorn ≥0.34, docker ≥7.1, pydantic ≥2.10, httpx ≥0.28.
- Frontend: CDN references replaced by vendored pinned packages.

### Desktop app (Tauri v2)
- `src-tauri/`: GConnect window (1400×900, min 940×600), system tray
  (open / copy URL / restart daemon / quit), single-instance lock.
- Daemon bundled as a PyInstaller **sidecar** (`binaries/gconnect-backend`),
  spawned on a free loopback port by the Rust shell; the webview receives
  `{port, token}` only via the `backend_info` IPC command — no secrets in
  localStorage, no broad capabilities.
- Strict CSP (`self` only + loopback connect/ws + blob workers), devtools
  disabled in production, capability file limited to `core:default`.
- Icons generated for Windows/macOS/Linux (`scripts/make_icon.py` + `npx tauri icon`).

### Tests
- 32 tests (auth, origin policy, traversal, mtime conflicts, atomic writes,
  symlink skipping, GPU-check logic, config fingerprint, isolated wipe,
  streaming zip, rename, fallback gating) — suite green.

### Removed / changed behavior (explicit)
- No feature removed. The host-shell terminal fallback is **off by default**
  (opt-in env var) — this is the security fix, not a removal.
- `/docs`, `/redoc`, `/openapi.json` no longer served.
- Root `/` now redirects to `/static/index.html` (relative asset paths work in
  both daemon and desktop modes).
- Workspace ZIP download renamed `connect_workspace.zip` → `gconnect_workspace.zip`.

### Post-release fixes during Phase 4 verification
- Sidecar: frontend assets now bundled into the PyInstaller binary
  (`datas=[("frontend","frontend")]` + `sys._MEIPASS` resolution) so the exe is
  self-contained.
- Daemon: auth token resolved **eagerly** at startup (desktop launchers read
  the token file right after `/api/health`; lazy generation would deadlock them).
- Desktop: sidecar runs a **parent-PID watchdog** — if the app is force-killed
  or crashes, the daemon exits within ~3s instead of orphaning a listener.
