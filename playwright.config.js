// Playwright end-to-end tests for the GConnect web IDE.
//
// Boots a disposable daemon (isolated workspace + fixed token, no container)
// on port 8900 for the duration of the run, then drives the real UI in
// Chromium. The port exists ONLY while `npm run test:e2e` is running.
//
//   npm run test:e2e           # headless
//   npm run test:e2e:headed    # watch the browser

const { defineConfig } = require("@playwright/test");
const os = require("os");
const path = require("path");

const PORT = 8900;
const TOKEN = "e2e-test-token-0123456789abcdef";
// Fresh workspace per run (the daemon creates it empty), outside the repo
// and OneDrive. Playwright starts the webServer BEFORE globalSetup, so a
// shared wiped dir is impossible - the running daemon holds file handles.
const RUN_ID = Date.now().toString(36);
const WORKSPACE = path.join(os.tmpdir(), `gconnect-e2e-ws-${RUN_ID}`);
process.env.PLAYWRIGHT_E2E_WORKSPACE = WORKSPACE;

// venv python layout differs between Windows and Linux/macOS (CI).
const PYTHON = process.platform === "win32"
  ? ".venv\\Scripts\\python.exe"
  : ".venv/bin/python";

module.exports = defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.js",
  timeout: 30_000,
  workers: 1, // one shared daemon + workspace: keep tests sequential
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `${PYTHON} -m uvicorn backend.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: {
      CONNECT_PORT: String(PORT),
      CONNECT_HOST: "127.0.0.1",
      CONNECT_AUTH_TOKEN: TOKEN,
      CONNECT_WORKSPACE_DIR: WORKSPACE,
      CONNECT_RUNTIME_DIR: path.join(WORKSPACE, ".gconnect"),
      CONNECT_LOG_DIR: path.join(WORKSPACE, ".gconnect", "logs"),
      CONNECT_AUTO_START_CONTAINER: "false",
    },
  },
});
