/**
 * AuthManager: supplies the daemon base URL + bearer token everywhere.
 *
 * Desktop (Tauri): the Rust side spawns the daemon sidecar and exposes the
 * auto-generated token through the `backend_info` IPC command. The token lives
 * in memory only - never in localStorage.
 *
 * Browser (remote GPU host): same-origin static frontend; the user pastes the
 * daemon token once; it is kept in sessionStorage (cleared when the tab ends).
 */
class AuthManager {
  constructor() {
    this.token = null;
    this.base = null;           // e.g. "http://127.0.0.1:8000" ("" = same origin)
    this.isDesktop = false;
    this._readyPromise = null;
  }

  ready() {
    if (!this._readyPromise) {
      this._readyPromise = this._init();
    }
    return this._readyPromise;
  }

  async _init() {
    // Tauri v2 injects __TAURI_INTERNALS__ into the webview.
    if (window.__TAURI_INTERNALS__ && window.__TAURI__) {
      this.isDesktop = true;
      try {
        const info = await window.__TAURI__.core.invoke("backend_info");
        this.token = info.token;
        this.base = `http://127.0.0.1:${info.port}`;
        return;
      } catch (err) {
        console.error("Failed to obtain backend info from the desktop shell:", err);
      }
    }

    // Browser mode: token may already be known (sessionStorage).
    const saved = sessionStorage.getItem("gconnect_token");
    if (saved) {
      this.token = saved;
    }
    this.base = ""; // same origin
  }

  /** Ask the user for the token (browser mode). Returns true when set. */
  promptForToken() {
    return new Promise((resolve) => {
      const overlay = document.getElementById("authOverlay");
      const input = document.getElementById("authTokenInput");
      const btn = document.getElementById("btnConfirmAuth");
      if (!overlay || !input || !btn) {
        resolve(false);
        return;
      }

      const submit = () => {
        const value = input.value.trim();
        if (!value) return;
        this.token = value;
        sessionStorage.setItem("gconnect_token", value);
        overlay.classList.add("hidden");
        btn.removeEventListener("click", submit);
        resolve(true);
      };

      btn.addEventListener("click", submit, { once: true });
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") submit();
      });
      overlay.classList.remove("hidden");
      input.focus();
    });
  }

  clearToken() {
    this.token = null;
    sessionStorage.removeItem("gconnect_token");
  }

  /** fetch() wrapper: absolute URL + Authorization header; on 401 in browser
   * mode, prompts for the token once and retries. */
  async api(path, options = {}) {
    await this.ready();
    const doFetch = () =>
      fetch(this.base + path, {
        ...options,
        headers: {
          ...(options.headers || {}),
          ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        },
      });

    let res = await doFetch();
    if (res.status === 401 && !this.isDesktop) {
      const ok = await this.promptForToken();
      if (ok) res = await doFetch();
    }
    return res;
  }

  /** URL with token as query param - for downloads and media elements where
   * headers cannot be set (token is high-entropy; transport is loopback or an
   * encrypted overlay like Tailscale). */
  urlWithToken(path) {
    const sep = path.includes("?") ? "&" : "?";
    return this.base + path + (this.token ? `${sep}token=${encodeURIComponent(this.token)}` : "");
  }

  /** WebSocket URL with token query param. */
  wsUrl(path) {
    const base = this.base || `${location.protocol}//${location.host}`;
    const proto = base.startsWith("https") ? "wss:" : "ws:";
    const hostPart = base.replace(/^https?:/, proto);
    const sep = path.includes("?") ? "&" : "?";
    return hostPart + path + (this.token ? `${sep}token=${encodeURIComponent(this.token)}` : "");
  }
}

window.authManager = new AuthManager();

/** Escapes text for safe interpolation into innerHTML (filenames, paths). */
function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
