import { expect, test } from "@playwright/test";
import { registerViaUI } from "./helpers";

test("deep research runs with the labeled demo search and shows a cited report", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/research");
  await expect(page.getByTestId("research-demo-note")).toBeVisible();
  await page.getByLabel("What should Trio research?").fill("Are heat pumps cheaper to run than gas boilers?");
  await page.getByLabel("Model").selectOption("mock:mock-deep");
  await page.getByRole("button", { name: "Start research" }).click();
  await expect(page.getByTestId("research-report")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId("research-steps")).toContainText("Checked citations");
  await expect(page.getByTestId("research-sources")).toContainText("[1]");
  await expect(page.getByTestId("research-report")).toContainText("Source [1]");
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Markdown" }).click();
  expect((await download).suggestedFilename()).toMatch(/^research-.*\.md$/);
});
