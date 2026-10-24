class EditorManager {
  constructor() {
    this.editor = null;
    this.tabs = []; // [{ path, name, isDirty, model, viewState }]
    this.activeTabPath = null;
    this.saveTimeouts = new Map();
    this.saveStatusEl = document.getElementById("saveStatus");
    this.activeFileIndicator = document.getElementById("activeFileName");
    this.tabsContainer = document.getElementById("editorTabsContainer");
  }

  init(onReady) {
    if (typeof require === "undefined") {
      console.error("Monaco loader not found.");
      return;
    }

    require.config({
      paths: {
        vs: "https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs",
      },
    });

    require(["vs/editor/editor.main"], () => {
      this.editor = monaco.editor.create(
        document.getElementById("monacoEditorContainer"),
        {
          value: "# Welcome to Connect Remote GPU Platform\n# Select a file from the workspace to begin editing.\n",
          language: "python",
          theme: "vs-dark",
          automaticLayout: true,
          fontSize: 13,
          fontFamily: "'JetBrains Mono', Consolas, monospace",
          minimap: { enabled: true },
          scrollBeyondLastLine: false,
          cursorBlinking: "smooth",
          smoothScrolling: true,
          renderWhitespace: "selection",
          lineNumbers: "on",
        }
      );

      this.editor.onDidChangeModelContent(() => {
        if (!this.activeTabPath) return;
        this.markTabDirty(this.activeTabPath, true);
        this.scheduleAutoSave(this.activeTabPath);
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

  openFile(path, content) {
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
        model,
        viewState: null,
      };
      this.tabs.push(tab);
    }

    this.switchToTab(path);
  }

  loadFile(path, content) {
    this.openFile(path, content);
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

    if (window.fileTreeManager && typeof window.fileTreeManager.setActivePath === "function") {
      window.fileTreeManager.setActivePath(path);
    }
  }

  closeTab(path, event) {
    if (event) {
      event.stopPropagation();
    }

    const tabIndex = this.tabs.findIndex((t) => t.path === path);
    if (tabIndex === -1) return;

    const tab = this.tabs[tabIndex];
    if (tab.isDirty) {
      if (!confirm(`File '${tab.name}' has unsaved changes. Close without saving?`)) {
        return;
      }
    }

    if (tab.model) {
      tab.model.dispose();
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
        const blankModel = monaco.editor.createModel("# Workspace empty\n", "plaintext");
        this.editor.setModel(blankModel);
        this.updateSaveStatus(false);
      }
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
        <span>${tab.name}</span>
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

  async saveFileByPath(path) {
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
      const res = await fetch("/api/files/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: path,
          content: content,
        }),
      });

      if (res.ok) {
        this.markTabDirty(path, false);
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

  resize() {
    if (this.editor) {
      this.editor.layout();
    }
  }
}

window.editorManager = new EditorManager();
