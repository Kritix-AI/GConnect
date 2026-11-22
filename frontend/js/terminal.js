class TerminalSession {
  constructor(id, title, manager) {
    this.id = id;
    this.title = title || "bash";
    this.manager = manager;
    this.term = null;
    this.fitAddon = null;
    this.ws = null;
    this.containerEl = null;
    this.reconnectTimeout = null;
    this._connecting = false;
    this.isConnected = false;
  }

  init() {
    if (typeof Terminal === "undefined") {
      console.error("xterm.js library not found.");
      return;
    }

    const parentContainer = document.getElementById("xtermContainer");
    if (!parentContainer) {
      console.error("#xtermContainer not found.");
      return;
    }

    // Dedicated child container element for this terminal instance inside #xtermContainer
    this.containerEl = document.createElement("div");
    this.containerEl.className = "terminal-session-container";
    this.containerEl.id = `terminalSession_${this.id}`;
    parentContainer.appendChild(this.containerEl);

    this.term = new Terminal({
      cursorBlink: true,
      cursorStyle: "block",
      fontSize: 13,
      lineHeight: 1.15,
      fontFamily: "'JetBrains Mono', Consolas, 'Courier New', monospace",
      convertEol: true,
      scrollback: 10000,
      smoothScrollDuration: 150,
      scrollSensitivity: 1,
      fastScrollSensitivity: 5,
      theme: {
        background: "#05070a",
        foreground: "#c9d1d9",
        cursor: "#58a6ff",
        selectionBackground: "rgba(88, 166, 255, 0.35)",
        black: "#1e222a",
        red: "#f85149",
        green: "#2ea043",
        yellow: "#d29922",
        blue: "#58a6ff",
        magenta: "#bc8cff",
        cyan: "#39c5cf",
        white: "#ffffff",
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
        event.preventDefault();
        if (event.type === "keydown") {
          navigator.clipboard.readText().then((text) => {
            if (text) {
              this.paste(text);
            }
          }).catch((err) => console.warn("Clipboard read error:", err));
        }
        return false;
      }

      // 3. Shift+Insert: Paste from clipboard
      if (event.shiftKey && event.key === "Insert") {
        event.preventDefault();
        if (event.type === "keydown") {
          navigator.clipboard.readText().then((text) => {
            if (text) {
              this.paste(text);
            }
          }).catch((err) => console.warn("Clipboard read error:", err));
        }
        return false;
      }

      return true;
    });

    this.term.open(this.containerEl);

    // Enable bracketed multi-line paste mode on terminal open
    this.term.write("\x1b[?2004h");

    // Copy-on-select support: highlight text to copy automatically
    this.term.onSelectionChange(() => {
      if (this.term.hasSelection()) {
        const selected = this.term.getSelection();
        if (selected && selected.trim()) {
          navigator.clipboard.writeText(selected).catch(() => {});
        }
      }
    });

    this.term.onData((data) => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Text frames carry raw terminal input only.
        this.ws.send(data);
      }
    });

    this.connect();

    setTimeout(() => this.fit(), 100);
  }

  paste(text) {
    if (!text || !this.term) return;
    // Normalize line endings: CRLF and LF to CR
    const normalized = text.replace(/\r\n/g, "\r").replace(/\n/g, "\r");
    this.term.paste(normalized);
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
      const url = window.authManager.wsUrl(`/ws/terminal?session=${encodeURIComponent(this.id)}`);

      try {
        this.ws = new WebSocket(url);
        // Accept binary frames so the control channel stays separate from text.
        this.ws.binaryType = "arraybuffer";
      } catch (err) {
        console.warn("Terminal WebSocket error:", err);
        this.isConnected = false;
        this.manager.onSessionStatusChange(this, "disconnected");
        this._connecting = false;
        this.scheduleReconnect();
        return;
      }

      this.ws.onopen = () => {
        this._connecting = false;
        this.isConnected = true;
        this.manager.onSessionStatusChange(this, "connected");
        // Enable bracketed multi-line paste mode upon connection
        if (this.term) {
          this.term.write("\x1b[?2004h");
        }
        this.fit();
      };

      this.ws.onmessage = (event) => {
        if (typeof event.data === "string" && event.data.includes("Docker sandbox is not running")) {
          this.isConnected = false;
          this.manager.onSessionStatusChange(this, "disconnected");
        } else {
          this.isConnected = true;
          this.manager.onSessionStatusChange(this, "connected");
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
        this.isConnected = false;
        this.manager.onSessionStatusChange(this, "disconnected");
        if (event && event.code === 4500) {
          // Server says the sandbox is unavailable (no Docker): stop reconnect loop
          this.ws = null;
          return;
        }
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.isConnected = false;
        this.manager.onSessionStatusChange(this, "disconnected");
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

  fit() {
    // Safe fit: only measure and resize if the container element is currently visible
    if (!this.containerEl || this.containerEl.classList.contains("hidden") || this.containerEl.offsetParent === null) {
      return;
    }
    if (this.fitAddon && this.term) {
      try {
        this.fitAddon.fit();
        this.term.scrollToBottom();
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          // Control messages travel as BINARY frames
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

  dispose() {
    this._teardownSocket();
    if (this.term) {
      try {
        this.term.dispose();
      } catch (err) {
        console.warn("Error disposing terminal:", err);
      }
      this.term = null;
    }
    if (this.containerEl && this.containerEl.parentNode) {
      this.containerEl.parentNode.removeChild(this.containerEl);
      this.containerEl = null;
    }
  }
}

class TerminalManager {
  constructor() {
    this.sessions = [];
    this.activeSessionId = null;
    this._sessionCounter = 0;
    this._initialized = false;

    this.statusBadge = null;
    this.tabsStrip = null;
    this.btnNewTerminal = null;
    this.xtermContainer = null;
    this.terminalPane = null;
  }

  init() {
    if (this._initialized) return;
    this._initialized = true;

    this.statusBadge = document.getElementById("terminalStatusBadge");
    this.tabsStrip = document.getElementById("terminalTabsStrip");
    this.btnNewTerminal = document.getElementById("btnNewTerminal");
    this.xtermContainer = document.getElementById("xtermContainer");
    this.terminalPane = document.getElementById("terminalPane");

    // Setup context menu on #xtermContainer
    this.setupContextMenu();

    // R2: Ensure clicking anywhere within #terminalPane or #xtermContainer immediately focuses active terminal
    if (this.terminalPane) {
      this.terminalPane.addEventListener("click", (e) => {
        // If clicking on a button, tab, or context menu inside terminalPane, do not intercept
        if (e.target.closest("button") || e.target.closest(".terminal-tab") || e.target.closest("#terminalContextMenu")) {
          return;
        }
        this.focus();
      });
    }

    if (this.xtermContainer) {
      this.xtermContainer.addEventListener("click", () => {
        this.focus();
      });
    }

    // New terminal button [+]
    if (this.btnNewTerminal) {
      this.btnNewTerminal.addEventListener("click", () => {
        this.createSession();
      });
    }

    // Window resize handler
    window.addEventListener("resize", () => {
      this.fit();
    });

    // Create the first default terminal session
    this.createSession("bash 1");
  }

  createSession(title) {
    if (window.appManager && typeof window.appManager.switchPanel === "function") {
      window.appManager.switchPanel("terminal");
    }

    this._sessionCounter++;
    const sessionTitle = title || `bash ${this._sessionCounter}`;
    const id = `session-${Date.now()}-${this._sessionCounter}`;
    const session = new TerminalSession(id, sessionTitle, this);
    this.sessions.push(session);
    session.init();
    this.activateSession(id);
    this.updateTabsUI();
    return session;
  }

  getActiveSession() {
    if (!this.sessions || this.sessions.length === 0) return null;
    return this.sessions.find((s) => s.id === this.activeSessionId) || this.sessions[0];
  }

  activateSession(sessionId) {
    const session = this.sessions.find((s) => s.id === sessionId);
    if (!session) return;
    this.activeSessionId = sessionId;

    // Update tab pills active class & aria-selected
    if (this.tabsStrip) {
      const tabs = this.tabsStrip.querySelectorAll(".terminal-tab");
      tabs.forEach((tab) => {
        const isActive = tab.dataset.sessionId === sessionId;
        tab.classList.toggle("active", isActive);
        tab.setAttribute("aria-selected", isActive ? "true" : "false");
      });
    }

    // Toggle session container visibility
    for (const s of this.sessions) {
      if (s.containerEl) {
        if (s.id === sessionId) {
          s.containerEl.classList.remove("hidden");
        } else {
          s.containerEl.classList.add("hidden");
        }
      }
    }

    // Update status badge for active session
    this.setStatus(session.isConnected ? "connected" : "disconnected");

    // Safe fit & focus active terminal
    session.fit();
    setTimeout(() => session.fit(), 50);
    session.focus();
  }

  closeSession(sessionId) {
    // Sole remaining tab protection: if only 1 terminal session remains, it cannot be closed
    if (this.sessions.length <= 1) {
      return;
    }

    const index = this.sessions.findIndex((s) => s.id === sessionId);
    if (index === -1) return;

    const sessionToClose = this.sessions[index];
    const wasActive = this.activeSessionId === sessionId;

    let nextSessionId = null;
    if (wasActive) {
      if (index + 1 < this.sessions.length) {
        nextSessionId = this.sessions[index + 1].id;
      } else if (index - 1 >= 0) {
        nextSessionId = this.sessions[index - 1].id;
      }
    }

    sessionToClose.dispose();
    this.sessions.splice(index, 1);

    if (wasActive && nextSessionId) {
      this.activateSession(nextSessionId);
    }

    this.updateTabsUI();
  }

  updateTabsUI() {
    if (!this.tabsStrip) {
      this.tabsStrip = document.getElementById("terminalTabsStrip");
    }
    if (!this.tabsStrip) return;

    this.tabsStrip.innerHTML = "";
    const isOnlyOne = this.sessions.length <= 1;

    for (const session of this.sessions) {
      const tabEl = document.createElement("div");
      tabEl.className = `terminal-tab ${session.id === this.activeSessionId ? "active" : ""}`;
      tabEl.dataset.sessionId = session.id;
      tabEl.setAttribute("role", "tab");
      tabEl.setAttribute("aria-selected", session.id === this.activeSessionId ? "true" : "false");

      const titleSpan = document.createElement("span");
      titleSpan.className = "terminal-tab-title";
      titleSpan.textContent = session.title;
      titleSpan.title = session.title;

      const closeBtn = document.createElement("button");
      closeBtn.className = `terminal-tab-close ${isOnlyOne ? "disabled" : ""}`;
      closeBtn.title = "Close Terminal";
      closeBtn.setAttribute("aria-label", `Close ${session.title}`);
      if (isOnlyOne) {
        closeBtn.disabled = true;
      }
      closeBtn.innerHTML = `<i data-lucide="x" class="icon-xs"></i>`;

      tabEl.appendChild(titleSpan);
      tabEl.appendChild(closeBtn);

      tabEl.addEventListener("click", (e) => {
        if (e.target.closest(".terminal-tab-close")) return;
        this.activateSession(session.id);
      });

      closeBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        this.closeSession(session.id);
      });

      this.tabsStrip.appendChild(tabEl);
    }

    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  onSessionStatusChange(session, status) {
    if (session.id === this.activeSessionId) {
      this.setStatus(status);
    }
  }

  setStatus(state) {
    if (!this.statusBadge) {
      this.statusBadge = document.getElementById("terminalStatusBadge");
    }
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

  setupContextMenu() {
    const container = document.getElementById("xtermContainer");
    const contextMenu = document.getElementById("terminalContextMenu");
    const btnCopy = document.getElementById("ctxMenuCopy");
    const btnPaste = document.getElementById("ctxMenuPaste");
    const btnSelectAll = document.getElementById("ctxMenuSelectAll");
    const btnClear = document.getElementById("ctxMenuClear");

    const hideContextMenu = () => {
      if (contextMenu && !contextMenu.classList.contains("hidden")) {
        contextMenu.classList.add("hidden");
      }
    };

    if (contextMenu && container) {
      container.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const activeSession = this.getActiveSession();
        const hasSel = activeSession && activeSession.term ? activeSession.term.hasSelection() : false;
        if (btnCopy) btnCopy.disabled = !hasSel;

        const menuWidth = 185;
        const menuHeight = 150;
        let x = e.clientX;
        let y = e.clientY;
        if (x + menuWidth > window.innerWidth) x = window.innerWidth - menuWidth - 8;
        if (y + menuHeight > window.innerHeight) y = window.innerHeight - menuHeight - 8;

        contextMenu.style.left = `${Math.max(8, x)}px`;
        contextMenu.style.top = `${Math.max(8, y)}px`;
        contextMenu.classList.remove("hidden");
        if (window.lucide && typeof window.lucide.createIcons === "function") {
          window.lucide.createIcons();
        }
      });

      if (btnCopy) {
        btnCopy.addEventListener("click", () => {
          const activeSession = this.getActiveSession();
          if (activeSession && activeSession.term && activeSession.term.hasSelection()) {
            navigator.clipboard.writeText(activeSession.term.getSelection());
          }
          hideContextMenu();
        });
      }

      if (btnPaste) {
        btnPaste.addEventListener("click", () => {
          navigator.clipboard.readText().then((text) => {
            const activeSession = this.getActiveSession();
            if (text && activeSession) {
              activeSession.paste(text);
            }
          }).catch((err) => console.warn("Clipboard read error:", err));
          hideContextMenu();
        });
      }

      if (btnSelectAll) {
        btnSelectAll.addEventListener("click", () => {
          const activeSession = this.getActiveSession();
          if (activeSession && activeSession.term) {
            activeSession.term.selectAll();
          }
          hideContextMenu();
        });
      }

      if (btnClear) {
        btnClear.addEventListener("click", () => {
          this.clear();
          hideContextMenu();
        });
      }

      window.addEventListener("click", (e) => {
        if (!contextMenu.contains(e.target)) {
          hideContextMenu();
        }
      });

      window.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          hideContextMenu();
        }
      });
    }
  }

  // --- Backwards Compatibility Proxies ---

  get term() {
    const session = this.getActiveSession();
    return session ? session.term : null;
  }

  get fitAddon() {
    const session = this.getActiveSession();
    return session ? session.fitAddon : null;
  }

  get ws() {
    const session = this.getActiveSession();
    return session ? session.ws : null;
  }

  fit() {
    const session = this.getActiveSession();
    if (session) {
      session.fit();
    }
  }

  focus() {
    const session = this.getActiveSession();
    if (session) {
      session.focus();
    }
  }

  clear() {
    const session = this.getActiveSession();
    if (session) {
      session.clear();
    }
  }

  paste(text) {
    const session = this.getActiveSession();
    if (session) {
      session.paste(text);
    }
  }

  sendCommand(cmd) {
    if (cmd.trim().startsWith("python") && window.problemsManager) {
      window.problemsManager.clearTerminalProblems();
    }
    const session = this.getActiveSession();
    if (session) {
      session.sendCommand(cmd);
    } else {
      console.warn("No active terminal session to send command.");
    }
  }

  connect() {
    for (const session of this.sessions) {
      session.connect();
    }
  }
}

window.terminalManager = new TerminalManager();
window.terminal = window.terminalManager;
