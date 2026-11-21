import { expect, test } from "@playwright/test";
import { registerViaUI, sendMessage } from "./helpers";

test("image generation in demo mode shows a labeled placeholder", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/images");
  await expect(page.getByTestId("images-demo-note")).toBeVisible();
  await page.getByLabel("Describe the image").fill("A watercolor lighthouse at dusk");
  await page.getByRole("button", { name: "Generate" }).click();
  const img = page.getByTestId("image-gallery").getByRole("img", { name: "A watercolor lighthouse at dusk" });
  await expect(img).toBeVisible({ timeout: 15_000 });
  expect(await img.evaluate((el: HTMLImageElement) => el.naturalWidth)).toBe(1024);
  await expect(page.getByTestId("image-gallery")).toContainText("Demo placeholder");
});

test("replies can be read aloud with the browser voice", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Say something");
  const reply = page.getByTestId("assistant-message").last();
  await expect(reply).toContainText("Mock response", { timeout: 15_000 });
  const btn = reply.getByRole("button", { name: "Read aloud" });
  await btn.click();
  await expect(reply.getByRole("button", { name: /Stop reading aloud|Read aloud/ })).toBeVisible();
});
