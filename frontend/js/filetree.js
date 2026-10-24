const AUDIO_EXTS = [".wav", ".mp3", ".ogg", ".flac", ".m4a", ".webm"];

class FileTreeManager {
  constructor() {
    this.container = document.getElementById("fileTreeContainer");
    this.activePath = null;

    // New file modal
    this.modalOverlay = document.getElementById("modalOverlay");
    this.newFileInput = document.getElementById("newFileInput");
    this.btnConfirmModal = document.getElementById("btnConfirmModal");
    this.btnCancelModal = document.getElementById("btnCancelModal");
    this.btnNewFile = document.getElementById("btnNewFile");
    this.btnRefreshFiles = document.getElementById("btnRefreshFiles");
    this.btnExportZip = document.getElementById("btnExportZip");

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
    this.loadTree();
  }

  bindEvents() {
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

  async loadTree() {
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

  _buildTreeElements(items, parentEl, depth) {
    items.forEach((item) => {
      const itemEl = document.createElement("div");
      itemEl.className = "tree-item";
      itemEl.dataset.path = item.path;
      itemEl.style.paddingLeft = `${depth * 14 + 12}px`;
      if (item.path === this.activePath) {
        itemEl.classList.add("active");
      }

      const isAudio = item.type === "file" && AUDIO_EXTS.some((ext) => item.name.toLowerCase().endsWith(ext));
      let iconName = "file-code-2";
      let iconColor = "#58a6ff";

      if (item.type === "directory") {
        iconName = "folder";
        iconColor = "#e3b341";
      } else if (isAudio) {
        iconName = "music";
        iconColor = "#a371f7";
      }

      // Names are escaped: file names are user/API-controlled content.
      itemEl.innerHTML = `
        <div class="tree-item-label">
          <i data-lucide="${iconName}" class="icon-sm" style="color: ${iconColor};"></i>
          <span title="${escapeHtml(item.path)}">${escapeHtml(item.name)}</span>
        </div>
        <div class="tree-item-actions">
          <button class="icon-btn btn-rename-item" title="Rename / Move">
            <i data-lucide="pencil" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-download-item" title="Download ${item.type === "directory" ? "as ZIP" : "file"}">
            <i data-lucide="download" class="icon-xs"></i>
          </button>
          <button class="icon-btn btn-delete-file" title="Delete">
            <i data-lucide="trash" class="icon-xs"></i>
          </button>
        </div>
      `;

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

        if (item.type === "file") {
          if (isAudio) {
            this.openAudioModal(item.path);
          } else {
            this.selectFile(item.path);
          }
        }
      });

      parentEl.appendChild(itemEl);

      if (item.type === "directory" && item.children) {
        this._buildTreeElements(item.children, parentEl, depth + 1);
      }
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

  openNewFileModal() {
    if (this.modalOverlay) {
      this.modalOverlay.classList.remove("hidden");
      this.newFileInput.value = "";
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
