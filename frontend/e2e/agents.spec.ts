import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("create an agent and chat with it, with a starter prompt", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/agents");
  await page.getByRole("button", { name: "New agent" }).click();
  const form = page.getByRole("form", { name: "New agent" });
  await form.getByLabel("Name").fill("Writing coach");
  await form.getByLabel("Instructions").fill("Point out unclear sentences.");
  await form.getByLabel("Model").selectOption("mock:mock-echo");
  await form.getByLabel("Conversation starters (one per line, up to 6)").fill("Review my intro paragraph");
  await form.getByRole("button", { name: "Save agent" }).click();
  await expect(page.getByTestId("agent-list")).toContainText("Writing coach");
  await page.getByTestId("agent-list").getByRole("button", { name: "Chat" }).click();
  await expect(page).toHaveURL(/\/chat\/.+/);
  await expect(page.getByTestId("agent-badge")).toHaveText("Writing coach");
  await page.getByTestId("agent-intro").getByRole("button", { name: "Review my intro paragraph" }).click();
  await page.getByTestId("send").click();
  await expect(page.getByTestId("assistant-message").last()).toContainText("Acting as your agent Writing coach", { timeout: 15_000 });
});

test("memory: remember a message, see it in settings, and forget it", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "I prefer metric units");
  await expect(page.getByTestId("assistant-message").last()).toContainText("Mock response", { timeout: 15_000 });
  await page.getByRole("button", { name: "Remember this" }).click();
  await page.goto("/settings");
  const mem = page.getByTestId("memory-settings");
  await expect(mem.getByTestId("memory-items")).toContainText("I prefer metric units");
  await mem.getByRole("button", { name: /Forget: I prefer metric/ }).click();
  await expect(mem.getByTestId("memory-items")).toContainText("Nothing remembered yet.");
});
