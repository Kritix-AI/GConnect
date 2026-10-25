// Shared helpers for the E2E specs.

const TOKEN = "e2e-test-token-0123456789abcdef";

/** Seeds sessionStorage so the auth overlay never appears, then loads the IDE. */
async function openIde(page) {
  await page.addInitScript(
    (token) => sessionStorage.setItem("gconnect_token", token),
    TOKEN
  );
  await page.goto("/");
  await page.locator("#fileTreeContainer .tree-item").first().waitFor();
}

/** Authenticated API request against the test daemon. */
function api(page, path, options = {}) {
  return page.request.fetch(path, {
    ...options,
    headers: { Authorization: `Bearer ${TOKEN}`, ...(options.headers || {}) },
  });
}

/** Hovers a tree item so its hover-only action buttons become visible. */
function treeItem(page, relPath) {
  return page.locator(`.tree-item[data-path="${relPath}"]`);
}

module.exports = { TOKEN, openIde, api, treeItem };
