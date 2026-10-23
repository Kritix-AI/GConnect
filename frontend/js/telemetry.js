class TelemetryManager {
  constructor() {
    this.ws = null;
    this.modelText = document.getElementById("gpuModelText");
    this.vramBar = document.getElementById("vramBar");
    this.vramValue = document.getElementById("vramValue");
    this.loadValue = document.getElementById("gpuLoadValue");
    this.tempValue = document.getElementById("gpuTempValue");
    this.reconnectTimeout = null;
  }

  init() {
    this.connect();
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/telemetry`;

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn("WebSocket init error:", err);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
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
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      if (this.ws) {
        this.ws.close();
      }
    };
  }

  scheduleReconnect() {
    if (this.reconnectTimeout) return;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, 3000);
  }

  renderStats(stat) {
    if (this.modelText) {
      this.modelText.textContent = stat.is_mock
        ? `${stat.gpu_name}`
        : stat.gpu_name;
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
      this.loadValue.textContent = `${stat.gpu_utilization_percent}%`;
    }

    if (this.tempValue) {
      this.tempValue.textContent = `${stat.temperature_c}°C`;
    }
  }
}

window.telemetryManager = new TelemetryManager();

