# GConnect: Personal Remote GPU Development Platform

GConnect is a lightweight remote GPU development platform for editing,
executing, and monitoring Python scripts on a dedicated NVIDIA GPU machine —
now available both as a **desktop application** (Tauri) and the original
**host-daemon** web IDE. It is optimized for heavy voice/multimodal AI
pipelines (Faster-Whisper, 4-bit Mistral-7B, Indic-Parer-TTS, Wav2Vec2)
without risking host machine instability.

> v2.0.0 is a security-hardened release: token authentication, loopback-only
> default binding, XSS/traversal fixes, dependency CVE cleanup, and the Tauri
> desktop conversion. See `docs/SECURITY_AUDIT.md` and `docs/CHANGELOG.md`.

---

## 🌟 Key Features

* **Persistent model cache** (`HF_HOME=/workspace/.hf_cache`): ~9 GB of voice
  models download once and survive container restarts (or stay ephemeral with
  `CONNECT_EPHEMERAL_MODE=true`).
* **Dual-channel networking**: port 8000 (IDE/API/terminal/telemetry), port
  8001 forwarded into the container for low-latency audio inference streams
  over Tailscale/LAN.
* **Interactive raw PTY**: Docker exec socket hijack piped to xterm.js —
  interactive prompts, `htop`, live `tqdm`, `Ctrl+C`, dynamic resize
  (binary-frame control protocol).
* **GPU passthrough + telemetry**: NVIDIA Container Toolkit DeviceRequest
  (ALL local GPUs), 8 GB shm (no DataLoader bus errors), 1 Hz NVML telemetry
  with per-GPU detail; honest "NO GPU" badge when no NVIDIA hardware exists.
* **GPU Cluster (multi-PC pooling)**: register the GConnect daemon on each of
  your PCs (Tailscale/LAN) in the Cluster panel — every real GPU appears in
  one place with pooled VRAM totals. Real distributed training is bootstrapped
  from `workspace/cluster/` (Ray head/join for cross-PC GPU pooling,
  torchrun DDP for multi-GPU on one machine).
* **Modern dark IDE**: multi-tab Monaco editor with debounced autosave and
  edit-conflict protection (mtime 409), file tree with create/rename/delete,
  audio preview modal for generated speech, one-click workspace ZIP export.
* **Security (new)**: shared-secret auth on every API/WebSocket, loopback
  default bind, cross-origin rejection, path traversal defenses, atomic
  writes, symlink-safe scans.

---

## 📦 Desktop application (Tauri v2)

The desktop app bundles the daemon as a **sidecar** binary (PyInstaller) and
talks to it on a free loopback port. The auth token is generated per run and
handed to the webview via IPC — nothing sensitive is stored in the browser.

### Prerequisites
* Node.js 18+ and npm
* Rust toolchain (MSVC on Windows) — https://rustup.rs
* Python 3.10+ (for building the sidecar only)
* Docker + NVIDIA Container Toolkit (optional; app degrades gracefully)

### Development

```bash
npm install                 # Tauri CLI
python scripts/make_icon.py # one-time: master icon (already committed)
npx tauri icon src-tauri/app-icon.png   # one-time: icon set (already committed)
python scripts/build_sidecar.py         # build daemon sidecar (PyInstaller)
npm run dev                 # = tauri dev
```

### Build installers

```bash
python scripts/build_sidecar.py
npm run build               # = tauri build
```

Windows produces an NSIS installer under `src-tauri/target/release/bundle/`.
For macOS/Linux the same commands work (`npm run build`); the sidecar script
detects the Rust target triple automatically. Bundle targets can be extended
in `src-tauri/tauri.conf.json` (`bundle.targets`: `dmg`, `appimage`, `deb`...).

### Desktop security model
* Strict CSP: only `self` + loopback HTTP/WS for the daemon; no CDN, no eval.
* Capability file grants `core:default` only — no fs/shell/http permissions
  reach the webview. The daemon sidecar is managed entirely from Rust.
* Devtools disabled in production builds (`app.windows[].devtools: false`).
* Single-instance lock; system tray with daemon restart.

---

## 🖥️ Host-daemon mode (remote GPU workstation)

Run the daemon on the GPU machine and use it from any browser.

### Windows host
```cmd
scripts\setup_host.bat
```
Silent background / tray: `scripts\start_tray.bat` (logs in `.gconnect/logs/`).

### Linux host
```bash
chmod +x scripts/setup_host.sh
./scripts/setup_host.sh
```

### Client connection
1. Open `http://127.0.0.1:8000` (local) — you will be asked for the access
   token once (stored in sessionStorage for the tab).
2. The token is in `.gconnect/auth_token` on the host, or set
   `CONNECT_AUTH_TOKEN` yourself via environment/`.env`.

### Remote access (LAN / Tailscale) — explicit opt-in
```bash
CONNECT_ALLOW_LAN=true CONNECT_AUTH_TOKEN=<long-random-secret> ./scripts/setup_host.sh
```
Then from your laptop: `http://<GPU_HOST_IP>:8000` (enter the same token).
Use an encrypted overlay (Tailscale/WireGuard) — the daemon itself is HTTP.

### Voice inference server
```bash
# Inside the GConnect terminal:
python kritix_server.py
# On your laptop:
python kritix_client.py --host <GPU_HOST_IP> --port 8001
```

### Connecting multiple PCs' GPUs (GPU Cluster)
1. On **every** PC: start the GConnect daemon (desktop app, or host mode with
   `CONNECT_ALLOW_LAN=true` + a fixed `CONNECT_AUTH_TOKEN`).
2. On your main PC: open **Cluster** in the header → add each remote
   (`http://<pc-ip>:8000` + its token).
3. The panel shows every node's real GPUs and **pooled VRAM**, refreshed live.
4. To actually train across them: `workspace/cluster/README.md`
   (Ray `--head`/`--join`, torchrun DDP, accelerate `device_map="auto"`).
   Note: VRAM never merges into one virtual CUDA device — pooling means the
   cluster scheduler shards your workload across every real GPU.

---

## ⚙️ Configuration

All settings: environment variables with the `CONNECT_` prefix or a `.env`
file — see [.env.example](.env.example) for the full annotated list.

| Variable | Default | Description |
|---|---|---|
| `CONNECT_HOST` | `127.0.0.1` | Bind host (loopback enforced unless ALLOW_LAN) |
| `CONNECT_PORT` | `8000` | Daemon port |
| `CONNECT_ALLOW_LAN` | `false` | Expose on the LAN (requires token) |
| `CONNECT_AUTH_TOKEN` | auto-generated | Shared secret (`.gconnect/auth_token` if unset) |
| `CONNECT_INFERENCE_PORT` | `8001` | Container-forwarded audio port |
| `CONNECT_CONTAINER_IMAGE` | `pytorch/pytorch:latest` | Sandbox image |
| `CONNECT_SHM_SIZE` | `8g` | Container shared memory |
| `CONNECT_WORKSPACE_DIR` | `./workspace` | Directory mounted at `/workspace` |
| `CONNECT_EPHEMERAL_MODE` | `false` | Colab-style in-container model cache |
| `CONNECT_ALLOW_LOCAL_FALLBACK` | `false` | HOST shell when Docker is down (dangerous) |

### 🧹 Disk cleanup
"Clean Models (Free Disk)" or `POST /api/cache/wipe` deletes the HF cache and
reports bytes actually freed (partial failures are surfaced).

---

## 🧪 Testing

```bash
pip install -r backend/requirements.txt
pytest tests/ -v
```

All tests run against an isolated temp workspace — they never touch your real
files or model cache.

---

## 🏗️ Architecture

```
[Desktop app (Tauri webview) / Browser]
   │  IPC backend_info {port, token}   (desktop only)
   │  HTTP + WebSocket (Bearer/?token=)
   ▼
[Host Daemon: FastAPI (loopback)]
   ├── Auth middleware + origin policy
   ├── Hardware: pynvml broadcaster (1 Hz, all GPUs)
   └── Container supervisor: Docker Engine API
         │  hijacked raw exec socket (binary control frames)
         ▼
[Sandbox: pytorch/pytorch + CUDA]
   ├── GPU: DeviceRequest(count=-1)
   ├── shm: 8g   ├── /workspace volume   ├── HF cache: /workspace/.hf_cache
   └── Voice inference stream: :8001
```

Desktop mode additionally:
```
src-tauri (Rust)
   ├── spawns binaries/gconnect-backend (PyInstaller sidecar, free port)
   ├── waits /api/health, reads runtime/auth_token
   ├── backend_info IPC → webview AuthManager
   ├── tray: open / copy URL / restart daemon / quit
   └── single-instance lock; child killed on exit
```

---

## 🔒 Security notes

* Default posture: loopback + token. The terminal is a shell in the container;
  treat the token like a password.
* Cross-origin browser requests are rejected (CSRF/rebinding defense).
* The local host-shell fallback is disabled by default and must be explicitly
  enabled — understand the implications before doing so.
* Full audit trail: `docs/SECURITY_AUDIT.md` (26 issues, all resolved in v2).
