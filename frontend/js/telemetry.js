class TelemetryManager {
  constructor() {
    this.ws = null;
    this.modelText = document.getElementById("gpuModelText");
    this.simBadge = document.getElementById("gpuSimBadge");
    this.staleBadge = document.getElementById("telemetryStaleBadge");
    this.liveDot = document.getElementById("telemetryLiveDot");
    this.vramBar = document.getElementById("vramBar");
    this.vramValue = document.getElementById("vramValue");
    this.loadValue = document.getElementById("gpuLoadValue");
    this.tempValue = document.getElementById("gpuTempValue");
    this.reconnectTimeout = null;
    this._connecting = false;
    this._lastUpdate = 0;
  }

  init() {
    this.connect();
    // Freshness watchdog: if no snapshot arrives for >3s, flag the display.
    this._staleTimer = setInterval(() => this._checkFreshness(), 1000);
  }

  _checkFreshness() {
    const stale = this._lastUpdate > 0 && (Date.now() - this._lastUpdate) > 3000;
    if (this.staleBadge) {
      this.staleBadge.classList.toggle("hidden", !stale);
    }
    if (this.liveDot) {
      this.liveDot.classList.toggle("stale", stale);
    }
  }

  connect() {
    if (this._connecting) return;
    this._connecting = true;

    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      try { this.ws.close(); } catch (err) { /* already closed */ }
      this.ws = null;
    }

    window.authManager.ready().then(() => {
      const url = window.authManager.wsUrl("/ws/telemetry");

      try {
        this.ws = new WebSocket(url);
      } catch (err) {
        console.warn("WebSocket init error:", err);
        this._connecting = false;
        this.scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this._connecting = false;
        // Arm the freshness clock at connect time: if the server never
        // delivers a first snapshot, STALE must still appear.
        this._lastUpdate = Date.now();
        console.log("Telemetry WebSocket connected.");
      };

      this.ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "gpu_stat") {
            this.renderStats(data);
          }
        } catch (err) {
          console.error("Telemetry parse error:", err);
        }
      };

      this.ws.onclose = () => {
        this._connecting = false;
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        if (this.ws) {
          this.ws.close();
        }
      };
    });
  }

  scheduleReconnect() {
    if (this.reconnectTimeout) return;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, 3000);
  }

  renderStats(stat) {
    this._lastUpdate = Date.now();
    if (this.staleBadge) this.staleBadge.classList.add("hidden");
    if (this.liveDot) this.liveDot.classList.remove("stale");

    if (this.modelText) {
      const count = stat.gpu_count || 0;
      this.modelText.textContent = count > 1
        ? `${stat.gpu_name} ×${count}`
        : stat.gpu_name;
      // Per-GPU detail on hover: SM (compute) vs memory-controller load.
      if (stat.gpus && stat.gpus.length > 0) {
        this.modelText.title = stat.gpus
          .map((g) => {
            const sm = g.sm_utilization_percent ?? g.gpu_utilization_percent;
            const mc = g.memory_controller_percent ?? 0;
            return `GPU${g.index}: ${g.name}\n  VRAM ${(g.vram_used_mb / 1024).toFixed(1)}/${(g.vram_total_mb / 1024).toFixed(1)} GB\n  Utilization ${g.gpu_utilization_percent}% (SM ${sm}% · MemCtl ${mc}%)\n  Temp ${g.temperature_c}°C`;
          })
          .join("\n\n") +
          "\n\nUtilization = max(SM/compute, memory controller) - comparable to Task Manager. SM-only is the pure compute figure.";
      }
    }

    // Honest fallback when no NVIDIA GPU is present (no fabricated numbers).
    // "degraded" = GPU exists but the sensor read failed (transient NVML
    // error); last-good values keep flowing from the backend meanwhile.
    if (this.simBadge) {
      this.simBadge.classList.toggle("hidden", !stat.is_mock);
      this.simBadge.textContent = stat.degraded ? "NO SIGNAL" : "NO GPU";
      this.simBadge.title = stat.degraded
        ? "GPU detected but the sensor could not be read - showing zeros"
        : "No NVIDIA GPU detected on this machine";
    }

    const usedGb = (stat.vram_used_mb / 1024).toFixed(1);
    const totalGb = (stat.vram_total_mb / 1024).toFixed(1);

    if (this.vramValue) {
      this.vramValue.textContent = `${usedGb} / ${totalGb} GB`;
    }

    if (this.vramBar) {
      const pct = Math.min(Math.max(stat.vram_percent, 0), 100);
      this.vramBar.style.width = `${pct}%`;

      this.vramBar.classList.remove("warning", "critical");
      if (pct > 90) {
        this.vramBar.classList.add("critical");
      } else if (pct > 70) {
        this.vramBar.classList.add("warning");
      }
    }

    if (this.loadValue) {
      const sm = stat.sm_utilization_percent ?? stat.gpu_utilization_percent;
      this.loadValue.textContent = `${stat.gpu_utilization_percent}%`;
      this.loadValue.title = `Utilization (max of SM ${sm}% / memory controller) - Task Manager comparable.\nSM (pure compute) is ${sm}%.`;
    }

    if (this.tempValue) {
      this.tempValue.textContent = `${stat.temperature_c}°C`;
    }
  }
}

window.telemetryManager = new TelemetryManager();
