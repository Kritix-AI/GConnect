class EditorManager {
  constructor() {
    this.editor = null;
    this.activeFilePath = null;
    this.saveTimeout = null;
    this.saveStatusEl = document.getElementById("saveStatus");
    this.activeFileIndicator = document.getElementById("activeFileName");
    this.tabFileName = document.getElementById("tabFileName");
    this.isDirty = false;
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
        if (!this.activeFilePath) return;
        this.markDirty(true);
        this.scheduleAutoSave();
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

  loadFile(path, content) {
    if (this.isDirty) {
      this.saveCurrentFile();
    }

    this.activeFilePath = path;
    const filename = path.split("/").pop();

    if (this.activeFileIndicator) this.activeFileIndicator.textContent = path;
    if (this.tabFileName) this.tabFileName.textContent = filename;

    let language = "python";
    if (path.endsWith(".json")) language = "json";
    else if (path.endsWith(".sh") || path.endsWith(".bash")) language = "shell";
    else if (path.endsWith(".md")) language = "markdown";
    else if (path.endsWith(".txt") || path.endsWith(".env")) language = "plaintext";

    if (this.editor) {
      const model = monaco.editor.createModel(content, language);
      this.editor.setModel(model);
      this.markDirty(false);
    }
  }

  markDirty(dirty) {
    this.isDirty = dirty;
    if (!this.saveStatusEl) return;

    if (dirty) {
      this.saveStatusEl.textContent = "Unsaved";
      this.saveStatusEl.classList.add("unsaved");
    } else {
      this.saveStatusEl.textContent = "Saved";
      this.saveStatusEl.classList.remove("unsaved");
    }
  }

  scheduleAutoSave() {
    clearTimeout(this.saveTimeout);
    this.saveTimeout = setTimeout(() => {
      this.saveCurrentFile();
    }, 1500);
  }

  async saveCurrentFile() {
    if (!this.activeFilePath || !this.editor) return;

    clearTimeout(this.saveTimeout);
    const content = this.editor.getValue();

    if (this.saveStatusEl) {
      this.saveStatusEl.textContent = "Saving...";
    }

    try {
      const res = await fetch("/api/files/save", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: this.activeFilePath,
          content: content,
        }),
      });

      if (res.ok) {
        this.markDirty(false);
      } else {
        if (this.saveStatusEl) {
          this.saveStatusEl.textContent = "Save failed";
          this.saveStatusEl.classList.add("unsaved");
        }
      }
    } catch (err) {
      console.error("Save error:", err);
      if (this.saveStatusEl) {
        this.saveStatusEl.textContent = "Save error";
      }
    }
  }

  getValue() {
    return this.editor ? this.editor.getValue() : "";
  }

  resize() {
    if (this.editor) {
      this.editor.layout();
    }
  }
}

window.editorManager = new EditorManager();

