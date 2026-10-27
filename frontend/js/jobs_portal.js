class SharedGPUJobPortal {
  constructor() {
    this.token = sessionStorage.getItem("gconnect_job_token") || "";
    this.login = document.getElementById("jobPortalLogin");
    this.app = document.getElementById("jobPortalApp");
    this.tokenInput = document.getElementById("jobPortalToken");
    this.loginError = document.getElementById("jobPortalLoginError");
    this.error = document.getElementById("jobPortalError");
    this.list = document.getElementById("portalJobList");
    this.timer = null;
  }

  init() {
    document.getElementById("btnJobPortalConnect")?.addEventListener("click", () => this.connect());
    this.tokenInput?.addEventListener("keydown", (e) => { if (e.key === "Enter") this.connect(); });
    document.getElementById("btnPortalSubmit")?.addEventListener("click", () => this.submit());
    document.getElementById("btnJobPortalRefresh")?.addEventListener("click", () => this.refresh());
    document.getElementById("btnJobPortalDisconnect")?.addEventListener("click", () => this.disconnect());
    if (window.lucide) window.lucide.createIcons();
    if (this.token) this.showApp();
  }

  async api(path, options = {}) {
    return fetch(path, {
      ...options,
      headers: { ...(options.headers || {}), Authorization: `Bearer ${this.token}` },
    });
  }

  async connect() {
    const token = this.tokenInput?.value.trim();
    if (!token) return this.showError(this.loginError, "Enter the developer access token.");
    this.token = token;
    try {
      const res = await this.api("/api/jobs");
      if (!res.ok) throw new Error(res.status === 403 ? "This token is not allowed to use GPU jobs." : "Token is invalid or revoked.");
      sessionStorage.setItem("gconnect_job_token", token);
      this.showApp();
    } catch (err) {
      this.token = "";
      this.showError(this.loginError, err.message || "Could not connect.");
    }
  }

  showApp() {
    this.login?.classList.add("hidden");
    this.app?.classList.remove("hidden");
    document.getElementById("btnJobPortalDisconnect")?.classList.remove("hidden");
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 4000);
  }

  disconnect() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.token = "";
    sessionStorage.removeItem("gconnect_job_token");
    this.app?.classList.add("hidden");
    this.login?.classList.remove("hidden");
    document.getElementById("btnJobPortalDisconnect")?.classList.add("hidden");
    if (this.tokenInput) this.tokenInput.value = "";
  }

  async refresh() {
    try {
      const res = await this.api("/api/jobs");
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not load jobs.");
      this.render(data.jobs || []);
    } catch (err) {
      this.showError(this.error, err.message || "Could not load jobs.");
    }
  }

  async submit() {
    const script = document.getElementById("portalJobScript")?.value || "";
    if (!script.trim()) return this.showError(this.error, "Enter Python code first.");
    const button = document.getElementById("btnPortalSubmit");
    button.disabled = true;
    button.textContent = "Submitting…";
    this.clearError(this.error);
    try {
      const res = await this.api("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: document.getElementById("portalJobName")?.value.trim() || "GPU job",
          script,
          gpu_count: Number(document.getElementById("portalGpuCount")?.value || 1),
          timeout_seconds: Number(document.getElementById("portalTimeout")?.value || 3600),
          network_access: Boolean(document.getElementById("portalNetwork")?.checked),
          distributed: Boolean(document.getElementById("portalDistributed")?.checked),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not submit job.");
      await this.refresh();
    } catch (err) {
      this.showError(this.error, err.message || "Could not submit job.");
    } finally {
      button.disabled = false;
      button.textContent = "Submit job";
    }
  }

  render(jobs) {
    const opened = new Set([...this.list.querySelectorAll("details[open]")].map((d) => d.dataset.jobId));
    this.list.replaceChildren();
    if (!jobs.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = "No jobs yet.";
      this.list.appendChild(empty);
      return;
    }
    for (const job of jobs) {
      const card = document.createElement("article");
      card.className = "gpu-job-card";
      const head = document.createElement("div");
      head.className = "gpu-job-card-head";
      const name = document.createElement("strong");
      name.textContent = job.name || "GPU job";
      const state = document.createElement("span");
      state.className = `gpu-job-state state-${job.state || "unknown"}`;
      state.textContent = job.state || "unknown";
      head.append(name, state);
      const info = document.createElement("div");
      info.className = "gpu-job-card-info";
      info.textContent = `${job.node || "provider"} · ${job.gpu_count || 1} GPU${job.gpu_count === 1 ? "" : "s"} · ${job.created_at ? new Date(job.created_at * 1000).toLocaleString() : ""}`;
      const details = document.createElement("details");
      details.dataset.jobId = job.id;
      details.open = opened.has(job.id);
      const summary = document.createElement("summary");
      summary.textContent = "Logs";
      const logs = document.createElement("pre");
      logs.className = "gpu-job-logs";
      logs.textContent = job.logs || "Waiting for provider…";
      details.append(summary, logs);
      const actions = document.createElement("div");
      actions.className = "gpu-job-card-actions";
      actions.appendChild(details);
      if (job.state === "completed") {
        const download = document.createElement("button");
        download.className = "btn btn-secondary btn-sm";
        download.textContent = "Download output";
        download.addEventListener("click", () => this.download(job.id));
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
      this.list.appendChild(card);
    }
  }

  async cancel(id) {
    try {
      const res = await this.api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Could not cancel job.");
      await this.refresh();
    } catch (err) {
      this.showError(this.error, err.message || "Could not cancel job.");
    }
  }

  async download(id) {
    try {
      const res = await this.api(`/api/jobs/${encodeURIComponent(id)}/artifacts`);
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Could not download artifacts.");
      }
      const url = URL.createObjectURL(await res.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `gconnect-${id.slice(0, 12)}-output.zip`;
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      this.showError(this.error, err.message || "Could not download artifacts.");
    }
  }

  showError(target, message) {
    if (!target) return;
    target.textContent = message;
    target.classList.remove("hidden");
  }

  clearError(target) {
    if (!target) return;
    target.textContent = "";
    target.classList.add("hidden");
  }
}

new SharedGPUJobPortal().init();
