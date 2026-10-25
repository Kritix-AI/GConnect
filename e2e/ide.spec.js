const { test, expect } = require("@playwright/test");
const { openIde, api, treeItem } = require("./helpers");

// Each test provisions its own files via the API, so specs stay
// independent even though they share the daemon's workspace.

async function putFile(page, path, content = "# e2e\n") {
  await api(page, "/api/files/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    data: { path, content },
  });
  await page.locator("#btnRefreshFiles").click();
  await expect(treeItem(page, path)).toBeVisible();
}

test.describe("web IDE functionality", () => {
  test.beforeEach(async ({ page }) => {
    await openIde(page);
  });

  test("telemetry pill reflects real hardware (GPU -> GPU view, no GPU -> CPU/RAM)", async ({ page }) => {
    // Ground truth from the daemon itself, so the test is portable between
    // GPU-less laptops and GPU workstations.
    const snap = await (await api(page, "/api/telemetry/snapshot")).json();
    await page.waitForTimeout(1500); // first WS push after connect

    if (snap.is_mock && !snap.degraded) {
      // No NVIDIA GPU: the pill must show CPU + RAM, never fake GPU numbers.
      await expect(page.locator("#vramMetricLabel")).toHaveText("RAM");
      await expect(page.locator("#loadMetricLabel")).toHaveText("CPU");
      await expect(page.locator("#tempMetric")).toBeHidden();
      await expect(page.locator("#gpuSimBadge")).toBeVisible();
      const ram = await page.locator("#vramValue").textContent();
      expect(ram).toMatch(/\d+\.\d \/ \d+\.\d GB/);
    } else {
      await expect(page.locator("#vramMetricLabel")).toHaveText("VRAM");
      await expect(page.locator("#loadMetricLabel")).toHaveText("UTILIZATION");
      await expect(page.locator("#tempMetric")).toBeVisible();
    }
  });

  test("create a file from the sidebar and open it in the editor", async ({ page }) => {
    await page.locator("#btnNewFile").click();
    await page.locator("#newFileInput").fill("e2e_ui_created.py");
    await page.getByRole("button", { name: "Create", exact: true }).click();

    await expect(treeItem(page, "e2e_ui_created.py")).toBeVisible();
    await expect(page.locator("#activeFileName")).toContainText("e2e_ui_created.py");
  });

  test("edits autosave and persist", async ({ page }) => {
    await putFile(page, "e2e_autosave.py", "# start\n");
    await treeItem(page, "e2e_autosave.py").click();
    await page.locator("#monacoEditorContainer").click();
    await page.keyboard.type("# e2e-autosave-marker");

    // 1.5s debounce + write; verify from the API, not from the editor.
    await page.waitForTimeout(3000);
    const data = await (await api(page, "/api/files/content?path=e2e_autosave.py")).json();
    expect(data.content).toContain("e2e-autosave-marker");
  });

  test("rename via the tree updates tree and tab", async ({ page }) => {
    await putFile(page, "e2e_before_rename.py");
    // Open the file first: the tab (and header indicator) only follows a
    // rename when the file is actually open in the editor.
    await treeItem(page, "e2e_before_rename.py").click();
    await expect(page.locator("#activeFileName")).toContainText("e2e_before_rename.py");

    const item = treeItem(page, "e2e_before_rename.py");
    await item.hover();
    await item.locator(".btn-rename-item").click();

    await page.locator("#renameInput").fill("e2e_after_rename.py");
    await page.getByRole("button", { name: "Rename", exact: true }).click();

    await expect(treeItem(page, "e2e_after_rename.py")).toBeVisible();
    await expect(treeItem(page, "e2e_before_rename.py")).toHaveCount(0);
    await expect(page.locator("#activeFileName")).toContainText("e2e_after_rename.py");
  });

  test("delete via the tree removes the file", async ({ page }) => {
    page.on("dialog", (d) => d.accept()); // confirm() must be accepted

    await putFile(page, "e2e_deleteme.py");
    const item = treeItem(page, "e2e_deleteme.py");
    await item.hover();
    await item.locator(".btn-delete-file").click();

    await expect(treeItem(page, "e2e_deleteme.py")).toHaveCount(0);
  });

  test("audio files open the preview modal and Escape closes it", async ({ page }) => {
    await putFile(page, "e2e_tone.wav", "RIFF-fake-wave-data");
    await treeItem(page, "e2e_tone.wav").click();

    await expect(page.locator("#audioModalOverlay")).toBeVisible();
    await expect(page.locator("#audioPreviewPlayer")).toHaveAttribute(
      "src",
      /\/api\/files\/raw\?path=e2e_tone\.wav/
    );

    await page.keyboard.press("Escape");
    await expect(page.locator("#audioModalOverlay")).toBeHidden();
  });

  test("cluster panel lists this PC with live status", async ({ page }) => {
    await page.locator("#btnCluster").click();
    await expect(page.locator("#clusterOverlay")).toBeVisible();
    await expect(page.locator(".cluster-local-tag")).toHaveText("THIS PC");
    await expect(page.locator("#clusterNodeCount")).toHaveText("1/1 online");
  });

  test("run script warns instead of dying silently when the sandbox is unavailable", async ({ page }) => {
    // The E2E daemon runs without Docker, so the sandbox is deterministically
    // unavailable: the yellow banner must appear, and Run must pop a warning
    // instead of sending the command into a dead terminal.
    await expect(page.locator("#dockerBanner")).toBeVisible({ timeout: 15_000 });
    await expect(page.locator("#dockerBannerText")).toContainText("Docker is not running");

    await expect(page.locator("#activeFileName")).not.toContainText("No file selected");
    await page.locator("#btnRunScript").click();
    await expect(page.locator("#dockerWarnOverlay")).toBeVisible();
    await page.locator("#btnOkDockerWarn").click();
    await expect(page.locator("#dockerWarnOverlay")).toBeHidden();
  });

  test("sidebar is resizable by dragging (VS Code-style)", async ({ page }) => {
    const sidebar = page.locator("#sidebar");
    const before = await sidebar.boundingBox();
    const resizer = await page.locator("#sidebarResizer").boundingBox();

    await page.mouse.move(resizer.x + resizer.width / 2, resizer.y + 200);
    await page.mouse.down();
    await page.mouse.move(before.x + 380, resizer.y + 200, { steps: 5 });
    await page.mouse.up();

    const after = await sidebar.boundingBox();
    expect(after.width).toBeGreaterThan(before.width + 80);
    expect(after.width).toBeLessThanOrEqual(520); // clamped

    // Double-click resets to the default width.
    await page.locator("#sidebarResizer").dblclick();
    expect((await sidebar.boundingBox()).width).toBe(250);
  });

  test("problems tab parses tracebacks and opens the file at the line", async ({ page }) => {
    await putFile(page, "e2e_buggy.py", "line1 = 1\nline2 = 2\nraise ValueError('boom')\n");

    // Feed a realistic traceback through the same entry point the terminal
    // stream uses (the E2E daemon has no sandbox to actually raise one).
    await page.evaluate(() => {
      window.problemsManager.ingest(
        'Traceback (most recent call last):\r\n' +
        '  File "e2e_buggy.py", line 3, in <module>\r\n' +
        '    raise ValueError(\'boom\')\r\n' +
        'ValueError: boom\r\n'
      );
    });

    await expect(page.locator("#problemCount")).toHaveText("1");
    await page.locator("#tabProblems").click();
    await expect(page.locator("#problemsContainer")).toBeVisible();
    await expect(page.locator(".problem-item")).toContainText("ValueError: boom");
    await expect(page.locator(".problem-meta")).toContainText("e2e_buggy.py:3");

    // Click-through: opens the file and jumps to the reported line.
    await page.locator(".problem-item").click();
    await expect(page.locator("#activeFileName")).toContainText("e2e_buggy.py");
    const cursor = await page.evaluate(() => {
      const ed = window.editorManager;
      return { line: ed.editor.getPosition().lineNumber, view: ed.tabs[0]?.path };
    });
    expect(cursor.line).toBe(3);

    // A new python run clears the list (VS Code build-rerun behavior).
    await page.evaluate(() => window.terminalManager.sendCommand("python e2e_buggy.py"));
    await expect(page.locator("#problemCount")).toBeHidden();
  });

  test("problems tab lists editor lint errors with click-through (VS Code-like)", async ({ page }) => {
    await putFile(page, "e2e_syntax_err.py", "def broken(:\n    pass\n");

    // Opening the file triggers a lint run; the badge and squiggle follow.
    await treeItem(page, "e2e_syntax_err.py").click();
    await expect(page.locator("#problemCount")).toHaveText("1", { timeout: 10_000 });

    // Red squiggle marker exists on the model (editor-side half).
    const marker = await page.evaluate(() => {
      const tab = window.editorManager.tabs.find((t) => t.path === "e2e_syntax_err.py");
      return monaco.editor.getModelMarkers({ owner: "gconnect-lint", resource: tab.model.uri })[0];
    });
    expect(marker).toBeTruthy();
    expect(marker.severity).toBe(8); // monaco.MarkerSeverity.Error

    await page.locator("#tabProblems").click();
    const item = page.locator(".problem-item").first();
    await expect(item).toContainText("invalid syntax");
    await expect(item).toContainText("e2e_syntax_err.py:1");

    // Click-through jumps to the reported line.
    await item.click();
    await expect(page.locator("#activeFileName")).toContainText("e2e_syntax_err.py");

    // Fixing the code clears the entry after the lint debounce.
    await page.locator("#monacoEditorContainer").click();
    await page.evaluate(() => {
      const tab = window.editorManager.tabs.find((t) => t.path === "e2e_syntax_err.py");
      tab.model.setValue("def fixed():\n    return 1\n");
    });
    await expect(page.locator("#problemCount")).toBeHidden({ timeout: 10_000 });
  });

  test("connection switcher is browser-mode hidden (desktop-only feature)", async ({ page }) => {
    await expect(page.locator("#btnConnection")).toBeHidden();
  });
});
