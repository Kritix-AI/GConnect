class ClusterManager {
  constructor() {
    this.overlay = document.getElementById("clusterOverlay");
    this.btnOpen = document.getElementById("btnCluster");
    this.btnClose = document.getElementById("btnCloseCluster");
    this.btnLabel = document.getElementById("clusterBtnLabel");
    this.nodeList = document.getElementById("clusterNodeList");
    this.nodeCount = document.getElementById("clusterNodeCount");
    this.gpuCount = document.getElementById("clusterGpuCount");
    this.vram = document.getElementById("clusterVram");
    this.inputName = document.getElementById("clusterNodeName");
    this.inputUrl = document.getElementById("clusterNodeUrl");
    this.inputToken = document.getElementById("clusterNodeToken");
    this.btnAdd = document.getElementById("btnAddClusterNode");
    this.refreshTimer = null;
  }

  init() {
    if (this.btnOpen) {
      this.btnOpen.addEventListener("click", () => this.open());
    }
    if (this.btnClose) {
      this.btnClose.addEventListener("click", () => this.close());
    }
    if (this.btnAdd) {
      this.btnAdd.addEventListener("click", () => this.addNode());
    }
    [this.inputName, this.inputUrl, this.inputToken].forEach((el) => {
      if (el) {
        el.addEventListener("keydown", (e) => {
          if (e.key === "Enter") this.addNode();
        });
      }
    });

    // Keep the header button label fresh even while the panel is closed.
    this.refreshStatus();
    setInterval(() => this.refreshStatus(), 10000);
  }

  open() {
    if (this.overlay) {
      this.overlay.classList.remove("hidden");
      this.refreshStatus();
      this.refreshTimer = setInterval(() => this.refreshStatus(), 4000);
      if (window.lucide) window.lucide.createIcons();
    }
  }

  close() {
    if (this.overlay) {
      this.overlay.classList.add("hidden");
    }
    if (this.refreshTimer) {
      clearInterval(this.refreshTimer);
      this.refreshTimer = null;
    }
  }

  async refreshStatus() {
    try {
      const res = await window.authManager.api("/api/cluster/status");
      if (!res.ok) return;
      const data = await res.json();
      this.render(data);
    } catch (err) {
      console.warn("Cluster status refresh failed:", err);
    }
  }

  render(data) {
    if (this.btnLabel) {
      this.btnLabel.textContent =
        `${data.online_count}/${data.node_count} PCs · ` +
        `${(data.pooled_vram_total_mb / 1024).toFixed(1)} GB`;
    }

    if (this.nodeCount) {
      this.nodeCount.textContent = `${data.online_count}/${data.node_count} online`;
    }
    if (this.gpuCount) {
      this.gpuCount.textContent = `${data.total_gpus}`;
    }
    if (this.vram) {
      const used = (data.pooled_vram_used_mb / 1024).toFixed(1);
      const total = (data.pooled_vram_total_mb / 1024).toFixed(1);
      this.vram.textContent = `${used} / ${total} GB`;
    }

    if (!this.nodeList) return;
    this.nodeList.innerHTML = "";

    (data.nodes || []).forEach((node) => {
      const el = document.createElement("div");
      el.className = `cluster-node ${node.online ? "online" : "offline"}`;

      const statusDot = node.online
        ? '<span class="cluster-dot online" title="online"></span>'
        : '<span class="cluster-dot offline" title="offline"></span>';

      const host = node.host || {};
      const hostLine = host.ram_total_mb
        ? `${escapeHtml(host.name || "CPU")} · CPU ${host.percent ?? 0}% · RAM ${(host.ram_used_mb / 1024).toFixed(1)}/${(host.ram_total_mb / 1024).toFixed(1)} GB`
        : "";

      const gpus = node.gpus && node.gpus.length
        ? node.gpus.map((g) =>
            `${escapeHtml(g.name)} · ${(g.vram_used_mb / 1024).toFixed(1)}/${(g.vram_total_mb / 1024).toFixed(1)} GB · ${g.gpu_utilization_percent}% · ${g.temperature_c}°C`
          ).join("<br>")
        : node.telemetry_degraded
          ? "GPU detected but sensor could not be read (no signal)"
          : node.telemetry_is_mock
            ? (hostLine || "No GPU detected")
            : "";

      const vramPct = Math.min(Math.max(node.vram_percent || 0, 0), 100);

      el.innerHTML = `
        <div class="cluster-node-head">
          ${statusDot}
          <span class="cluster-node-name">${escapeHtml(node.name)}</span>
          ${node.local ? '<span class="cluster-local-tag">THIS PC</span>' : ""}
          <span class="cluster-node-gpus">${node.gpu_count} GPU${node.gpu_count === 1 ? "" : "s"}</span>
          ${!node.local ? `
            <button class="icon-btn cluster-node-remove" title="Remove node">
              <i data-lucide="trash" class="icon-xs"></i>
            </button>` : ""}
        </div>
        <div class="cluster-node-url">${escapeHtml(node.url)}${node.error ? ` <span class="cluster-error">— ${escapeHtml(node.error)}</span>` : ""}</div>
        <div class="cluster-node-detail">${gpus}</div>
        <div class="vram-meter-container cluster-vram-bar">
          <div class="vram-meter-fill ${vramPct > 90 ? "critical" : vramPct > 70 ? "warning" : ""}" style="width: ${vramPct}%;"></div>
        </div>
      `;

      const removeBtn = el.querySelector(".cluster-node-remove");
      if (removeBtn) {
        removeBtn.addEventListener("click", (e) => {
          e.stopPropagation();
          this.removeNode(node.name);
        });
      }

      this.nodeList.appendChild(el);
    });

    if (window.lucide) window.lucide.createIcons();
  }

  async addNode() {
    const name = this.inputName?.value.trim();
    const url = this.inputUrl?.value.trim();
    const token = this.inputToken?.value.trim();

    if (!name || !url || !token) {
      alert("Fill in name, URL and token of the remote GConnect daemon.");
      return;
    }

    try {
      const res = await window.authManager.api("/api/cluster/nodes", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, url, token }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        alert(data.detail || "Failed to add node.");
        return;
      }
      if (this.inputName) this.inputName.value = "";
      if (this.inputUrl) this.inputUrl.value = "";
      if (this.inputToken) this.inputToken.value = "";
      this.refreshStatus();
    } catch (err) {
      console.error("Add cluster node error:", err);
      alert("Error adding node.");
    }
  }

  async removeNode(name) {
    if (!confirm(`Remove cluster node '${name}'?`)) return;
    try {
      const res = await window.authManager.api("/api/cluster/nodes", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      if (!res.ok) throw new Error("failed");
      this.refreshStatus();
    } catch (err) {
      console.error("Remove cluster node error:", err);
      alert("Failed to remove node.");
    }
  }
}

window.clusterManager = new ClusterManager();
