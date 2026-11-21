import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("create a knowledge base, add text, search it and chat with cited sources", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/knowledge");
  await page.getByLabel("New knowledge base name").fill("Team handbook");
  await page.getByRole("button", { name: "Create knowledge base" }).click();
  await expect(page.getByRole("heading", { name: "Team handbook" })).toBeVisible();

  await page.getByText("Paste text instead").click();
  await page.getByLabel("Title").fill("Vacation policy");
  await page.getByLabel("Text", { exact: true }).fill("Full-time employees receive 26 days of paid vacation per year.");
  await page.getByRole("button", { name: "Add text" }).click();
  await expect(page.getByTestId("kb-documents")).toContainText("Vacation policy");

  await page.getByLabel("Search query").fill("how many vacation days");
  await page.getByRole("button", { name: "Search" }).click();
  await expect(page.getByTestId("kb-results")).toContainText("26 days");

  await page.goto("/chat");
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await page.getByTestId("kb-picker").click();
  await page.getByRole("dialog", { name: "Choose knowledge bases" }).getByLabel("Team handbook").check();
  await page.getByTestId("kb-picker").click();
  await sendMessage(page, "How many vacation days do I get?");
  const reply = page.getByTestId("assistant-message").last();
  await expect(reply).toContainText("Source [1] (Vacation policy)", { timeout: 15_000 });
  await reply.getByText(/Sources from your knowledge base/).click();
  await expect(reply.getByTestId("source-list")).toContainText("26 days of paid vacation");
  await expect(page.getByTestId("kb-searched")).toContainText("1 passage found");
});
