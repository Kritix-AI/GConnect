/**
 * ProblemsManager: VS Code-style PROBLEMS panel with two sources:
 *
 * 1. Lint diagnostics from /api/lint (syntax + pyflakes) for files open in
 *    the editor - set via setLintDiagnostics() on every lint run.
 * 2. Runtime errors parsed from the terminal stream (Python tracebacks) -
 *    ingested via ingest(). A new `python` run clears only these.
 *
 * Clicking an entry opens the file and jumps to the reported line.
 */
class ProblemsManager {
  constructor() {
    this.container = document.getElementById("problemsContainer");
    this.countBadge = document.getElementById("problemCount");
    this.problems = []; // [{source: "lint"|"terminal", severity?, type, message, file, line, count}]
    this._pending = ""; // partial line carried across output chunks
  }

  /** Replaces the lint diagnostics of one file (editor content changed). */
  setLintDiagnostics(path, diagnostics) {
    const keep = this.problems.filter((p) => !(p.source === "lint" && p.file === path));
    const converted = (diagnostics || []).map((d) => ({
      source: "lint",
      severity: d.severity === "error" ? "error" : "warning",
      type: d.severity === "error" ? "Error" : "Warning",
      message: d.message,
      file: path,
      line: d.line || null,
      count: 1,
    }));
    this.problems = keep.concat(converted);
    this.render();
  }

  /** The file was closed/deleted: drop its entries. */
  dropFile(path) {
    const before = this.problems.length;
    this.problems = this.problems.filter((p) => p.file !== path);
    if (this.problems.length !== before) this.render();
  }

  /** The file was renamed: move its entries to the new path. */
  renameFile(oldPath, newPath) {
    let changed = false;
    for (const p of this.problems) {
      if (p.file === oldPath) {
        p.file = newPath;
        changed = true;
      }
    }
    if (changed) this.render();
  }

  /** Feeds raw terminal output (may contain ANSI escapes / \r). */
  ingest(rawText) {
    if (!rawText) return;
    const clean = String(rawText)
      // OSC sequences, CSI sequences, and stray control chars
      .replace(/\x1b\][^\x07]*(\x07|\x1b\\)/g, "")
      .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "")
      .replace(/\x1b[=>]/g, "");

    this._pending += clean;
    if (this._pending.length > 64 * 1024) {
      this._pending = this._pending.slice(-8 * 1024);
    }

    const lines = this._pending.split(/\r?\n/);
    this._pending = lines.pop() || ""; // last item is an unfinished line
    for (const line of lines) {
      this._processLine(line.replace(/\r/g, ""));
    }
  }

  _processLine(line) {
    if (/^Traceback \(most recent call last\):/.test(line)) {
      this._tbFrames = [];
      this._tbActive = true;
      return;
    }
    if (!this._tbActive) return;

    const frame = /^  File "([^"]+)", line (\d+)/.exec(line);
    if (frame) {
      this._tbFrames.push({ file: frame[1], line: parseInt(frame[2], 10) });
      return;
    }

    // Source-context lines are indented; skip them.
    if (/^\s/.test(line) || line === "") return;

    // Final line of a traceback: ErrorType or ErrorType: message.
    // Accept it only inside an active traceback to avoid false positives
    // on source code that merely mentions an Error subclass.
    const err = /^([A-Za-z_][A-Za-z0-9_.]*(?:Error|Exception|Warning|Exit|Interrupt))\b:?\s*(.*)$/.exec(line);
    if (err) {
      const origin = this._tbFrames[this._tbFrames.length - 1] || { file: null, line: null };
      this.addProblem({
        source: "terminal",
        type: err[1],
        message: err[2].trim() || line.trim(),
        file: origin.file,
        line: origin.line,
      });
      this._tbActive = false;
      this._tbFrames = [];
    }
  }

  addProblem(problem) {
    const last = this.problems[this.problems.length - 1];
    if (
      last &&
      last.type === problem.type &&
      last.message === problem.message &&
      last.file === problem.file &&
      last.line === problem.line
    ) {
      last.count = (last.count || 1) + 1; // identical repeat: count occurrences
    } else {
      this.problems.push({ count: 1, severity: "error", ...problem });
    }
    if (this.problems.length > 200) {
      this.problems = this.problems.slice(-200);
    }
    this.render();
  }

  /** A new python run invalidates previous TERMINAL problems (lint stays). */
  clearTerminalProblems() {
    const keep = this.problems.filter((p) => p.source !== "terminal");
    if (keep.length !== this.problems.length) {
      this.problems = keep;
      this._pending = "";
      this._tbActive = false;
      this._tbFrames = [];
      this.render();
    }
  }

  /** Trash button on the PROBLEMS tab: wipe everything. */
  clear() {
    this.problems = [];
    this._pending = "";
    this._tbActive = false;
    this._tbFrames = [];
    this.render();
  }

  render() {
    if (this.countBadge) {
      this.countBadge.textContent = String(this.problems.length);
      this.countBadge.classList.toggle("hidden", this.problems.length === 0);
    }
    if (!this.container) return;

    this.container.innerHTML = "";
    if (!this.problems.length) {
      const empty = document.createElement("div");
      empty.className = "problems-empty";
      empty.textContent = "No problems have been detected in this session.";
      this.container.appendChild(empty);
      return;
    }

    // VS Code-style grouping: one header per file, terminal output last.
    const groups = new Map();
    for (const p of this.problems) {
      const key = p.source === "lint" ? p.file || "unknown file" : "Terminal output";
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(p);
    }
    const keys = [...groups.keys()].sort((a, b) => {
      const aTerm = a === "Terminal output";
      const bTerm = b === "Terminal output";
      if (aTerm !== bTerm) return aTerm ? 1 : -1;
      return a.localeCompare(b);
    });

    for (const key of keys) {
      const entries = groups.get(key);
      const header = document.createElement("div");
      header.className = "problem-file";
      header.innerHTML = `
        <i data-lucide="${key === "Terminal output" ? "terminal" : "file-code-2"}" class="icon-xs"></i>
        <span>${escapeHtml(key)}</span>
        <span class="problem-file-count">${entries.length}</span>
      `;
      this.container.appendChild(header);

      for (const p of entries) {
        const item = document.createElement("div");
        item.className = `problem-item ${p.severity === "warning" ? "warning" : ""}`;
        const isWarn = p.severity === "warning";
        const where = p.file ? `${p.file}${p.line ? `:${p.line}` : ""}` : "unknown location";
        item.innerHTML = `
          <i data-lucide="${isWarn ? "triangle-alert" : "circle-x"}" class="p-icon${isWarn ? " warning" : ""}"></i>
          <span class="problem-msg">${escapeHtml(p.source === "lint" ? p.message : `${p.type}: ${p.message}`)}</span>
          <span class="problem-meta">${escapeHtml(where)}${p.count > 1 ? ` (${p.count}×)` : ""}</span>
        `;
        item.title = "Click to open the file at this line";
        item.addEventListener("click", () => this.openProblem(p));
        this.container.appendChild(item);
      }
    }

    if (window.lucide) window.lucide.createIcons();
  }

  openProblem(p) {
    if (!p.file || !window.fileTreeManager || !window.editorManager) return;
    window.fileTreeManager
      .selectFile(p.file)
      .then(() => window.editorManager.revealLine(p.line))
      .catch(() => {
        // File not in the workspace (deleted/renamed since the run).
        if (this.container) {
          const note = document.createElement("div");
          note.className = "problems-note";
          note.textContent = `'${p.file}' could not be opened - it is not in the workspace anymore.`;
          this.container.prepend(note);
          setTimeout(() => note.remove(), 4000);
        }
      });
  }
}

window.problemsManager = new ProblemsManager();
