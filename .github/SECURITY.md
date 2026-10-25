# Security Policy

## Supported versions

GConnect is a single-branch project at this stage: the latest commit on the
default branch (`v1.0`) is the only supported version. Please make sure you
are running a freshly built binary (or the bundled installer from the repo
root) before reporting an issue.

## How to report a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's **private vulnerability reporting**:

1. Go to the **Security** tab of this repository
2. Click **Report a vulnerability**
3. Describe the issue, how to reproduce it, and the impact you believe it has

You will get an acknowledgment within **72 hours** and a triage decision
(accepted / declined / needs more info) within **7 days**. Accepted issues are
fixed on the default branch and disclosed in the release notes — with credit
if you want it.

## Scope

**In scope** (things this project itself is responsible for):

- `backend/auth.py` — token auth, origin checks, the Tauri CORS grant
- `backend/file_manager.py` — path traversal, atomic writes, size caps
- `backend/pty_bridge.py` — the terminal WebSocket and its control channel
- `backend/cluster_manager.py` — remote node registry and token storage
- `src-tauri/src/main.rs` — the desktop shell: sidecar lifecycle, IPC
  commands, connection config handling
- Anything that lets an unauthenticated or cross-origin caller reach the API

**Out of scope:**

- Attacks that require an account on, or physical access to, the GPU host
- Containers escaping Docker (report those to Docker / the NVIDIA toolkit)
- Vulnerabilities in vendored third-party libraries (Monaco, xterm) — report
  upstream, but a heads-up here is appreciated
- Denial-of-service against a daemon you are authorized to use anyway

## Deployment reality check

GConnect is designed to be exposed either to **loopback only** (default) or to
a **private overlay network** (Tailscale/WireGuard) with a bearer token. The
daemon speaks plain HTTP by design — putting it directly on the hostile
internet is not a supported configuration. Please read the
[Security Model](README.md#-security-model) in the README and the
[full audit](docs/SECURITY_AUDIT.md) before deciding how much of that applies
to your setup.
