/**
 * ShareManager: Manages the "Share My GPU" Host Dashboard.
 * Displays hardware specifications, detected network IPs (Tailscale/LAN),
 * firewall configuration, and one-click teammate connection invites.
 */

class ShareManager {
  constructor() {
    this.overlay = document.getElementById("shareOverlay");
    this.btnOpenModal = document.getElementById("btnShareGPU");
    this.btnCloseModal = document.getElementById("btnCloseShareModal");
    this.btnConnectShare = document.getElementById("btnConnectShare");

    // Specs elements
    this.gpuNameEl = document.getElementById("shareGpuName");
    this.vramEl = document.getElementById("shareVramStatus");
    this.dockerEl = document.getElementById("shareDockerStatus");
    this.networkIpEl = document.getElementById("shareNetworkIp");
    this.networkTypeEl = document.getElementById("shareNetworkType");

    // Controls
    this.firewallBanner = document.getElementById("shareFirewallBanner");
    this.btnAllowFirewall = document.getElementById("btnAllowFirewall");
    this.tokenInput = document.getElementById("shareTokenInput");
    this.btnSaveToken = document.getElementById("btnSaveCustomToken");
    this.btnToggleShare = document.getElementById("btnToggleShare");
    this.btnToggleShareText = document.getElementById("btnToggleShareText");

    // Invite card
    this.inviteCard = document.getElementById("shareInviteCard");
    this.inviteUrlEl = document.getElementById("inviteUrlDisplay");
    this.invitePortEl = document.getElementById("invitePortDisplay");
    this.inviteTokenEl = document.getElementById("inviteTokenDisplay");
    this.btnCopyUrl = document.getElementById("btnCopyUrl");
    this.btnCopyToken = document.getElementById("btnCopyToken");
    this.btnCopyFull = document.getElementById("btnCopyFullInvite");

    // Client auto-paste input
    this.pasteInviteInput = document.getElementById("pasteInviteInput");

    this.shareData = null;
    this.pollInterval = null;
  }

  init() {
    if (this.btnOpenModal) {
      this.btnOpenModal.addEventListener("click", () => this.open());
    }
    if (this.btnCloseModal) {
      this.btnCloseModal.addEventListener("click", () => this.close());
    }
    if (this.btnConnectShare) {
      this.btnConnectShare.addEventListener("click", () => {
        // Trigger share mode in connection modal
        this.startSharingMode();
      });
    }

    if (this.btnAllowFirewall) {
      this.btnAllowFirewall.addEventListener("click", () => this.allowFirewall());
    }

    if (this.btnSaveToken) {
      this.btnSaveToken.addEventListener("click", () => this.saveCustomToken());
    }

    if (this.btnToggleShare) {
      this.btnToggleShare.addEventListener("click", () => this.toggleSharing());
    }

    if (this.btnCopyUrl) {
      this.btnCopyUrl.addEventListener("click", () => this.copyText(this.inviteUrlEl?.textContent));
    }

    if (this.btnCopyToken) {
      this.btnCopyToken.addEventListener("click", () => this.copyText(this.inviteTokenEl?.textContent));
    }

    if (this.btnCopyFull) {
      this.btnCopyFull.addEventListener("click", () => this.copyFullInvite());
    }

    if (this.pasteInviteInput) {
      this.pasteInviteInput.addEventListener("input", (e) => this.handleSmartPaste(e.target.value));
      this.pasteInviteInput.addEventListener("paste", (e) => {
        setTimeout(() => this.handleSmartPaste(this.pasteInviteInput.value), 50);
      });
    }

    // Check if initial mode is share
    if (window.authManager && window.authManager.mode === "share") {
      if (this.btnOpenModal) {
        this.btnOpenModal.classList.add("btn-active-share");
      }
    }
  }

  async open() {
    if (!this.overlay) return;
    this.overlay.classList.remove("hidden");
    await this.refresh();
    this.startPolling();
  }

  close() {
    if (this.overlay) {
      this.overlay.classList.add("hidden");
    }
    this.stopPolling();
  }

  startPolling() {
    this.stopPolling();
    this.pollInterval = setInterval(() => this.refresh(), 3000);
  }

  stopPolling() {
    if (this.pollInterval) {
      clearInterval(this.pollInterval);
      this.pollInterval = null;
    }
  }

  async refresh() {
    try {
      const res = await window.authManager.api("/api/host/share-info");
      if (!res.ok) return;
      this.shareData = await res.json();

      try {
        const fwRes = await window.authManager.api("/api/host/firewall-status");
        if (fwRes.ok) {
          const fw = await fwRes.json();
          this.shareData.firewall_allowed = !!fw.allowed;
        }
      } catch (_) {}

      this.render();
    } catch (err) {
      console.warn("[ShareManager] Failed to load share info:", err);
    }
  }

  render() {
    if (!this.shareData) return;
    const d = this.shareData;

    // 0. Firewall Banner Status
    if (this.firewallBanner && d.firewall_allowed) {
      this.firewallBanner.style.background = "rgba(46, 160, 67, 0.15)";
      this.firewallBanner.style.borderColor = "var(--color-success, #2ea043)";
      this.firewallBanner.innerHTML = `
        <i data-lucide="shield-check" style="color: var(--color-success, #2ea043); font-size: 1.25rem;"></i>
        <div class="firewall-text">
          <strong style="color: var(--color-success, #2ea043);">Windows Firewall:</strong> Inbound ports 8000 & 8001 are allowed.
        </div>
      `;
      if (window.lucide) window.lucide.createIcons();
    }

    // 1. GPU / CPU Specs
    if (d.telemetry && d.telemetry.gpus && d.telemetry.gpus.length > 0) {
      const gpu = d.telemetry.gpus[0];
      const usedGb = (gpu.memory_used_mb / 1024).toFixed(1);
      const totalGb = (gpu.memory_total_mb / 1024).toFixed(1);
      if (this.gpuNameEl) this.gpuNameEl.textContent = gpu.name || "NVIDIA GPU";
      if (this.vramEl) this.vramEl.textContent = `VRAM: ${usedGb} GB / ${totalGb} GB (${gpu.utilization_gpu_pct || 0}% load)`;
    } else if (d.telemetry && d.telemetry.host && d.telemetry.host.name) {
      const host = d.telemetry.host;
      if (this.gpuNameEl) this.gpuNameEl.textContent = "No NVIDIA GPU (CPU Host)";
      const ramUsed = (host.ram_used_mb / 1024).toFixed(1);
      const ramTotal = (host.ram_total_mb / 1024).toFixed(1);
      if (this.vramEl) this.vramEl.textContent = `RAM: ${ramUsed} / ${ramTotal} GB · ${host.name}`;
    } else {
      if (this.gpuNameEl) this.gpuNameEl.textContent = "No NVIDIA GPU detected";
      if (this.vramEl) this.vramEl.textContent = "VRAM: None (CPU Only)";
    }

    // 2. Docker Status
    if (this.dockerEl && d.docker_status) {
      const status = d.docker_status.container_status;
      if (status === "running") {
        this.dockerEl.textContent = "● Container Ready";
        this.dockerEl.className = "spec-value text-success";
      } else if (d.docker_status.docker_available) {
        this.dockerEl.textContent = "● Docker Ready (Standby)";
        this.dockerEl.className = "spec-value text-info";
      } else {
        this.dockerEl.textContent = "Docker Desktop Required";
        this.dockerEl.className = "spec-value text-warning";
      }
    }

    // 3. Network Detection
    if (this.networkIpEl && d.network) {
      const ip = d.network.recommended_ip || "127.0.0.1";
      this.networkIpEl.textContent = ip;
      if (this.networkTypeEl) {
        if (d.network.tailscale_ip && ip === d.network.tailscale_ip) {
          this.networkTypeEl.textContent = "⚡ Tailscale VPN (Direct Encrypted)";
        } else {
          this.networkTypeEl.textContent = "Local Network (LAN)";
        }
      }
    }

    // 4. Token
    if (this.tokenInput && !this.tokenInput.matches(":focus")) {
      this.tokenInput.value = d.token || "";
    }

    // 5. Sharing State & Invite Card
    const isSharing = !!d.is_sharing;
    if (this.inviteCard) {
      this.inviteCard.classList.toggle("hidden", !isSharing);
    }
    if (this.btnToggleShareText) {
      this.btnToggleShareText.textContent = isSharing ? "Stop Sharing GPU" : "Start Sharing GPU";
    }
    if (this.btnToggleShare) {
      this.btnToggleShare.className = isSharing ? "btn btn-danger btn-lg" : "btn btn-primary btn-lg";
    }

    if (isSharing && d.network) {
      const ip = d.network.recommended_ip || "localhost";
      const port = d.port || 8000;
      const hostUrl = `http://${ip}:${port}`;
      if (this.inviteUrlEl) this.inviteUrlEl.textContent = hostUrl;
      if (this.invitePortEl) this.invitePortEl.textContent = `Port ${d.inference_port || 8001}`;
      if (this.inviteTokenEl) this.inviteTokenEl.textContent = d.token || "";
    }
  }

  async startSharingMode() {
    const connModal = document.getElementById("connectionOverlay");
    if (connModal) connModal.classList.add("hidden");

    if (window.authManager && window.authManager.isDesktop) {
      try {
        const info = await window.authManager.setConnection("share");
        if (info && info.url) {
          window.authManager.base = info.url;
          window.authManager.token = info.token;
          window.authManager.mode = info.mode;
          window.authManager.unconfigured = false;
        }
      } catch (err) {
        console.error("Failed to set share connection:", err);
      }
    }

    if (window.appManager) {
      window.appManager.ensureManagersStarted();
    }
    this.open();
  }

  async toggleSharing() {
    const isCurrentlySharing = this.shareData && this.shareData.is_sharing;

    if (window.authManager && window.authManager.isDesktop) {
      const targetMode = isCurrentlySharing ? "local" : "share";
      try {
        if (this.btnToggleShareText) {
          this.btnToggleShareText.textContent = "Applying Configuration...";
        }
        await window.authManager.setConnection(targetMode);
        setTimeout(() => this.refresh(), 1000);
      } catch (err) {
        alert("Failed to toggle sharing mode: " + err);
      }
    } else {
      // Browser mode: already bound, toggle container / invite card
      if (this.inviteCard) {
        this.inviteCard.classList.toggle("hidden");
      }
    }
  }

  async saveCustomToken() {
    if (!this.tokenInput) return;
    const val = this.tokenInput.value.trim();
    if (!val || val.length < 4) {
      alert("Token must be at least 4 characters.");
      return;
    }
    try {
      const res = await window.authManager.api("/api/host/configure-token", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: val }),
      });
      if (res.ok) {
        if (window.authManager) window.authManager.token = val;
        await this.refresh();
        alert("Access Token updated successfully!");
      } else {
        const err = await res.json();
        alert("Failed to update token: " + (err.detail || "Unknown error"));
      }
    } catch (e) {
      alert("Error saving token: " + e);
    }
  }

  async allowFirewall() {
    try {
      let triggered = false;
      // 1. Try Tauri IPC if on desktop
      if (window.__TAURI__ && window.__TAURI__.core) {
        await window.__TAURI__.core.invoke("request_firewall_rule");
        triggered = true;
      } else {
        const res = await window.authManager.api("/api/host/firewall-rule", { method: "POST" });
        if (res.ok) triggered = true;
      }
      if (triggered) {
        this.showToast("UAC Prompt requested. Please click 'Yes'.");
        setTimeout(() => this.refresh(), 1500);
        setTimeout(() => this.refresh(), 4000);
      }
    } catch (e) {
      alert("Could not trigger firewall helper: " + e);
    }
  }

  copyText(text) {
    if (!text) return;
    navigator.clipboard.writeText(text.trim()).then(() => {
      this.showToast("Copied to clipboard!");
    });
  }

  copyFullInvite() {
    if (!this.shareData || !this.shareData.network) return;
    const ip = this.shareData.network.recommended_ip || "localhost";
    const port = this.shareData.port || 8000;
    const token = this.shareData.token || "";
    const infPort = this.shareData.inference_port || 8001;

    const invite =
`⚡ GConnect GPU Server Ready!
Host URL:         http://${ip}:${port}
Access Token:     ${token}
Inference Stream: Port ${infPort}

(Paste into GConnect or connect your Kritix client to start)`;

    navigator.clipboard.writeText(invite).then(() => {
      if (this.btnCopyFull) {
        const orig = this.btnCopyFull.innerHTML;
        this.btnCopyFull.innerHTML = `<i data-lucide="check"></i> <span>✓ Copied Invite to Clipboard!</span>`;
        if (window.lucide) window.lucide.createIcons();
        setTimeout(() => {
          this.btnCopyFull.innerHTML = orig;
          if (window.lucide) window.lucide.createIcons();
        }, 2500);
      }
    });
  }

  handleSmartPaste(text) {
    if (!text) return;

    // Detect Host URL
    const urlMatch = text.match(/https?:\/\/[^\s]+/i);
    // Detect Access Token
    const tokenMatch = text.match(/(?:Access Token|Token|token):\s*([A-Za-z0-9_-]+)/i) ||
                       text.match(/(?:CONNECT_AUTH_TOKEN|token)=([A-Za-z0-9_-]+)/i);

    const urlInput = document.getElementById("connectUrlInput");
    const tokenInput = document.getElementById("connectTokenInput");

    if (urlMatch && urlInput) {
      urlInput.value = urlMatch[0].trim();
      urlInput.classList.add("input-highlight-pulse");
      setTimeout(() => urlInput.classList.remove("input-highlight-pulse"), 1000);
    }

    if (tokenMatch && tokenInput) {
      tokenInput.value = tokenMatch[1].trim();
      tokenInput.classList.add("input-highlight-pulse");
      setTimeout(() => tokenInput.classList.remove("input-highlight-pulse"), 1000);
    }
  }

  showToast(msg) {
    const toast = document.createElement("div");
    toast.className = "copy-toast";
    toast.textContent = msg;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add("visible"), 10);
    setTimeout(() => {
      toast.classList.remove("visible");
      setTimeout(() => toast.remove(), 300);
    }, 1800);
  }
}

window.shareManager = new ShareManager();
