class AppManager {
  constructor() {
    this.btnRunScript = document.getElementById("btnRunScript");
    this.btnRestartSandbox = document.getElementById("btnRestartSandbox");
    this.btnConnection = document.getElementById("btnConnection");
    this.splitDivider = document.getElementById("splitDivider");
    this.editorPane = document.getElementById("editorPane");
    this.terminalPane = document.getElementById("terminalPane");
    this.btnClearTerminal = document.getElementById("btnClearTerminal");
    this.btnWipeCache = document.getElementById("btnWipeCache");

    // Connection screen (desktop app)
    this.connectionOverlay = document.getElementById("connectionOverlay");
    this.connectionChoices = document.getElementById("connectionChoices");
    this.connectionForm = document.getElementById("connectionForm");
    this.btnConnectLocal = document.getElementById("btnConnectLocal");
    this.btnConnectRemote = document.getElementById("btnConnectRemote");
    this.connectUrlInput = document.getElementById("connectUrlInput");
    this.connectTokenInput = document.getElementById("connectTokenInput");
    this.btnConnectionBack = document.getElementById("btnConnectionBack");
    this.btnDoConnect = document.getElementById("btnDoConnect");
    this.btnCancelConnection = document.getElementById("btnCancelConnection");
    this.connectionError = document.getElementById("connectionError");

    // Docker / sandbox availability banner + run warning popup
    this.dockerBanner = document.getElementById("dockerBanner");
    this.dockerBannerText = document.getElementById("dockerBannerText");
    this.btnBannerRetry = document.getElementById("btnBannerRetry");
    this.btnBannerDismiss = document.getElementById("btnBannerDismiss");
    this.dockerWarnOverlay = document.getElementById("dockerWarnOverlay");
    this.dockerWarnText = document.getElementById("dockerWarnText");
    this.btnOkDockerWarn = document.getElementById("btnOkDockerWarn");
    this.containerStatus = null;
    this.bannerDismissed = false;

    // Bottom panel tabs (Terminal / Problems) + sidebar resizer
    this.tabTerminal = document.getElementById("tabTerminal");
    this.tabProblems = document.getElementById("tabProblems");
    this.problemsContainer = document.getElementById("problemsContainer");
    this.xtermContainer = document.getElementById("xtermContainer");
    this.sidebarResizer = document.getElementById("sidebarResizer");
    this.activePanel = "terminal";
  }

  async init() {
    if (window.lucide) {
      window.lucide.createIcons();
    }

    await window.authManager.ready();

    this.bindEvents();

    if (window.authManager.needsConnection()) {
      // No connection configured (or the shell failed): show the picker and
      // keep the managers idle. Choosing a connection reloads the page.
      this.showConnectionScreen(false);
      return;
    }

    if (!window.authManager.isDesktop && this.btnConnection) {
      this.btnConnection.style.display = "none";
    }

    window.telemetryManager.init();
    window.terminalManager.init();
    window.fileTreeManager.init();
    if (window.clusterManager) {
      window.clusterManager.init();
    }

    window.editorManager.init(() => {
      this.ensureSampleScript();
    });

    this.startContainerWatch();
    this.setupSplitDivider();
    this.setupSidebarResizer();
  }

  bindEvents() {
    if (this.btnRunScript) {
      this.btnRunScript.addEventListener("click", () => this.runActiveScript());
    }

    if (this.btnRestartSandbox) {
      this.btnRestartSandbox.addEventListener("click", () => this.restartSandbox());
    }

    if (this.btnClearTerminal) {
      this.btnClearTerminal.addEventListener("click", () => {
        // Clear whichever bottom-panel tab is active.
        if (this.activePanel === "problems" && window.problemsManager) {
          window.problemsManager.clear();
        } else {
          window.terminalManager.clear();
        }
      });
    }

    if (this.tabTerminal && this.tabProblems) {
      this.tabTerminal.addEventListener("click", () => this.switchPanel("terminal"));
      this.tabProblems.addEventListener("click", () => this.switchPanel("problems"));
    }

    if (this.btnWipeCache) {
      this.btnWipeCache.addEventListener("click", () => this.wipeModelCache());
    }

    if (this.btnConnection) {
      this.btnConnection.addEventListener("click", () => this.showConnectionScreen(true));
    }

    if (this.btnConnectLocal) {
      this.btnConnectLocal.addEventListener("click", () => this.applyConnection("local"));
    }

    if (this.btnConnectRemote) {
      this.btnConnectRemote.addEventListener("click", () => {
        if (this.connectionChoices) this.connectionChoices.classList.add("hidden");
        if (this.connectionForm) this.connectionForm.classList.remove("hidden");
        this._setConnectionError("");
        if (this.connectUrlInput) this.connectUrlInput.focus();
      });
    }

    if (this.btnConnectionBack) {
      this.btnConnectionBack.addEventListener("click", () => {
        if (this.connectionForm) this.connectionForm.classList.add("hidden");
        if (this.connectionChoices) this.connectionChoices.classList.remove("hidden");
        this._setConnectionError("");
      });
    }

    if (this.btnCancelConnection) {
      this.btnCancelConnection.addEventListener("click", () => this.hideConnectionScreen());
    }

    if (this.btnDoConnect) {
      this.btnDoConnect.addEventListener("click", () => this.confirmRemoteConnection());
    }

    if (this.btnBannerRetry) {
      this.btnBannerRetry.addEventListener("click", async () => {
        this.bannerDismissed = false;
        await this.refreshContainerStatus();
        window.terminalManager.connect();
      });
    }

    if (this.btnBannerDismiss) {
      this.btnBannerDismiss.addEventListener("click", () => {
        this.bannerDismissed = true;
        this.updateBanner();
      });
    }

    if (this.btnOkDockerWarn) {
      this.btnOkDockerWarn.addEventListener("click", () => {
        if (this.dockerWarnOverlay) this.dockerWarnOverlay.classList.add("hidden");
      });
    }

    [this.connectUrlInput, this.connectTokenInput].forEach((el) => {
      if (el) {
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter") this.confirmRemoteConnection();
        });
      }
    });

    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        this.runActiveScript();
      } else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        window.editorManager.saveCurrentFile();
      }
    });
  }

  async ensureSampleScript() {
    try {
      const res = await window.authManager.api("/api/files");
      const data = await res.json();
      if (!data.files || data.files.length === 0) {
        const defaultScript = `# GConnect - Remote GPU Development Platform
# Specifically configured for PyTorch, CUDA, and Voice Agent workflows

import os
import torch

print("========================================")
print(" GConnect GPU Sandbox Ready")
print("========================================")
print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available:  {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"Device Name:     {torch.cuda.get_device_name(0)}")
    print(f"Device Count:    {torch.cuda.device_count()}")
    x = torch.randn(1000, 1000, device="cuda")
    print("CUDA Matrix Allocation: SUCCESS")

print(f"Hugging Face Cache: {os.environ.get('HF_HOME')}")
print("========================================")
`;
        await window.authManager.api("/api/files/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: "hello_gpu.py",
            content: defaultScript,
          }),
        });

        await window.fileTreeManager.loadTree();
        window.fileTreeManager.selectFile("hello_gpu.py");
      } else {
        const firstFile = this._findFirstFile(data.files);
        if (firstFile) {
          window.fileTreeManager.selectFile(firstFile.path);
        }
      }
    } catch (err) {
      console.warn("Could not check/create sample script:", err);
    }
  }

  _findFirstFile(items) {
    for (const item of items) {
      if (item.type === "file") return item;
      if (item.type === "directory" && item.children) {
        const nested = this._findFirstFile(item.children);
        if (nested) return nested;
      }
    }
    return null;
  }

  async runActiveScript() {
    const activePath = window.editorManager.activeFilePath;
    if (!activePath) {
      alert("Please select or create a .py file to run.");
      return;
    }

    if (!activePath.toLowerCase().endsWith(".py")) {
      alert(`Only Python (.py) files can be run. '${activePath}' is not a Python script.`);
      return;
    }

    // Never let a missing sandbox swallow the run silently.
    await this.refreshContainerStatus();
    if (!this.sandboxOk()) {
      this.showDockerWarn();
      return;
    }

    await window.editorManager.saveCurrentFile();
    console.log(`Executing ${activePath} in container sandbox...`);
    window.terminalManager.sendCommand(`python "${activePath}"`);
    window.terminalManager.focus();
  }

  async restartSandbox() {
    if (!confirm("Restart Docker sandbox container? Active terminal sessions will be reset.")) {
      return;
    }

    const btn = this.btnRestartSandbox;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Restarting...</span>`;
      if (window.lucide) window.lucide.createIcons();
    }

    try {
      const res = await window.authManager.api("/api/container/restart", { method: "POST" });
      const data = await res.json();
      if (data.status === "success") {
        // connect() is idempotent: it closes the old socket/session first.
        setTimeout(() => {
          window.terminalManager.connect();
          alert("Docker sandbox restarted successfully.");
        }, 1500);
      } else {
        alert("Failed to restart Docker sandbox.");
      }
    } catch (err) {
      console.error("Restart container error:", err);
      alert("Error contacting host daemon to restart sandbox.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="refresh-cw"></i><span>Restart Sandbox</span>`;
        if (window.lucide) window.lucide.createIcons();
      }
    }
  }

  async wipeModelCache() {
    if (!confirm("This will delete downloaded models from the host disk. Proceed?")) {
      return;
    }

    const btn = this.btnWipeCache;
    const originalHtml = btn ? btn.innerHTML : "";
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Cleaning...</span>`;
      if (window.lucide) window.lucide.createIcons();
    }

    try {
      const res = await window.authManager.api("/api/cache/wipe", { method: "POST" });
      const data = await res.json();
      if (res.ok && (data.status === "success" || data.status === "partial")) {
        const freedFormatted = data.freed_mb >= 1024
          ? `${(data.freed_mb / 1024).toFixed(2)} GB`
          : `${data.freed_mb} MB`;
        if (data.status === "partial") {
          alert(`Partially cleaned: freed ${freedFormatted}, but some entries could not be deleted.\n${data.message || ""}`);
        } else {
          alert(`Successfully freed ${freedFormatted} from host machine.`);
        }
      } else {
        alert(`Failed to clean models: ${data.message || data.detail || "Unknown error"}`);
      }
    } catch (err) {
      console.error("Wipe cache error:", err);
      alert("Error contacting host daemon to wipe model cache.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = originalHtml;
        if (window.lucide) window.lucide.createIcons();
      }
    }
  }

  // ------------------------------------------------------------------
  // Connection screen (desktop app: local sidecar vs remote GPU PC)
  // ------------------------------------------------------------------

  showConnectionScreen(cancellable) {
    if (!this.connectionOverlay) return;
    if (this.connectionChoices) this.connectionChoices.classList.remove("hidden");
    if (this.connectionForm) this.connectionForm.classList.add("hidden");
    this._setConnectionError("");
    if (this.btnCancelConnection) {
      this.btnCancelConnection.classList.toggle("hidden", !cancellable);
    }
    this.connectionOverlay.classList.remove("hidden");
    if (window.lucide) window.lucide.createIcons();
  }

  hideConnectionScreen() {
    if (this.connectionOverlay) {
      this.connectionOverlay.classList.add("hidden");
    }
  }

  _setConnectionError(message) {
    if (!this.connectionError) return;
    this.connectionError.textContent = message;
    this.connectionError.classList.toggle("hidden", !message);
  }

  confirmRemoteConnection() {
    const url = this.connectUrlInput ? this.connectUrlInput.value.trim() : "";
    const token = this.connectTokenInput ? this.connectTokenInput.value.trim() : "";
    if (!url || !token) {
      this._setConnectionError("Both the daemon URL and the token are required.");
      return;
    }
    this.applyConnection("remote", url, token);
  }

  async applyConnection(mode, url, token) {
    const btn = mode === "local" ? this.btnConnectLocal : this.btnDoConnect;
    if (btn) btn.disabled = true;
    this._setConnectionError("");

    try {
      const info = await window.authManager.setConnection(mode, url, token);
      if (mode === "remote" && info && info.online === false) {
        const proceed = confirm(
          `Could not reach ${url} right now.\n\n` +
          "Check the daemon is running with CONNECT_ALLOW_LAN=true and the URL is correct.\n\n" +
          "Connect anyway?"
        );
        if (!proceed) {
          if (btn) btn.disabled = false;
          return;
        }
      }
      // Cleanest switch: every manager re-initializes on the fresh page.
      window.location.reload();
    } catch (err) {
      console.error("Set connection error:", err);
      this._setConnectionError(String(err));
      if (btn) btn.disabled = false;
    }
  }

  // ------------------------------------------------------------------
  // Docker / sandbox availability
  // ------------------------------------------------------------------

  startContainerWatch() {
    this.refreshContainerStatus();
    setInterval(() => this.refreshContainerStatus(), 10000);
  }

  async refreshContainerStatus() {
    try {
      const res = await window.authManager.api("/api/container/status");
      if (res.ok) {
        const prev = this.containerStatus;
        this.containerStatus = await res.json();
        // A dismiss only lasts until the sandbox goes down AGAIN.
        if (prev && this.sandboxOk(prev) && !this.sandboxOk()) {
          this.bannerDismissed = false;
        }
      }
    } catch (err) {
      /* daemon unreachable: banner state stays as-is */
    }
    this.updateBanner();
  }

  sandboxOk(status = this.containerStatus) {
    return !!(status && status.docker_available && status.container_status === "running");
  }

  updateBanner() {
    if (!this.dockerBanner) return;
    const ok = this.sandboxOk();
    const show = !ok && !this.bannerDismissed;
    this.dockerBanner.classList.toggle("hidden", !show);
    if (!show) return;

    const where = window.authManager.mode === "remote" ? "the remote GPU PC" : "this machine";
    const reason = !this.containerStatus || !this.containerStatus.docker_available
      ? `Docker is not running (or not installed) on ${where}.`
      : "The sandbox container is not running - try Restart Sandbox.";
    if (this.dockerBannerText) {
      this.dockerBannerText.textContent =
        `${reason} Scripts cannot run until it is available.`;
    }
    if (window.lucide) window.lucide.createIcons();
  }

  showDockerWarn() {
    if (!this.dockerWarnOverlay) return;
    const where = window.authManager.mode === "remote" ? "the remote GPU PC" : "this machine";
    const reason = !this.containerStatus || !this.containerStatus.docker_available
      ? `Docker is not running (or not installed) on ${where}.`
      : `The sandbox container on ${where} is not running.`;
    if (this.dockerWarnText) {
      this.dockerWarnText.textContent =
        `${reason} Your file is safe - it just can't be executed yet.`;
    }
    this.bannerDismissed = false;
    this.updateBanner();
    this.dockerWarnOverlay.classList.remove("hidden");
    if (window.lucide) window.lucide.createIcons();
  }

  // ------------------------------------------------------------------
  // Bottom panel tabs + sidebar resizer (VS Code-style)
  // ------------------------------------------------------------------

  switchPanel(which) {
    if (which !== "terminal" && which !== "problems") return;
    this.activePanel = which;
    const terminalActive = which === "terminal";
    this.tabTerminal.classList.toggle("active", terminalActive);
    this.tabProblems.classList.toggle("active", !terminalActive);
    this.xtermContainer.classList.toggle("hidden", !terminalActive);
    this.problemsContainer.classList.toggle("hidden", terminalActive);
    if (terminalActive) {
      // xterm measured itself inside a display:none box - refit on return.
      window.terminalManager.fit();
    }
  }

  setupSidebarResizer() {
    const sidebar = document.getElementById("sidebar");
    if (!this.sidebarResizer || !sidebar) return;

    const clamp = (px) => Math.min(520, Math.max(150, px));
    const relayout = () => {
      window.editorManager.resize();
      window.terminalManager.fit();
    };

    let dragging = false;
    this.sidebarResizer.addEventListener("mousedown", (e) => {
      dragging = true;
      this.sidebarResizer.classList.add("dragging");
      document.body.style.cursor = "col-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      sidebar.style.width = `${clamp(e.clientX)}px`;
      relayout();
    });

    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      this.sidebarResizer.classList.remove("dragging");
      document.body.style.cursor = "default";
      relayout();
    });

    // VS Code affordance: double-click resets to the default width.
    this.sidebarResizer.addEventListener("dblclick", () => {
      sidebar.style.width = "250px";
      relayout();
    });
  }

  setupSplitDivider() {
    let isDragging = false;

    this.splitDivider.addEventListener("mousedown", (e) => {
      isDragging = true;
      document.body.style.cursor = "row-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;

      const viewport = document.querySelector(".workspace-viewport");
      const rect = viewport.getBoundingClientRect();
      const offsetY = e.clientY - rect.top;

      const minHeight = 100;
      const maxHeight = rect.height - minHeight;

      if (offsetY >= minHeight && offsetY <= maxHeight) {
        const topPercent = (offsetY / rect.height) * 100;
        const bottomPercent = 100 - topPercent;

        this.editorPane.style.flex = `${topPercent}`;
        this.terminalPane.style.flex = `${bottomPercent}`;

        window.editorManager.resize();
        window.terminalManager.fit();
      }
    });

    window.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        document.body.style.cursor = "default";
        window.editorManager.resize();
        window.terminalManager.fit();
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.appManager = new AppManager();
  window.appManager.init();
});
