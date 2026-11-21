import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("mobile layout: drawer navigation and chat work on a phone", async ({ page }) => {
  await registerViaUI(page);
  // Sidebar hidden by default on phones; open it from the menu button.
  const list = page.getByTestId("conversation-list").filter({ visible: true });
  await expect(list).toHaveCount(0);
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(list).toHaveCount(1);
  await page.getByRole("button", { name: "Close menu" }).first().click();

  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Hi from my phone");
  await expect(page.getByTestId("assistant-message")).toContainText("Mock response", { timeout: 15_000 });

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
