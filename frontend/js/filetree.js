const AUDIO_EXTS = [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"];

class FileTreeManager {
  constructor() {
    this.container = document.getElementById("fileTreeContainer");
    this.activePath = null;
    this.expandedFolders = new Set();

    // Sidebar header buttons & elements
    this.btnOpenFolder = document.getElementById("btnOpenFolder");
    this.btnNewFolder = document.getElementById("btnNewFolder");
    this.folderImportInput = document.getElementById("folderImportInput");
    this.workspaceNameLabel = document.getElementById("workspaceNameLabel");
    this.workspaceTitle = document.getElementById("workspaceTitle");

    // New file modal
    this.modalOverlay = document.getElementById("modalOverlay");
    this.newFileInput = document.getElementById("newFileInput");
    this.btnConfirmModal = document.getElementById("btnConfirmModal");
    this.btnCancelModal = document.getElementById("btnCancelModal");
    this.btnNewFile = document.getElementById("btnNewFile");
    this.btnRefreshFiles = document.getElementById("btnRefreshFiles");
    this.btnExportZip = document.getElementById("btnExportZip");
    this.btnImportFiles = document.getElementById("btnImportFiles");
    this.fileImportInput = document.getElementById("fileImportInput");

    // Rename modal
    this.renameOverlay = document.getElementById("renameOverlay");
    this.renameSubtitle = document.getElementById("renameSubtitle");
    this.renameInput = document.getElementById("renameInput");
    this.btnConfirmRename = document.getElementById("btnConfirmRename");
    this.btnCancelRename = document.getElementById("btnCancelRename");

    // Audio preview modal
    this.audioModalOverlay = document.getElementById("audioModalOverlay");
    this.audioFileName = document.getElementById("audioFileName");
    this.audioFilePath = document.getElementById("audioFilePath");
    this.audioPreviewPlayer = document.getElementById("audioPreviewPlayer");
    this.btnDownloadAudio = document.getElementById("btnDownloadAudio");
    this.btnCloseAudioModal = document.getElementById("btnCloseAudioModal");
    this.btnOkAudioModal = document.getElementById("btnOkAudioModal");
  }

  init() {
    this.bindEvents();
    this.updateWorkspaceInfo();
    this.loadTree();
  }

  bindEvents() {
    if (this.btnOpenFolder) {
      this.btnOpenFolder.addEventListener("click", () => this.openFolder());
    }

    if (this.btnNewFolder) {
      this.btnNewFolder.addEventListener("click", () => this.createNewFolder());
    }

    if (this.btnRefreshFiles) {
      this.btnRefreshFiles.addEventListener("click", () => this.loadTree());
    }

    if (this.btnNewFile) {
      this.btnNewFile.addEventListener("click", () => this.openNewFileModal());
    }

    if (this.btnExportZip) {
      this.btnExportZip.addEventListener("click", () => {
        window.location.href = window.authManager.urlWithToken("/api/files/download?path=");
      });
    }

    if (this.btnImportFiles && this.fileImportInput) {
      this.btnImportFiles.addEventListener("click", () => {
        this.fileImportInput.click();
      });

      this.fileImportInput.addEventListener("change", async (e) => {
        const files = e.target.files;
        if (!files || !files.length) return;

        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
          formData.append("files", files[i]);
        }
        formData.append("target_dir", "");

        try {
          const res = await window.authManager.api("/api/files/upload", {
            method: "POST",
            body: formData,
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(`Import failed: ${err.detail || res.statusText}`);
            return;
          }
          await this.loadTree();
        } catch (err) {
          console.error("Upload error:", err);
          alert(`Failed to import files: ${err.message || err}`);
        } finally {
          this.fileImportInput.value = "";
        }
      });
    }

    if (this.folderImportInput) {
      this.folderImportInput.addEventListener("change", async (e) => {
        const files = e.target.files;
        if (!files || !files.length) return;

        const formData = new FormData();
        for (let i = 0; i < files.length; i++) {
          const file = files[i];
          const relPath = file.webkitRelativePath || file.name;
          formData.append("files", file, relPath);
          formData.append("relative_paths", relPath);
        }
        formData.append("target_dir", "");

        try {
          const res = await window.authManager.api("/api/files/upload", {
            method: "POST",
            body: formData,
          });
          if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert(`Folder import failed: ${err.detail || res.statusText}`);
            return;
          }
          await this.loadTree();
        } catch (err) {
          console.error("Folder upload error:", err);
          alert(`Failed to import folder: ${err.message || err}`);
        } finally {
          this.folderImportInput.value = "";
        }
      });
    }

    if (this.btnCancelModal) {
      this.btnCancelModal.addEventListener("click", () => this.closeModal());
    }

    if (this.btnConfirmModal) {
      this.btnConfirmModal.addEventListener("click", () => this.confirmNewFile());
    }

    if (this.newFileInput) {
      this.newFileInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.confirmNewFile();
        if (e.key === "Escape") this.closeModal();
      });
    }

    if (this.btnCancelRename) {
      this.btnCancelRename.addEventListener("click", () => this.closeRenameModal());
    }

    if (this.btnConfirmRename) {
      this.btnConfirmRename.addEventListener("click", () => this.confirmRename());
    }

    if (this.renameInput) {
      this.renameInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") this.confirmRename();
        if (e.key === "Escape") this.closeRenameModal();
      });
    }

    // Audio modal events
    if (this.btnCloseAudioModal) {
      this.btnCloseAudioModal.addEventListener("click", () => this.closeAudioModal());
    }
    if (this.btnOkAudioModal) {
      this.btnOkAudioModal.addEventListener("click", () => this.closeAudioModal());
    }

    window.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (this.audioModalOverlay && !this.audioModalOverlay.classList.contains("hidden")) {
          this.closeAudioModal();
        }
      }
    });
  }

  async openFolder() {
    const isTauri = typeof window !== "undefined" && !!(window.__TAURI__ || window.__TAURI_INTERNALS__);
    if (isTauri) {
      try {
        let selected = null;
        if (window.__TAURI__?.core?.invoke) {
          selected = await window.__TAURI__.core.invoke("open_folder_dialog");
        } else if (window.__TAURI__?.invoke) {
          selected = await window.__TAURI__.invoke("open_folder_dialog");
        }
        if (selected) {
          // Path chosen; backend sidecar restarts with CONNECT_WORKSPACE_DIR
          setTimeout(() => {
            window.location.reload();
          }, 1000);
        }
      } catch (err) {
        console.error("open_folder_dialog error:", err);
        alert(`Failed to open folder dialog: ${err.message || err}`);
      }
    } else {
      // Browser mode: prompt user for directory path or trigger folder import
      const dirPath = prompt(
        "Enter workspace directory path, or leave blank to import a folder from your disk:"
      );
      if (dirPath === null) return;
      const trimmed = dirPath.trim();
      if (!trimmed) {
        if (this.folderImportInput) {
          this.folderImportInput.click();
        }
      } else {
        try {
          const res = await window.authManager.api("/api/files/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: trimmed, is_directory: true }),
          });
          if (res.ok) {
            this.expandedFolders.add(trimmed);
            await this.loadTree();
          } else {
            const err = await res.json().catch(() => ({}));
            alert(`Could not set folder: ${err.detail || res.statusText}`);
          }
        } catch (e) {
          console.error("Browser mode open folder error:", e);
          alert(`Could not open folder: ${e.message || e}`);
        }
      }
    }
  }

  async createNewFolder(parentDir = "") {
    const defaultVal = parentDir ? `${parentDir}/` : "models/nlp";
    const folderName = prompt("Enter folder name (e.g. models/nlp):", defaultVal);
    if (!folderName) return;
    const trimmed = folderName.trim();
    if (!trimmed) return;

    try {
      const res = await window.authManager.api("/api/files/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: trimmed,
          is_directory: true,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Failed to create folder.");
        return;
      }

      // Track created folder in expandedFolders
      const segments = trimmed.split("/").filter(Boolean);
      let cur = "";
      for (const seg of segments) {
        cur = cur ? `${cur}/${seg}` : seg;
        this.expandedFolders.add(cur);
      }

      await this.loadTree();
    } catch (err) {
      console.error("Create folder error:", err);
      alert(`Failed to create folder: ${err.message || err}`);
    }
  }

  async updateWorkspaceInfo() {
    const label = document.getElementById("workspaceNameLabel");
    const titleEl = document.getElementById("workspaceTitle");
    if (!label) return;

    try {
      const res = await window.authManager.api("/api/files/workspace-info");
      if (res.ok) {
        const data = await res.json();
        const wsName = data.workspace_name;
        if (wsName) {
          label.textContent = `WORKSPACE: ${wsName}`;
        } else {
          label.textContent = "WORKSPACE";
        }
        if (titleEl && data.workspace_path) {
          titleEl.title = `Current workspace: ${data.workspace_path}`;
        }
      }
    } catch (err) {
      console.warn("Could not fetch workspace-info:", err);
    }
  }

  async loadTree() {
    this.updateWorkspaceInfo();
    try {
      const res = await window.authManager.api("/api/files");
      if (!res.ok) throw new Error("Failed to load workspace files");
      const data = await res.json();
      this.renderTree(data.files);
    } catch (err) {
      console.error("File tree load error:", err);
      if (this.container) {
        this.container.innerHTML = `<div class="empty-state" style="padding: 12px; color: var(--accent-red);">Error loading files</div>`;
      }
    }
  }

  renderTree(files) {
    if (!this.container) return;
    this.container.innerHTML = "";

    if (!files || files.length === 0) {
      this.container.innerHTML = `
        <div style="padding: 16px; font-size: 12px; color: var(--text-muted); text-align: center;">
          Workspace is empty.<br>Click the <strong>+</strong> icon above to create your first .py script!
        </div>
      `;
      return;
    }

    const listEl = document.createElement("div");
    this._buildTreeElements(files, listEl, 0);
    this.container.appendChild(listEl);

    if (window.lucide) {
      window.lucide.createIcons();
    }
  }

  toggleFolder(path, rowEl = null, childrenEl = null) {
    if (!rowEl && this.container) {
      rowEl = this.container.querySelector(`.tree-folder-row[data-path="${path.replace(/"/g, '\\"')}"]`);
    }
    if (!childrenEl && this.container) {
      childrenEl = this.container.querySelector(`.tree-children[data-folder-path="${path.replace(/"/g, '\\"')}"]`);
    }

    const isExpanded = this.expandedFolders.has(path);
    if (isExpanded) {
      this.expandedFolders.delete(path);
      if (childrenEl) childrenEl.classList.add("hidden");
      if (rowEl) {
        const chevron = rowEl.querySelector(".folder-chevron");
        if (chevron) chevron.classList.remove("open");
        const folderIcon = rowEl.querySelector(".folder-icon");
        if (folderIcon) {
          folderIcon.setAttribute("data-lucide", "folder");
          if (window.lucide) window.lucide.createIcons();
        }
      }
    } else {
      this.expandedFolders.add(path);
      if (childrenEl) childrenEl.classList.remove("hidden");
      if (rowEl) {
        const chevron = rowEl.querySelector(".folder-chevron");
        if (chevron) chevron.classList.add("open");
        const folderIcon = rowEl.querySelector(".folder-icon");
        if (folderIcon) {
          folderIcon.setAttribute("data-lucide", "folder-open");
          if (window.lucide) window.lucide.createIcons();
        }
      }
    }
  }

  _buildTreeElements(items, parentEl, depth) {
    items.forEach((item) => {
      const isDir = item.type === "directory";
      const isExpanded = isDir && this.expandedFolders.has(item.path);
      const isAudio = !isDir && AUDIO_EXTS.some((ext) => item.name.toLowerCase().endsWith(ext));

      let iconName = "file-code-2";
      let iconColor = "#58a6ff";
      if (isDir) {
        iconName = isExpanded ? "folder-open" : "folder";
        iconColor = "#e3b341";
      } else if (isAudio) {
        iconName = "music";
        iconColor = "#a371f7";
      }

      const itemEl = document.createElement("div");
      itemEl.className = `tree-item ${isDir ? "tree-folder-row" : "tree-file-row"}`;
      itemEl.dataset.path = item.path;
      itemEl.dataset.type = item.type;
      itemEl.style.paddingLeft = `${depth * 14 + 10}px`;
      if (item.path === this.activePath) {
        itemEl.classList.add("active");
      }

      const chevronHtml = isDir
        ? `<i data-lucide="chevron-right" class="folder-chevron${isExpanded ? " open" : ""}"></i>`
        : `<span class="chevron-spacer"></span>`;

      const actionsHtml = isDir
        ? `
          <button class="icon-btn btn-new-file-folder" title="New File in Folder">
            <i data-lucide="file-plus" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-rename-item" title="Rename / Move">
            <i data-lucide="pencil" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-download-item" title="Download as ZIP">
            <i data-lucide="download" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-delete-file" title="Delete Folder">
            <i data-lucide="trash" class="icon-xs"></i>
          </button>
        `
        : `
          <button class="icon-btn btn-rename-item" title="Rename / Move">
            <i data-lucide="pencil" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-download-item" title="Download file">
            <i data-lucide="download" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-delete-file" title="Delete">
            <i data-lucide="trash" class="icon-xs"></i>
          </button>
        `;

      itemEl.innerHTML = `
        <div class="tree-item-label">
          ${chevronHtml}
          <i data-lucide="${iconName}" class="icon-sm ${isDir ? "folder-icon" : ""}" style="color: ${iconColor};"></i>
          <span class="tree-item-name" title="${escapeHtml(item.path)}">${escapeHtml(item.name)}</span>
        </div>
        <div class="tree-item-actions">
          ${actionsHtml}
        </div>
      `;

      parentEl.appendChild(itemEl);

      let childrenEl = null;
      if (isDir) {
        childrenEl = document.createElement("div");
        childrenEl.className = `tree-children${isExpanded ? "" : " hidden"}`;
        childrenEl.dataset.folderPath = item.path;

        if (item.children && item.children.length > 0) {
          this._buildTreeElements(item.children, childrenEl, depth + 1);
        } else {
          const emptyEl = document.createElement("div");
          emptyEl.className = "tree-empty-folder";
          emptyEl.style.paddingLeft = `${(depth + 1) * 14 + 10}px`;
          emptyEl.textContent = "(empty)";
          childrenEl.appendChild(emptyEl);
        }

        parentEl.appendChild(childrenEl);
      }

      itemEl.addEventListener("click", (e) => {
        if (e.target.closest(".btn-download-item")) {
          e.stopPropagation();
          window.location.href = window.authManager.urlWithToken(
            `/api/files/download?path=${encodeURIComponent(item.path)}`
          );
          return;
        }

        if (e.target.closest(".btn-delete-file")) {
          e.stopPropagation();
          this.deleteItem(item.path);
          return;
        }

        if (e.target.closest(".btn-rename-item")) {
          e.stopPropagation();
          this.openRenameModal(item.path);
          return;
        }

        if (e.target.closest(".btn-new-file-folder")) {
          e.stopPropagation();
          this.openNewFileModal(item.path);
          return;
        }

        if (isDir) {
          e.stopPropagation();
          this.toggleFolder(item.path, itemEl, childrenEl);
          return;
        }

        if (item.type === "file") {
          if (isAudio) {
            this.openAudioModal(item.path);
          } else {
            this.selectFile(item.path);
          }
        }
      });
    });
  }

  setActivePath(path) {
    this.activePath = path;
    if (!this.container) return;
    this.container.querySelectorAll(".tree-item").forEach((el) => {
      if (el.dataset.path === path) {
        el.classList.add("active");
      } else {
        el.classList.remove("active");
      }
    });
  }

  /**
   * Loads a file into the editor. Always re-fetches from disk; the editor
   * refreshes non-dirty tabs and guards dirty ones via mtime (409) on save.
   * `options.force` bypasses dirty protection (used after a 409 reload).
   */
  async selectFile(path, options = {}) {
    this.setActivePath(path);
    try {
      const res = await window.authManager.api(`/api/files/content?path=${encodeURIComponent(path)}`);
      if (!res.ok) throw new Error("Could not load file content");
      const data = await res.json();
      if (window.editorManager) {
        const tab = window.editorManager.tabs.find((t) => t.path === path);
        if (tab && tab.isDirty && !options.force) {
          // Keep the user's unsaved edits; save-time 409 check protects them.
          window.editorManager.switchToTab(path);
        } else {
          window.editorManager.loadFile(path, data.content, data.mtime);
        }
      }
    } catch (err) {
      console.error("Load file content error:", err);
    }
  }

  openAudioModal(path) {
    if (!this.audioModalOverlay) return;
    const filename = path.split("/").pop();

    if (this.audioFileName) this.audioFileName.textContent = filename;
    if (this.audioFilePath) this.audioFilePath.textContent = path;

    const rawUrl = window.authManager.urlWithToken(`/api/files/raw?path=${encodeURIComponent(path)}`);
    const downloadUrl = window.authManager.urlWithToken(`/api/files/download?path=${encodeURIComponent(path)}`);

    if (this.audioPreviewPlayer) {
      this.audioPreviewPlayer.src = rawUrl;
      this.audioPreviewPlayer.play().catch((e) => {
        console.log("Autoplay prevented or pending user gesture:", e);
      });
    }

    if (this.btnDownloadAudio) {
      this.btnDownloadAudio.href = downloadUrl;
      this.btnDownloadAudio.setAttribute("download", filename);
    }

    this.audioModalOverlay.classList.remove("hidden");
    if (window.lucide) window.lucide.createIcons();
  }

  closeAudioModal() {
    if (!this.audioModalOverlay) return;
    if (this.audioPreviewPlayer) {
      this.audioPreviewPlayer.pause();
      this.audioPreviewPlayer.removeAttribute("src");
      this.audioPreviewPlayer.load();
    }
    this.audioModalOverlay.classList.add("hidden");
  }

  openNewFileModal(parentDir = "") {
    if (this.modalOverlay) {
      this.modalOverlay.classList.remove("hidden");
      this.newFileInput.value = parentDir ? `${parentDir}/` : "";
      this.newFileInput.focus();
    }
  }

  closeModal() {
    if (this.modalOverlay) {
      this.modalOverlay.classList.add("hidden");
    }
  }

  async confirmNewFile() {
    const filename = this.newFileInput.value.trim();
    if (!filename) return;

    try {
      const res = await window.authManager.api("/api/files/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: filename,
          is_directory: filename.endsWith("/"),
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Failed to create file.");
        return;
      }

      // Auto-expand parent folders of the created file
      const slashIdx = filename.lastIndexOf("/");
      if (slashIdx !== -1) {
        const parentPath = filename.substring(0, slashIdx);
        const parts = parentPath.split("/").filter(Boolean);
        let cur = "";
        for (const p of parts) {
          cur = cur ? `${cur}/${p}` : p;
          this.expandedFolders.add(cur);
        }
      }

      this.closeModal();
      await this.loadTree();

      if (!filename.endsWith("/")) {
        this.selectFile(filename);
      }
    } catch (err) {
      console.error("Create item error:", err);
    }
  }

  openRenameModal(path) {
    if (!this.renameOverlay) return;
    this._renameTarget = path;
    if (this.renameSubtitle) {
      this.renameSubtitle.textContent = `New path for '${path}':`;
    }
    this.renameInput.value = path;
    this.renameOverlay.classList.remove("hidden");
    this.renameInput.focus();
  }

  closeRenameModal() {
    if (this.renameOverlay) {
      this.renameOverlay.classList.add("hidden");
    }
    this._renameTarget = null;
  }

  async confirmRename() {
    const oldPath = this._renameTarget;
    const newPath = this.renameInput.value.trim();
    if (!oldPath || !newPath || newPath === oldPath) {
      this.closeRenameModal();
      return;
    }

    try {
      const res = await window.authManager.api("/api/files/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || "Failed to rename item.");
        return;
      }

      // Preserve expanded state for renamed folder and its subpaths
      if (this.expandedFolders.has(oldPath)) {
        this.expandedFolders.delete(oldPath);
        this.expandedFolders.add(newPath);
      }
      for (const p of Array.from(this.expandedFolders)) {
        if (p.startsWith(`${oldPath}/`)) {
          this.expandedFolders.delete(p);
          this.expandedFolders.add(newPath + p.slice(oldPath.length));
        }
      }

      this.closeRenameModal();
      await this.loadTree();

      if (window.editorManager) {
        window.editorManager.renameTab(oldPath, newPath);
      }
      if (this.activePath === oldPath) {
        this.setActivePath(newPath);
      }
    } catch (err) {
      console.error("Rename item error:", err);
      alert("Failed to rename item.");
    }
  }

  async deleteItem(path) {
    if (!confirm(`Are you sure you want to delete '${path}'?`)) {
      return;
    }

    try {
      const res = await window.authManager.api("/api/files/delete", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path }),
      });

      if (!res.ok) throw new Error("Failed to delete");

      // Clean up expandedFolders set
      this.expandedFolders.delete(path);
      for (const p of Array.from(this.expandedFolders)) {
        if (p.startsWith(`${path}/`)) {
          this.expandedFolders.delete(p);
        }
      }

      // If open in the editor, close it without a second confirmation
      // (deletion was already confirmed above).
      if (window.editorManager) {
        window.editorManager.closeTab(path, null, { force: true });
      }

      await this.loadTree();
    } catch (err) {
      console.error("Delete error:", err);
      alert("Failed to delete item.");
    }
  }
}

window.fileTreeManager = new FileTreeManager();
