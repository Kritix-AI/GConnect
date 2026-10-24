# GConnect Changelog

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
