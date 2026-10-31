class EditorManager {
  constructor() {
    this.editor = null;
    this.tabs = []; // [{ path, name, isDirty, mtime, model, viewState }]
    this.activeTabPath = null;
    this.saveTimeouts = new Map();
    this.saveStatusEl = document.getElementById("saveStatus");
    this.activeFileIndicator = document.getElementById("activeFileName");
    this.tabsContainer = document.getElementById("editorTabsContainer");
    this._blankModel = null;
    this._lintTimer = null;
    this._lintSeq = 0;
  }

  init(onReady) {
    if (typeof require === "undefined") {
      console.error("Monaco loader not found.");
      return;
    }

    require.config({
      paths: {
        vs: "vendor/monaco/vs",
      },
    });

    require(["vs/editor/editor.main"], () => {
      this.editor = monaco.editor.create(
        document.getElementById("monacoEditorContainer"),
        {
          value: "# Welcome to GConnect Remote GPU Platform\n# Select a file from the workspace to begin editing.\n",
          language: "python",
          theme: "vs-dark",
          automaticLayout: true,
          fontSize: 13,
          fontFamily: "'JetBrains Mono', Consolas, monospace",
          minimap: { enabled: true },
          scrollBeyondLastLine: false,
          cursorBlinking: "blink",
          cursorSmoothCaretAnimation: "on",
          smoothScrolling: true,
          renderWhitespace: "selection",
          lineNumbers: "on",
        }
      );

      this.editor.onDidChangeModelContent(() => {
        if (!this.activeTabPath) return;
        this.markTabDirty(this.activeTabPath, true);
        this.scheduleAutoSave(this.activeTabPath);
        this.scheduleLint();
      });

      this.editor.onDidChangeCursorPosition((e) => {
        const cursorEl = document.getElementById("statusBarCursor");
        if (cursorEl && e.position) {
          cursorEl.textContent = `Ln ${e.position.lineNumber}, Col ${e.position.column}`;
        }
      });

      this.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
        this.saveCurrentFile();
      });

      this.editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => {
        if (window.appManager) {
          window.appManager.runActiveScript();
        }
      });

      if (onReady) onReady();
    });
  }

  get activeFilePath() {
    return this.activeTabPath;
  }

  detectLanguage(path) {
    if (path.endsWith(".json")) return "json";
    if (path.endsWith(".sh") || path.endsWith(".bash")) return "shell";
    if (path.endsWith(".md")) return "markdown";
    if (path.endsWith(".txt") || path.endsWith(".env")) return "plaintext";
    if (path.endsWith(".html")) return "html";
    if (path.endsWith(".css")) return "css";
    if (path.endsWith(".js")) return "javascript";
    return "python";
  }

  /**
   * Opens (or refreshes) a file tab. If the tab already exists and is NOT
   * dirty, the content is refreshed from disk so external changes (terminal
   * runs, other clients) are never silently clobbered by autosave.
   */
  openFile(path, content, mtime) {
    let tab = this.tabs.find((t) => t.path === path);

    if (!tab) {
      const filename = path.split("/").pop();
      const language = this.detectLanguage(path);
      const uri = monaco.Uri.parse(`inmemory://workspace/${encodeURIComponent(path)}`);
      let model = monaco.editor.getModel(uri);
      if (!model) {
        model = monaco.editor.createModel(content, language, uri);
      } else {
        model.setValue(content);
      }

      tab = {
        path,
        name: filename,
        isDirty: false,
        mtime: mtime || null,
        model,
        viewState: null,
      };
      this.tabs.push(tab);
    } else if (!tab.isDirty) {
      const current = tab.model.getValue();
      if (current !== content) {
        tab.model.setValue(content);
      }
      tab.mtime = mtime || tab.mtime;
    }

    this.switchToTab(path);
  }

  loadFile(path, content, mtime) {
    this.openFile(path, content, mtime);
  }

  switchToTab(path) {
    if (!this.editor) return;

    if (this.activeTabPath) {
      const currentTab = this.tabs.find((t) => t.path === this.activeTabPath);
      if (currentTab) {
        currentTab.viewState = this.editor.saveViewState();
      }
    }

    const tab = this.tabs.find((t) => t.path === path);
    if (!tab) return;

    this.activeTabPath = path;
    this.editor.setModel(tab.model);
    if (tab.viewState) {
      this.editor.restoreViewState(tab.viewState);
    }
    this.editor.focus();

    if (this.activeFileIndicator) {
      this.activeFileIndicator.textContent = path;
    }
    this.updateSaveStatus(tab.isDirty);
    this.renderTabs();
    this.scheduleLint(250);

    const langEl = document.getElementById("statusBarLang");
    if (langEl && tab.model) {
      const modeId = tab.model.getLanguageId();
      langEl.textContent = modeId.charAt(0).toUpperCase() + modeId.slice(1);
    }

    if (window.fileTreeManager && typeof window.fileTreeManager.setActivePath === "function") {
      window.fileTreeManager.setActivePath(path);
    }
  }

  /**
   * Closes a tab. `options.force` skips the unsaved-changes confirmation
   * (used by the delete flow, which has already confirmed).
   */
  closeTab(path, event, options = {}) {
    if (event) {
      event.stopPropagation();
    }

    const tabIndex = this.tabs.findIndex((t) => t.path === path);
    if (tabIndex === -1) return;

    const tab = this.tabs[tabIndex];
    if (tab.isDirty && !options.force) {
      if (!confirm(`File '${tab.name}' has unsaved changes. Close without saving?`)) {
        return;
      }
    }

    if (tab.model) {
      tab.model.dispose();
    }
    window.problemsManager?.dropFile?.(path);
    if (this.saveTimeouts.has(path)) {
      clearTimeout(this.saveTimeouts.get(path));
      this.saveTimeouts.delete(path);
    }

    this.tabs.splice(tabIndex, 1);

    if (this.activeTabPath === path) {
      if (this.tabs.length > 0) {
        const nextIndex = Math.min(tabIndex, this.tabs.length - 1);
        this.switchToTab(this.tabs[nextIndex].path);
      } else {
        this.activeTabPath = null;
        if (this.activeFileIndicator) {
          this.activeFileIndicator.textContent = "No file selected";
        }
        if (!this._blankModel || this._blankModel.isDisposed()) {
          this._blankModel = monaco.editor.createModel("# Workspace empty\n", "plaintext");
        }
        this.editor.setModel(this._blankModel);
        this.updateSaveStatus(false);
      }
    }

    this.renderTabs();
  }

  /** Renames a tab in place after a /api/files/rename. */
  renameTab(oldPath, newPath) {
    const tab = this.tabs.find((t) => t.path === oldPath);
    if (!tab) return;
    tab.path = newPath;
    tab.name = newPath.split("/").pop();
    window.problemsManager?.renameFile?.(oldPath, newPath);
    if (this.activeTabPath === oldPath) {
      this.activeTabPath = newPath;
      if (this.activeFileIndicator) this.activeFileIndicator.textContent = newPath;
    }
    this.renderTabs();
  }

  renderTabs() {
    if (!this.tabsContainer) return;
    this.tabsContainer.innerHTML = "";

    this.tabs.forEach((tab) => {
      const tabEl = document.createElement("div");
      tabEl.className = `editor-tab ${tab.path === this.activeTabPath ? "active" : ""}`;

      const isPy = tab.name.endsWith(".py");
      const iconName = isPy ? "file-code-2" : "file-text";

      tabEl.innerHTML = `
        <i data-lucide="${iconName}" class="tab-icon"></i>
        <span>${escapeHtml(tab.name)}</span>
        ${tab.isDirty ? '<span class="tab-dirty-dot" title="Unsaved changes"></span>' : ''}
        <button class="tab-close-btn" title="Close Tab">
          <i data-lucide="x" class="icon-xs"></i>
        </button>
      `;

      tabEl.addEventListener("click", () => {
        this.switchToTab(tab.path);
      });

      const closeBtn = tabEl.querySelector(".tab-close-btn");
      closeBtn.addEventListener("click", (e) => {
        this.closeTab(tab.path, e);
      });

      this.tabsContainer.appendChild(tabEl);
    });

    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  markTabDirty(path, dirty) {
    const tab = this.tabs.find((t) => t.path === path);
    if (!tab) return;

    tab.isDirty = dirty;
    if (this.activeTabPath === path) {
      this.updateSaveStatus(dirty);
    }
    this.renderTabs();
  }

  updateSaveStatus(isDirty) {
    if (!this.saveStatusEl) return;
    if (isDirty) {
      this.saveStatusEl.textContent = "Unsaved";
      this.saveStatusEl.classList.add("unsaved");
    } else {
      this.saveStatusEl.textContent = "Saved";
      this.saveStatusEl.classList.remove("unsaved");
    }
  }

  scheduleAutoSave(path) {
    if (this.saveTimeouts.has(path)) {
      clearTimeout(this.saveTimeouts.get(path));
    }

    const timer = setTimeout(() => {
      this.saveFileByPath(path);
    }, 1500);

    this.saveTimeouts.set(path, timer);
  }

  async saveCurrentFile() {
    if (!this.activeTabPath) return;
    await this.saveFileByPath(this.activeTabPath);
  }

  async saveFileByPath(path, force = false) {
    const tab = this.tabs.find((t) => t.path === path);
    if (!tab || !tab.model) return;

    if (this.saveTimeouts.has(path)) {
      clearTimeout(this.saveTimeouts.get(path));
      this.saveTimeouts.delete(path);
    }

    const content = tab.model.getValue();

    if (this.activeTabPath === path && this.saveStatusEl) {
      this.saveStatusEl.textContent = "Saving...";
    }

    try {
      const res = await window.authManager.api("/api/files/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: path,
          content: content,
          // Optimistic concurrency: refuse silent overwrites of external edits.
          expected_mtime: force ? null : tab.mtime,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        tab.mtime = data.mtime ?? tab.mtime;
        this.markTabDirty(path, false);
      } else if (res.status === 409) {
        // File changed on disk since it was loaded.
        const overwrite = confirm(
          `'${tab.name}' was changed on disk after you opened it.\n\n` +
          "OK = overwrite the disk version with your editor content\n" +
          "Cancel = keep the disk version and reload it into the editor"
        );
        if (overwrite) {
          await this.saveFileByPath(path, true);
        } else if (window.fileTreeManager) {
          await window.fileTreeManager.selectFile(path, { force: true });
        }
      } else {
        if (this.activeTabPath === path && this.saveStatusEl) {
          this.saveStatusEl.textContent = "Save failed";
          this.saveStatusEl.classList.add("unsaved");
        }
      }
    } catch (err) {
      console.error("Save error:", err);
      if (this.activeTabPath === path && this.saveStatusEl) {
        this.saveStatusEl.textContent = "Save error";
      }
    }
  }

  getValue() {
    if (!this.activeTabPath) return "";
    const tab = this.tabs.find((t) => t.path === this.activeTabPath);
    return tab && tab.model ? tab.model.getValue() : "";
  }

  // ------------------------------------------------------------------
  // Linting: /api/lint drives Monaco squiggles + the PROBLEMS list
  // ------------------------------------------------------------------

  scheduleLint(delay = 700) {
    if (this._lintTimer) {
      clearTimeout(this._lintTimer);
    }
    this._lintTimer = setTimeout(() => this.lintActiveFile(), delay);
  }

  async lintActiveFile() {
    const path = this.activeTabPath;
    if (!path || !window.authManager) return;

    const tab = this.tabs.find((t) => t.path === path);
    if (!tab || !tab.model) return;

    if (!path.toLowerCase().endsWith(".py")) {
      // Only Python is linted; clear any stale markers/diagnostics.
      if (!tab.model.isDisposed()) {
        monaco.editor.setModelMarkers(tab.model, "gconnect-lint", []);
      }
      window.problemsManager?.setLintDiagnostics(path, []);
      return;
    }

    const content = tab.model.getValue();
    const seq = ++this._lintSeq;
    try {
      const res = await window.authManager.api("/api/lint", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path, content }),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (seq !== this._lintSeq) return; // a newer lint superseded this one

      const diagnostics = data.diagnostics || [];
      const model = tab.model;
      if (model.isDisposed()) return;

      const markers = diagnostics.map((d) => {
        const line = Math.max(1, d.line || 1);
        const startCol = Math.max(1, d.col || 1);
        const boundedLine = Math.min(line, model.getLineCount());
        const endCol = Math.max(startCol + 1, model.getLineMaxColumn(boundedLine));
        return {
          startLineNumber: line,
          startColumn: startCol,
          endLineNumber: line,
          endColumn: endCol,
          message: d.message,
          severity:
            d.severity === "error"
              ? monaco.MarkerSeverity.Error
              : monaco.MarkerSeverity.Warning,
          source: d.source || "lint",
        };
      });
      monaco.editor.setModelMarkers(model, "gconnect-lint", markers);
      window.problemsManager?.setLintDiagnostics(path, diagnostics);
    } catch (err) {
      console.warn("Lint request failed:", err);
    }
  }

  /** Jumps to a 1-based line (PROBLEMS panel click-through). */
  revealLine(line) {
    if (!this.editor || !line || !Number.isFinite(line)) return;
    const lineNumber = Math.max(1, Math.floor(line));
    this.editor.revealLineInCenter(lineNumber);
    this.editor.setPosition({ lineNumber, column: 1 });
    this.editor.focus();
  }

  resize() {
    if (this.editor) {
      this.editor.layout();
    }
  }
}

window.editorManager = new EditorManager();
