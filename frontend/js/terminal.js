class TerminalManager {
  constructor() {
    this.term = null;
    this.fitAddon = null;
    this.ws = null;
    this.statusBadge = document.getElementById("terminalStatusBadge");
    this.reconnectTimeout = null;
    this._connecting = false;
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
      lineHeight: 1.15,
      fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace",
      convertEol: true,
      scrollback: 10000,
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

    // Terminal Keyboard Shortcuts (Copy, Paste, SIGINT handling)
    this.term.attachCustomKeyEventHandler((event) => {
      // 1. Ctrl+C or Cmd+C: Copy if selection exists; otherwise pass through (SIGINT)
      if ((event.ctrlKey || event.metaKey) && (event.key === "c" || event.key === "C")) {
        if (this.term.hasSelection()) {
          if (event.type === "keydown") {
            navigator.clipboard.writeText(this.term.getSelection());
          }
          return false; // Don't send ^C to the pty
        }
        return true; // Pass through to pty to interrupt running command
      }

      // 2. Ctrl+V, Cmd+V, or Ctrl+Shift+V: Paste from clipboard
      if ((event.ctrlKey || event.metaKey) && (event.key === "v" || event.key === "V")) {
        if (event.type === "keydown") {
          navigator.clipboard.readText().then((text) => {
            if (text && this.ws && this.ws.readyState === WebSocket.OPEN) {
              this.ws.send(text);
            }
          }).catch((err) => console.warn("Clipboard read error:", err));
        }
        return false;
      }

      // 3. Shift+Insert: Paste from clipboard
      if (event.shiftKey && event.key === "Insert") {
        if (event.type === "keydown") {
          navigator.clipboard.readText().then((text) => {
            if (text && this.ws && this.ws.readyState === WebSocket.OPEN) {
              this.ws.send(text);
            }
          }).catch((err) => console.warn("Clipboard read error:", err));
        }
        return false;
      }

      return true;
    });

    const container = document.getElementById("xtermContainer");
    if (container) {
      this.term.open(container);

      // Right-click paste support (matching Windows Terminal / VS Code)
      container.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        if (this.term.hasSelection()) {
          navigator.clipboard.writeText(this.term.getSelection());
          this.term.clearSelection();
        } else {
          navigator.clipboard.readText().then((text) => {
            if (text && this.ws && this.ws.readyState === WebSocket.OPEN) {
              this.ws.send(text);
            }
          }).catch((err) => console.warn("Clipboard read error on contextmenu:", err));
        }
      });

      setTimeout(() => this.fit(), 100);
    }

    this.connect();

    this.term.onData((data) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Text frames carry raw terminal input only.
        this.ws.send(data);
      }
    });

    window.addEventListener("resize", () => {
      this.fit();
    });
  }

  /**
   * Idempotent: tears down any existing socket before connecting so
   * reconnects and container restarts never leak sessions server-side.
   */
  connect() {
    if (this._connecting) return;
    this._connecting = true;

    try {
      this._teardownSocket();
    } catch (err) {
      console.warn("Socket teardown error:", err);
    }

    window.authManager.ready().then(() => {
      const url = window.authManager.wsUrl("/ws/terminal");

      try {
        this.ws = new WebSocket(url);
        // Accept binary frames so the control channel stays separate from text.
        this.ws.binaryType = "arraybuffer";
      } catch (err) {
        console.warn("Terminal WebSocket error:", err);
        this.setStatus(false);
        this._connecting = false;
        this.scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this._connecting = false;
        this.fit();
      };

      this.ws.onmessage = (event) => {
        if (typeof event.data === "string" && event.data.includes("Docker sandbox is not running")) {
          this.setStatus("disconnected");
        } else {
          this.setStatus("connected");
        }

        if (this.term) {
          this.term.write(event.data);
          this.term.scrollToBottom();
        }
        // Feed the PROBLEMS parser (Python tracebacks in the output).
        if (window.problemsManager) {
          window.problemsManager.ingest(event.data);
        }
      };

      this.ws.onclose = (event) => {
        this._connecting = false;
        this.setStatus("disconnected");
        if (event && event.code === 4500) {
          // Server says the sandbox is unavailable (no Docker): stop the
          // reconnect loop - the yellow banner + Retry button take over.
          this.ws = null;
          return;
        }
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        if (this.ws) this.ws.close();
      };
    });
  }

  _teardownSocket() {
    if (this.reconnectTimeout) {
      clearTimeout(this.reconnectTimeout);
      this.reconnectTimeout = null;
    }
    if (this.ws) {
      // Remove handlers so an intentional close does not trigger reconnect.
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onmessage = null;
      try {
        this.ws.close();
      } catch (err) { /* already closed */ }
      this.ws = null;
    }
  }

  scheduleReconnect() {
    if (this.reconnectTimeout) return;
    this.reconnectTimeout = setTimeout(() => {
      this.reconnectTimeout = null;
      this.connect();
    }, 3000);
  }

  setStatus(state) {
    if (!this.statusBadge) return;
    if (state === true) state = "connected";
    if (state === false) state = "disconnected";
    if (state === "connected") {
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
        this.term.scrollToBottom();
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          // Control messages travel as BINARY frames - text frames are raw
          // terminal input, so no sniffing is needed server-side.
          const payload = JSON.stringify({
            type: "resize",
            cols: this.term.cols,
            rows: this.term.rows,
          });
          this.ws.send(new TextEncoder().encode(payload));
        }
      } catch (err) {
        /* container not measurable yet */
      }
    }
  }

  sendCommand(cmd) {
    // A fresh python run invalidates previous TERMINAL problems; editor
    // lint diagnostics are independent and stay.
    if (cmd.trim().startsWith("python") && window.problemsManager) {
      window.problemsManager.clearTerminalProblems();
    }
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
