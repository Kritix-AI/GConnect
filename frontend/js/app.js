class AppManager {
  constructor() {
    this.btnRunScript = document.getElementById("btnRunScript");
    this.btnRestartSandbox = document.getElementById("btnRestartSandbox");
    this.splitDivider = document.getElementById("splitDivider");
    this.editorPane = document.getElementById("editorPane");
    this.terminalPane = document.getElementById("terminalPane");
    this.btnClearTerminal = document.getElementById("btnClearTerminal");
  }

  init() {
    if (window.lucide) {
      window.lucide.createIcons();
    }

    window.telemetryManager.init();
    window.terminalManager.init();
    window.fileTreeManager.init();

    window.editorManager.init(() => {
      this.ensureSampleScript();
    });

    this.bindEvents();
    this.setupSplitDivider();
  }

  bindEvents() {
    if (this.btnRunScript) {
      this.btnRunScript.addEventListener("click", () => this.runActiveScript());
    }

    if (this.btnRestartSandbox) {
      this.btnRestartSandbox.addEventListener("click", () => this.restartSandbox());
    }

    if (this.btnClearTerminal) {
      this.btnClearTerminal.addEventListener("click", () => {
        window.terminalManager.clear();
      });
    }

    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
        e.preventDefault();
        this.runActiveScript();
      } else if ((e.ctrlKey || e.metaKey) && e.key === "s") {
        e.preventDefault();
        window.editorManager.saveCurrentFile();
      }
    });
  }

  async ensureSampleScript() {
    try {
      const res = await fetch("/api/files");
      const data = await res.json();
      if (!data.files || data.files.length === 0) {
        const defaultScript = `# Connect - Remote GPU Development Platform
# Specifically configured for PyTorch, CUDA, and Voice Agent workflows

import os
import torch

print("========================================")
print(" Connect GPU Sandbox Ready")
print("========================================")
print(f"PyTorch Version: {torch.__version__}")
print(f"CUDA Available:  {torch.cuda.is_available()}")

if torch.cuda.is_available():
    print(f"Device Name:     {torch.cuda.get_device_name(0)}")
    print(f"Device Count:    {torch.cuda.device_count()}")
    x = torch.randn(1000, 1000, device="cuda")
    print("CUDA Matrix Allocation: SUCCESS")

print(f"Hugging Face Cache: {os.environ.get('HF_HOME')}")
print("========================================")
`;
        await fetch("/api/files/save", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            path: "hello_gpu.py",
            content: defaultScript,
          }),
        });

        await window.fileTreeManager.loadTree();
        window.fileTreeManager.selectFile("hello_gpu.py");
      } else {
        const firstFile = this._findFirstFile(data.files);
        if (firstFile) {
          window.fileTreeManager.selectFile(firstFile.path);
        }
      }
    } catch (err) {
      console.warn("Could not check/create sample script:", err);
    }
  }

  _findFirstFile(items) {
    for (const item of items) {
      if (item.type === "file") return item;
      if (item.type === "directory" && item.children) {
        const nested = this._findFirstFile(item.children);
        if (nested) return nested;
      }
    }
    return null;
  }

  async runActiveScript() {
    const activePath = window.editorManager.activeFilePath;
    if (!activePath) {
      alert("Please select or create a .py file to run.");
      return;
    }

    await window.editorManager.saveCurrentFile();
    console.log(`Executing ${activePath} in container sandbox...`);
    window.terminalManager.sendCommand(`python "${activePath}"`);
    window.terminalManager.focus();
  }

  async restartSandbox() {
    if (!confirm("Restart Docker sandbox container? Active terminal sessions will be reset.")) {
      return;
    }

    const btn = this.btnRestartSandbox;
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = `<i data-lucide="loader-2" class="spin"></i><span>Restarting...</span>`;
    }

    try {
      const res = await fetch("/api/container/restart", { method: "POST" });
      const data = await res.json();
      if (data.status === "success") {
        setTimeout(() => {
          window.terminalManager.connect();
          alert("Docker sandbox restarted successfully.");
        }, 1500);
      } else {
        alert("Failed to restart Docker sandbox.");
      }
    } catch (err) {
      console.error("Restart container error:", err);
      alert("Error contacting host daemon to restart sandbox.");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = `<i data-lucide="refresh-cw"></i><span>Restart Sandbox</span>`;
        if (window.lucide) window.lucide.createIcons();
      }
    }
  }

  setupSplitDivider() {
    let isDragging = false;

    this.splitDivider.addEventListener("mousedown", (e) => {
      isDragging = true;
      document.body.style.cursor = "row-resize";
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!isDragging) return;

      const viewport = document.querySelector(".workspace-viewport");
      const rect = viewport.getBoundingClientRect();
      const offsetY = e.clientY - rect.top;

      const minHeight = 100;
      const maxHeight = rect.height - minHeight;

      if (offsetY >= minHeight && offsetY <= maxHeight) {
        const topPercent = (offsetY / rect.height) * 100;
        const bottomPercent = 100 - topPercent;

        this.editorPane.style.flex = `${topPercent}`;
        this.terminalPane.style.flex = `${bottomPercent}`;

        window.editorManager.resize();
        window.terminalManager.fit();
      }
    });

    window.addEventListener("mouseup", () => {
      if (isDragging) {
        isDragging = false;
        document.body.style.cursor = "default";
        window.editorManager.resize();
        window.terminalManager.fit();
      }
    });
  }
}

document.addEventListener("DOMContentLoaded", () => {
  window.appManager = new AppManager();
  window.appManager.init();
});

