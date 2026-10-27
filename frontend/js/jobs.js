class GPUJobsManager {
  constructor() {
    this.overlay = document.getElementById("gpuJobsOverlay");
    this.openButton = document.getElementById("btnGpuJobs");
    this.closeButton = document.getElementById("btnCloseGpuJobs");
    this.submitButton = document.getElementById("btnSubmitGpuJob");
    this.refreshButton = document.getElementById("btnRefreshGpuJobs");
    this.sourceLabel = document.getElementById("gpuJobSource");
    this.nameInput = document.getElementById("gpuJobName");
    this.nodeSelect = document.getElementById("gpuJobNode");
    this.gpuCountSelect = document.getElementById("gpuJobGpuCount");
    this.timeoutSelect = document.getElementById("gpuJobTimeout");
    this.networkCheckbox = document.getElementById("gpuJobNetwork");
    this.distributedCheckbox = document.getElementById("gpuJobDistributed");
    this.errorBox = document.getElementById("gpuJobError");
    this.jobList = document.getElementById("gpuJobList");
    this.refreshTimer = null;
    this.providerCounts = new Map();
    this.submitting = false;
  }

  init() {
    this.openButton?.addEventListener("click", () => this.open());
    this.closeButton?.addEventListener("click", () => this.close());
    this.submitButton?.addEventListener("click", () => this.submit());
    this.refreshButton?.addEventListener("click", () => this.refresh());
    this.nodeSelect?.addEventListener("change", () => this.updateGpuChoices());
  }

  open() {
    this.overlay?.classList.remove("hidden");
    this.refreshProviders();
    this.refresh();
    this.refreshTimer = setInterval(() => this.refresh(), 4000);
    if (window.lucide) window.lucide.createIcons();
  }

  close() {
    this.overlay?.classList.add("hidden");
    if (this.refreshTimer) clearInterval(this.refreshTimer);
    this.refreshTimer = null;
  }

  async refreshProviders() {
    if (!this.nodeSelect) return;
    try {
      const res = await window.authManager.api("/api/cluster/status");
      if (!res.ok) return;
      const data = await res.json();
      const selected = this.nodeSelect.value;
      this.providerCounts.clear();
      this.nodeSelect.replaceChildren(new Option("Auto-select provider (queue if busy)", ""));
      for (const node of data.nodes || []) {
        if (!node.online || !node.gpu_passthrough || (!node.local && !node.jobs_enabled) || !node.gpus?.length) continue;
        const free = node.gpus.filter((gpu) => Number(gpu.vram_free_mb) >= 512).length;
        if (!free) continue;
        this.providerCounts.set(node.name, free);
        const label = `${node.name} · ${free} GPU${free === 1 ? "" : "s"} available`;
        this.nodeSelect.add(new Option(label, node.name));
      }
      if ([...this.nodeSelect.options].some((option) => option.value === selected)) {
        this.nodeSelect.value = selected;
      }
      this.updateGpuChoices();
    } catch (err) {
      console.warn("Could not refresh GPU providers:", err);
    }
  }

  updateGpuChoices() {
    if (!this.gpuCountSelect) return;
    const selected = this.gpuCountSelect.value;
    const count = this.nodeSelect?.value
      ? (this.providerCounts.get(this.nodeSelect.value) || 0)
      : Math.max(0, ...this.providerCounts.values());
    this.gpuCountSelect.replaceChildren();
    const availableCount = Math.min(Math.max(count, 1), 16);
    for (let n = 1; n <= availableCount; n += 1) {
      this.gpuCountSelect.add(new Option(`${n} GPU${n === 1 ? "" : "s"}`, String(n)));
    }
    this.gpuCountSelect.value = [...this.gpuCountSelect.options].some((o) => o.value === selected) ? selected : "1";
    if (this.submitButton) this.submitButton.disabled = this.submitting;
  }

  async submit() {
    const path = window.editorManager?.activeFilePath;
    if (!path || !path.toLowerCase().endsWith(".py")) {
      this.showError("Open a Python (.py) file in the editor first.");
      return;
    }
    const script = window.editorManager.getValue();
    if (!script.trim()) {
      this.showError("The active Python file is empty.");
      return;
    }

    this.clearError();
    this.submitting = true;
    this.submitButton.disabled = true;
    this.submitButton.querySelector("span").textContent = "Submitting…";
    try {
      await window.editorManager.saveCurrentFile();
      const res = await window.authManager.api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: this.nameInput?.value.trim() || path,
          script,
          gpu_count: Number(this.gpuCountSelect?.value || 1),
          node: this.nodeSelect?.value || null,
          timeout_seconds: Number(this.timeoutSelect?.value || 3600),
          network_access: Boolean(this.networkCheckbox?.checked),
          distributed: Boolean(this.distributedCheckbox?.checked),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not submit GPU job.");
      if (this.nameInput) this.nameInput.value = "";
      await this.refresh();
    } catch (err) {
      this.showError(err.message || "Could not submit GPU job.");
    } finally {
      this.submitting = false;
      this.submitButton.querySelector("span").textContent = "Submit job";
      this.updateGpuChoices();
    }
  }

  async refresh() {
    const path = window.editorManager?.activeFilePath;
    if (this.sourceLabel) {
      this.sourceLabel.textContent = path?.toLowerCase().endsWith(".py")
        ? `Source: ${path} (current editor contents)`
        : "Open a Python file to submit it as a GPU job";
    }
    try {
      const res = await window.authManager.api("/api/jobs");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not load GPU jobs.");
      this.render(data.jobs || []);
    } catch (err) {
      if (this.jobList) this.jobList.textContent = err.message || "Could not load GPU jobs.";
    }
  }

  render(jobs) {
    if (!this.jobList) return;
    const opened = new Set([...this.jobList.querySelectorAll("details[open]")].map((d) => d.dataset.jobId));
    this.jobList.replaceChildren();
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No GPU jobs submitted yet.";
      this.jobList.appendChild(empty);
      return;
    }

    for (const job of jobs) {
      const card = document.createElement("article");
      card.className = "gpu-job-card";
      const head = document.createElement("div");
      head.className = "gpu-job-card-head";
      const title = document.createElement("strong");
      title.textContent = job.name || "GPU job";
      const state = document.createElement("span");
      state.className = `gpu-job-state state-${job.state || "unknown"}`;
      state.textContent = (job.state || "unknown").replaceAll("_", " ");
      head.append(title, state);

      const info = document.createElement("div");
      info.className = "gpu-job-card-info";
      const created = job.created_at ? new Date(job.created_at * 1000).toLocaleString() : "";
      info.textContent = `${job.node || "provider"} · ${job.gpu_count || 1} GPU${job.gpu_count === 1 ? "" : "s"} · ${created}`;

      const logs = document.createElement("pre");
      logs.className = "gpu-job-logs";
      logs.textContent = job.logs || "Waiting for provider…";

      const actions = document.createElement("div");
      actions.className = "gpu-job-card-actions";
      const detail = document.createElement("details");
      detail.dataset.jobId = job.id;
      detail.open = opened.has(job.id);
      const summary = document.createElement("summary");
      summary.textContent = "Logs";
      detail.append(summary, logs);
      actions.appendChild(detail);
      if (job.state === "completed") {
        const download = document.createElement("button");
        download.className = "btn btn-secondary btn-sm";
        download.textContent = "Download output";
        download.addEventListener("click", () => this.downloadArtifacts(job.id));
        actions.appendChild(download);
      }
      if (["queued", "starting", "running"].includes(job.state)) {
        const cancel = document.createElement("button");
        cancel.className = "btn btn-secondary btn-sm";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", () => this.cancel(job.id));
        actions.appendChild(cancel);
      }
      card.append(head, info, actions);
      this.jobList.appendChild(card);
    }
  }

  async cancel(jobId) {
    try {
      const res = await window.authManager.api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not cancel job.");
      await this.refresh();
    } catch (err) {
      this.showError(err.message || "Could not cancel job.");
    }
  }

  async downloadArtifacts(jobId) {
    try {
      const res = await window.authManager.api(`/api/jobs/${encodeURIComponent(jobId)}/artifacts`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Could not download job output.");
      }
      const blobUrl = URL.createObjectURL(await res.blob());
      const link = document.createElement("a");
      link.href = blobUrl;
      link.download = `gconnect-${jobId.slice(0, 12)}-output.zip`;
      link.click();
      URL.revokeObjectURL(blobUrl);
    } catch (err) {
      this.showError(err.message || "Could not download job output.");
    }
  }

  showError(message) {
    if (!this.errorBox) return;
    this.errorBox.textContent = message;
    this.errorBox.classList.remove("hidden");
  }

  clearError() {
    if (!this.errorBox) return;
    this.errorBox.textContent = "";
    this.errorBox.classList.add("hidden");
  }
}

window.gpuJobsManager = new GPUJobsManager();
