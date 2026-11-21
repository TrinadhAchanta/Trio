import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("generate a document, edit it and download it as Word", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/documents");
  await page.getByLabel("What should the document say?").fill("A short memo about moving to the new office");
  await page.getByLabel("Type").selectOption("memo");
  await page.getByLabel("Model").selectOption("mock:mock-echo");
  await page.getByRole("button", { name: "Generate" }).click();
  const view = page.getByTestId("doc-view");
  await expect(view).toContainText("Mock response", { timeout: 15_000 });
  await view.getByRole("tab", { name: "edit" }).click();
  await view.getByLabel("Document Markdown").fill("# Office move\n\nWe move on **May 1**.");
  await view.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByTestId("doc-list")).toContainText("Office move");
  const download = page.waitForEvent("download");
  await view.getByRole("link", { name: "docx" }).click();
  expect((await download).suggestedFilename()).toBe("Office-move.docx");
});

test("a chat reply can be downloaded as PDF", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Give me a summary");
  const reply = page.getByTestId("assistant-message").last();
  await expect(reply).toContainText("Mock response", { timeout: 15_000 });
  await reply.getByRole("button", { name: "Download as a file" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("menuitem", { name: "PDF" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.pdf$/);
});
