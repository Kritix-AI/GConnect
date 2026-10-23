class TerminalManager {
  constructor() {
    this.term = null;
    this.fitAddon = null;
    this.ws = null;
    this.statusBadge = document.getElementById("terminalStatusBadge");
    this.reconnectTimeout = null;
  }

  init() {
    if (typeof Terminal === "undefined") {
      console.error("xterm.js library not found.");
      return;
    }

    this.term = new Terminal({
      cursorBlink: true,
      cursorStyle: "bar",
      fontSize: 13,
      fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace",
      theme: {
        background: "#05070a",
        foreground: "#d8dee9",
        cursor: "#58a6ff",
        selectionBackground: "rgba(88, 166, 255, 0.3)",
        black: "#1e222a",
        red: "#f85149",
        green: "#2ea043",
        yellow: "#d29922",
        blue: "#58a6ff",
        magenta: "#bc8cff",
        cyan: "#39c5cf",
        white: "#e6edf3",
      },
    });

    if (typeof FitAddon !== "undefined") {
      this.fitAddon = new FitAddon.FitAddon();
      this.term.loadAddon(this.fitAddon);
    }

    if (typeof WebLinksAddon !== "undefined") {
      this.term.loadAddon(new WebLinksAddon.WebLinksAddon());
    }

    const container = document.getElementById("xtermContainer");
    if (container) {
      this.term.open(container);
      setTimeout(() => this.fit(), 100);
    }

    this.connect();

    this.term.onData((data) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(data);
      }
    });

    window.addEventListener("resize", () => {
      this.fit();
    });
  }

  connect() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/terminal`;

    try {
      this.ws = new WebSocket(url);
    } catch (err) {
      console.warn("Terminal WebSocket error:", err);
      this.setStatus(false);
      this.scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      this.setStatus(true);
      this.fit();
    };

    this.ws.onmessage = (event) => {
      if (this.term) {
        this.term.write(event.data);
      }
    };

    this.ws.onclose = () => {
      this.setStatus(false);
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      if (this.ws) this.ws.close();
    };
  }

  scheduleReconnect() {
    if (this.reconnectTimeout) return;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, 3000);
  }

  setStatus(connected) {
    if (!this.statusBadge) return;
    if (connected) {
      this.statusBadge.textContent = "Connected";
      this.statusBadge.className = "connection-status connected";
    } else {
      this.statusBadge.textContent = "Disconnected";
      this.statusBadge.className = "connection-status disconnected";
    }
  }

  fit() {
    if (this.fitAddon && this.term) {
      try {
        this.fitAddon.fit();
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          const resizePayload = JSON.stringify({
            type: "resize",
            cols: this.term.cols,
            rows: this.term.rows,
          });
          this.ws.send(resizePayload);
        }
      } catch (err) {
      }
    }
  }

  sendCommand(cmd) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(`${cmd}\n`);
    } else {
      console.warn("Terminal socket is not open to send command.");
    }
  }

  clear() {
    if (this.term) {
      this.term.clear();
    }
  }

  focus() {
    if (this.term) {
      this.term.focus();
    }
  }
}

window.terminalManager = new TerminalManager();

