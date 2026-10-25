const { test, expect } = require("@playwright/test");
const { TOKEN } = require("./helpers");

test.describe("authentication overlay (browser mode)", () => {
  test("unauthenticated visit shows the token overlay", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Authentication Required" })).toBeVisible();
  });

  test("wrong token keeps prompting", async ({ page }) => {
    await page.goto("/");
    const input = page.getByRole("textbox", { name: "Access token" });
    await input.fill("definitely-not-the-token");
    await page.getByRole("button", { name: "Connect", exact: true }).click();

    // The retried request 401s and the overlay must come back.
    await expect(page.locator("#authOverlay")).toBeVisible({ timeout: 10_000 });
  });

  test("correct token unlocks the IDE", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("textbox", { name: "Access token" }).fill(TOKEN);
    await page.getByRole("button", { name: "Connect", exact: true }).click();
    await expect(page.locator("#authOverlay")).toBeHidden();

    // A fresh load with the stored token must skip the overlay entirely
    // and show the workspace (sample script auto-created on first run).
    await page.reload();
    await expect(page.locator("#authOverlay.hidden")).toBeAttached();
    await expect(page.locator("#fileTreeContainer .tree-item").first()).toBeVisible({
      timeout: 15_000,
    });
  });
});
