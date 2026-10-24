# GConnect — Phase 1 Audit Report

Audit date: 2026-10-24. Scope: entire repository (backend, frontend, scripts, tests, docs).
This report consolidates the manual code review and automated dependency scanning
(`pip-audit`). Every issue below is addressed in Phase 2; the fix for each is listed.

Severity scale: **Critical** (remote code execution / data destruction),
**High** (silent malfunction, data loss, privilege escalation),
**Medium** (resource exhaustion, degraded behavior, weak hardening),
**Low** (code quality, dead code, cosmetics).

---

## A. Security Vulnerabilities

### SEC-01 — No authentication on any endpoint [Critical]
- **File:** `backend/main.py` (all routes), `backend/file_manager.py` (all routes), `backend/pty_bridge.py:20`.
- The daemon binds `0.0.0.0:8000` with zero authentication. Anyone who can reach the port can:
  read/write/delete all workspace files, download the entire workspace as ZIP,
  execute arbitrary shell commands inside the container via `/ws/terminal`,
  wipe the model cache, and restart the container. This is an unauthenticated
  remote shell.
- **Fix:** Token authentication (auto-generated high-entropy secret persisted with
  `0600`-style permissions, or supplied via `CONNECT_AUTH_TOKEN` env var).
  Required on every `/api/*` and `/ws/*` route via `Authorization: Bearer` header
  or `?token=` query parameter (needed for downloads/WebSockets).
  Default bind changed from `0.0.0.0` to `127.0.0.1`; LAN exposure is now an
  explicit opt-in (`CONNECT_ALLOW_LAN=true`) that requires a configured token.

### SEC-02 — Docker-unavailable fallback spawns a shell on the host [Critical]
- **File:** `backend/pty_bridge.py:111-131` (`_bridge_local_fallback`).
- When Docker is down, `/ws/terminal` spawns `cmd.exe` / `/bin/bash` **on the host**
  with no sandbox and (per SEC-01) no auth. Combined with the default `0.0.0.0`
  bind this is remote code execution on the host machine itself — worse than the
  container shell it replaces, because the container was the isolation boundary.
- **Fix:** Fallback disabled by default. Enabled only with explicit
  `CONNECT_ALLOW_LOCAL_FALLBACK=true` (documented as a developer-only,
  loopback-recommended option). When disabled, the terminal shows an explanatory
  notice instead of a host shell. Feature retained, but opt-in — not removed.

### SEC-03 — XSS via file names injected into `innerHTML` [High]
- **Files:** `frontend/js/filetree.js:137-150` (`item.name`), `frontend/js/editor.js:190-197`
  (`tab.name`).
- File/folder names are rendered into `innerHTML` without escaping. Because names can
  be created through the (previously unauthenticated) API, a crafted name such as
  `<img src=x onerror=...>` executes script in the IDE the next time the tree renders.
- **Fix:** Central `escapeHtml()` helper; all dynamic names rendered through it.

### SEC-04 — CORS `allow_origins=["*"]` combined with `allow_credentials=True` [High]
- **File:** `backend/main.py:50-56`.
- Permissive cross-origin policy. With credentials allowed this defeats browser
  same-origin protections for any future cookie/session mechanism and signals
  zero origin discipline.
- **Fix:** CORS middleware removed entirely. The frontend is same-origin (served by
  the daemon) or bundled inside the desktop app; cross-origin browser requests are
  rejected by an Origin allowlist check (same-host origins + Tauri webview origins),
  which also blocks DNS-rebinding/CSRF-style attacks against the localhost listener.

### SEC-05 — Docker `info()` GPU-runtime check is dead logic (else-branch also True) [High]
- **File:** `backend/docker_manager.py:44-52`.
  ```python
  if "nvidia" in runtimes: gpu_passthrough_available = True
  else: gpu_passthrough_available = True   # ← bug
  ```
- The capability check always succeeds. On hosts without the NVIDIA Container
  Toolkit, container creation with `DeviceRequest` either fails (opaque error) or
  worse, silently runs without GPU in some runtimes — the user finds out only when
  `torch.cuda.is_available()` returns False.
- **Fix:** Correct detection (nvidia runtime **or** CDI device presence), plus
  self-healing creation: try GPU-enabled create first; if the daemon rejects it,
  log a loud warning, recreate without GPU, and surface `gpu_passthrough: false`
  in `/api/container/status` and the UI.

### SEC-06 — Path traversal defenses are good but unvalidated input remains elsewhere [Medium]
- **Files:** `backend/file_manager.py:39-61` (traversal OK), `130-146` (save),
  `109-127` (content read).
- `_resolve_safe_path` correctly blocks traversal/absolute paths (verified by tests),
  but: file *content* is read fully into memory regardless of size (a multi-GB wav
  via `/api/files/content` exhausts RAM), and writes are non-atomic.
- **Fix:** Size cap on `/content` (HTTP 413 above limit), atomic writes
  (temp file + `os.replace`), generic 500 messages (no internal path/exception leak),
  OpenAPI docs endpoints disabled in production.

### SEC-07 — WebSocket terminal input protocol sniffing can swallow commands [Medium]
- **File:** `backend/pty_bridge.py:70-79`.
- Control-frame detection is `text.startswith("{") and "resize" in text`. A user
  typing any matching string at the shell prompt has their keystrokes silently
  dropped — and the heuristic invites future parser confusion (injection-adjacent).
- **Fix:** Explicit framing: text frames = raw PTY input; **binary frames = JSON
  control messages**. No content sniffing.

### SEC-08 — Uvicorn access logs would leak the auth token [Medium]
- **Files:** planned auth design + `backend/main.py:104-106`, `scripts/tray_app.py`.
- WebSocket/download URLs carry `?token=...`; default uvicorn access logging prints
  full query strings into logs.
- **Fix:** `access_log=False` for the daemon in all launch paths (app logs remain,
  without query strings).

### SEC-09 — Sensitive/operational data on disk without hygiene [Low]
- **Files:** `backend/config.py` (creates dirs at import), token design (new).
- **Fix:** Token file written with restrictive permissions; `.gconnect/` runtime dir
  git-ignored; `.env.example` provided; no secrets in code.

---

## B. Functional Bugs

### BUG-01 — Stale editor content + autosave = silent data loss [High]
- **File:** `frontend/js/editor.js:78-103` (`openFile`).
- Re-selecting an already-open file never refreshes from disk. If the file changed
  externally (terminal run, another client), the editor shows stale content and the
  1.5 s debounced autosave **overwrites the newer on-disk version**.
- **Fix:** (a) Re-fetch and refresh model content when a non-dirty tab is re-selected.
  (b) Optimistic-concurrency on save: backend returns file `mtime`; saves send
  `expected_mtime`; mismatch → HTTP 409; frontend asks the user before overwriting.

### BUG-02 — WebSocket / PTY session leak on reconnect & container restart [High]
- **File:** `frontend/js/terminal.js:65-97` (`connect()`), `frontend/js/app.js:146-148`.
- `connect()` never closes the previous WebSocket. Reconnect timer + the restart
  flow's `setTimeout(..., 1500) connect()` stack connections; each stale connection
  holds a container exec session alive server-side.
- **Fix:** Idempotent `connect()` (close/teardown existing socket first), single
  reconnect timer, guard against concurrent connects.

### BUG-03 — Container is never reconciled with changed configuration [High]
- **File:** `backend/docker_manager.py:57-128`.
- Once `connect_dev_sandbox` exists it is reused forever. Changing
  `CONNECT_EPHEMERAL_MODE`, `CONTAINER_IMAGE`, `SHM_SIZE`, or ports has **no effect**
  until the user manually `docker rm`s the container. `restart_container()` also
  does not re-apply config.
- **Fix:** Config fingerprint stored as a container label; on `get_or_create()`,
  a mismatch triggers automatic recreate (workspace volume is preserved — data-safe).

### BUG-04 — `pytest` deletes the user's real 9 GB model cache [High]
- **File:** `tests/test_api.py:98-113` (`test_wipe_model_cache`).
- The test invokes the real wipe endpoint against the real `workspace/.hf_cache`.
  Running the suite on a provisioned host destroys downloaded models.
- **Fix:** Test isolates the workspace in a tmp dir (monkeypatched settings); no
  test ever touches real user data.

### BUG-05 — Telemetry blocks the event loop and multiplies NVML polling [Medium]
- **File:** `backend/gpu_telemetry.py:100-108`.
- Each WebSocket client runs its own loop; blocking NVML calls execute **on the
  asyncio event loop** (stalling all requests/WebSockets); N clients = N×1 Hz
  hardware polls.
- **Fix:** Single broadcaster task (started in app lifespan) sampling via
  `run_in_executor`, fan-out to per-client queues. Only GPU 0 was reported although
  all GPUs are passed through — now a `gpus[]` array accompanies the primary stats.

### BUG-06 — Cache wipe over-reports and swallows failures [Medium]
- **File:** `backend/docker_manager.py:184-233`.
- `freed_mb` is computed **before** deletion while deletion errors are silently
  swallowed (`except: pass`) — the API can claim success while files remain.
  `shutil.rmtree(onerror=...)` is deprecated on Python 3.12+. Wiping while the
  container is mid-download corrupts the cache.
- **Fix:** Count bytes of actually-deleted entries; return per-path errors;
  `onexc` when available (fallback `onerror`); warning emitted when container is
  running during wipe.

### BUG-07 — Directory scan: symlink cycles = infinite recursion; no limits [Medium]
- **File:** `backend/file_manager.py:64-95` (`_scan_directory`).
- No visited-set, depth cap, or entry cap; a symlink/junction cycle hangs the
  request and can overflow the stack; symlinked directories also leak the tree of
  locations outside the workspace.
- **Fix:** Visited-set on `realpath`, symlinked directories skipped, depth and
  total-entry caps.

### BUG-08 — Workspace ZIP is buffered fully in memory [Medium]
- **File:** `backend/file_manager.py:256-277`.
- README claims "ZIP streaming" but the whole archive is built in a `BytesIO` —
  a workspace with large generated audio/models can OOM the host.
- **Fix:** Zip to a temp file, stream via `FileResponse`, delete in a background task.

### BUG-09 — Non-atomic file writes [Medium]
- **File:** `backend/file_manager.py:130-146`.
- Autosave fires every 1.5 s; a crash mid-write truncates/corrupts the file.
- **Fix:** Write to `.<name>.gconnect-tmp` in the same directory, then `os.replace()`.

### BUG-10 — Blank-editor Monaco models leak [Low]
- **File:** `frontend/js/editor.js:166-173`.
- Closing the last tab creates a fresh throwaway model every time and never
  disposes the previous one.
- **Fix:** Reuse (and dispose on replace) a single blank model.

### BUG-11 — Dead `is_mock` handling; users can't tell telemetry is simulated [Low]
- **File:** `frontend/js/telemetry.js:62-67` (`stat.is_mock ? a : a` — identical branches).
- The mock RTX-4090 telemetry renders identically to real hardware.
- **Fix:** Render a visible "(Simulated)" badge when `is_mock` is true.

### BUG-12 — Rename endpoint has no UI (dead feature) [Low]
- **File:** `backend/file_manager.py:194-214` implemented; no frontend caller.
- **Fix:** Pencil action in the file tree calling `/api/files/rename`
  (with dirty-tab safety check).

### BUG-13 — Double confirmation when deleting a dirty open file [Low]
- **Files:** `frontend/js/filetree.js:289-313` + `editor.js:140-177`.
- Delete flow confirms, then `closeTab` confirms again for unsaved changes.
- **Fix:** `closeTab(path, { force: true })` used by the delete flow (delete already
  had its own confirmation; the file is going away regardless).

### BUG-14 — Frontend cannot load offline: all libs from CDNs, `lucide@latest` unpinned [High for a desktop app]
- **File:** `frontend/index.html:9-18`.
- Monaco/xterm/Lucide load from unpkg/cdnjs/jsdelivr at runtime; `lucide@latest` is
  unpinned (silent breakage on upstream release). The product's own use-case
  (remote GPU box, Tailscale-only) plus the desktop conversion both demand local assets.
- **Fix:** Vendor all libraries into `frontend/vendor/` at pinned versions; CSP
  without `https:` sources.

### BUG-15 — Tray/setup scripts ignore configured host/port [Low]
- **Files:** `scripts/tray_app.py:109-118` (hardcoded `--port 8000`),
  `scripts/setup_host.bat:57`, `scripts/setup_host.sh:48`.
- Documented `CONNECT_PORT`/`CONNECT_HOST` env overrides silently don't apply.
- **Fix:** Scripts read the same env vars (with defaults) so config is honored
  everywhere.

### BUG-16 — Tray daemon logs discarded (`DEVNULL`) [Low]
- **File:** `scripts/tray_app.py:120-127`.
- Failures of the hidden daemon are undebuggable.
- **Fix:** Redirect to a rotating log file under `<repo>/.gconnect/logs/`.

### BUG-17 — Windows fallback shell is not a PTY [Low]
- **File:** `backend/pty_bridge.py:111-179`.
- Pipes to `cmd.exe` give no VT processing/echo parity; xterm.js experience is
  broken. Now moot for most users because fallback is disabled by default (SEC-02);
  the notice message explains this.

### BUG-18 — `Run Script` executes any file type with `python` [Low]
- **File:** `frontend/js/app.js:118-129`.
- **Fix:** Guard: only `.py` (and plain) files run; others show a message.

---

## C. Dependency Problems (pip-audit, resolved floor versions)

| Package | Version audited | Finding | Fix version | Action |
|---|---|---|---|---|
| Pillow | 11.0.0 | 60+ CVEs (CVE-2026-96636 … 112406) | 13.0.0 | Bump to `Pillow>=13.0.0` (tray icon only) |
| python-multipart | 0.0.20 | 6 advisories (PYSEC-2026-1852/3036-3040) | 0.0.31 | **Removed** — no multipart endpoint exists in the app |
| pytest | 8.3.4 | PYSEC-2026-1845 | 9.0.3 | Bump (dev dependency) |
| fastapi / uvicorn / docker / pydantic / others | current floors | none found | — | Floors kept |

npm side had no lockfile at audit time (no package.json — CDN-only frontend);
Phase 3 introduces `package.json` and `npm audit` is re-run there.

---

## D. Code Quality

1. **Side effects at import** — `backend/config.py:34-36` creates directories on
   import. Kept (harmless, needed by every entrypoint) but now also creates the
   `.gconnect` runtime dir. *(Noted, low.)*
2. **Generic `except Exception: pass`** in multiple places (wipe, resize, clipboard)
   — replaced with logged handling where user-visible.
3. **`DELETE` with JSON body** — unconventional HTTP; kept for API compatibility
   (FastAPI supports it) but noted; frontend is its only client.
4. **`user-select: none`** on body + JetBrains Mono referenced without loading the
   webfont — cosmetic; font stack falls back to Consolas (kept, documented).
5. **`docs/` evidence report and README** rebrand required for the desktop product
   (README rewritten in Phase 3/4).

---

## E. Issues deliberately NOT "fixed" (assumptions stated)

- **No user accounts/multi-tenancy** — this is a single-user personal tool; a shared
  secret + loopback default is the right size. Multi-user auth would be scope creep.
- **HTTP only (no TLS)** — loopback + Tauri webview don't need it; remote/LAN mode
  is expected to run inside a private overlay network (Tailscale/WireGuard), which
  the README now states explicitly. Adding self-signed TLS would break the
  zero-config promise without adding real safety inside an encrypted overlay.
- **Auto-updater** — requires signing keys the user must generate; scaffold not
  enabled by default (documented how to add).

---

## F. Phase 4 verification results (2026-10-24)

Everything below was executed against the real build:

| Check | Result |
|---|---|
| `pytest tests/` | **32 passed**, 1 skipped (symlink test needs Windows dev mode) |
| `pip-audit` (final floors) | **0 vulnerabilities** (pydantic-settings floor raised to 2.14.2 for CVE-2026-58203) |
| `npm audit` | **0 vulnerabilities** |
| Rust build | `cargo check` + release build clean; 3 compile errors found and fixed during bring-up (Tauri v2 API signatures) |
| NSIS installer | `GConnect_1.0.0_x64-setup.exe` (33.4 MB) produced successfully |
| Sidecar standalone | boots, serves bundled frontend + vendored assets, eager token, auth enforced |
| Desktop app e2e | window → IPC `backend_info` → daemon on 127.0.0.1:8000 → token file in app-data |
| Terminal PTY | real Docker container bash; commands execute; binary resize frames handled; JSON-lookalike text reaches shell (old sniffing bug regression-tested live) |
| Container | created and running with GPU passthrough (real Docker 29 + NVIDIA runtime on the test machine) |
| Auth (live) | 401 without token, 200 with, cross-origin 403, health public, docs/OpenAPI closed |
| Orphan test | force-kill of the app previously left the daemon running → **fixed with a parent-PID watchdog in the sidecar** (verified: sidecar exits ≤3s after parent death) |
| Offline check | zero CDN references in the frontend; all libraries vendored |

Known warnings (benign, reported honestly):
- Tauri warns the bundle identifier `com.gconnect.app` ends with `.app`
  (macOS convention conflict). Cosmetic on Windows; rename to e.g.
  `com.gconnect.desktop` before shipping a macOS build.
- Tauri warns `__TAURI_BUNDLE_TYPE variable not found` — a CLI/crate version
  skew in the bundle-type patch step; affects only the (unused) updater plugin.
- The machine's C: drive was at 100% during bundling; NSIS staging was run with
  `TMP`/`TEMP` redirected to another drive. If you hit `os error 112`, free
  space or set `TMP`/`TEMP` before `npm run build`.
