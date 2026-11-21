import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("billing page shows the free plan, credit usage and an unconfigured-billing notice", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Use one credit");
  await expect(page.getByTestId("assistant-message")).toContainText("Mock response", { timeout: 15_000 });

  await page.goto("/billing");
  const current = page.getByTestId("current-plan");
  await expect(current).toContainText("Free plan");
  await expect(current).toContainText("1 of 200 credits used");
  await expect(page.getByTestId("billing-mode-note")).toContainText("not configured");
  await expect(page.getByTestId("plan-pro").getByRole("button", { name: "Upgrade to Pro" })).toBeDisabled();
  await expect(page.getByText("No invoices yet.")).toBeVisible();
});
