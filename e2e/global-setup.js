// The daemon's workspace is unique per run (see playwright.config.js) and
// starts empty, so nothing needs wiping up front. This hook only prunes
// workspaces from PREVIOUS runs that no live process holds anymore.

const fs = require("fs");
const os = require("os");
const path = require("path");

module.exports = async () => {
  const tmp = os.tmpdir();
  const current = process.env.PLAYWRIGHT_E2E_WORKSPACE || "";
  let entries = [];
  try {
    entries = fs.readdirSync(tmp);
  } catch {
    return;
  }
  for (const name of entries) {
    if (!name.startsWith("gconnect-e2e-ws-")) continue;
    const dir = path.join(tmp, name);
    if (dir === current) continue;
    try {
      fs.rmSync(dir, { recursive: true, force: true });
    } catch {
      // Held by a dying process from a previous run - the OS will clean it.
    }
  }
};
