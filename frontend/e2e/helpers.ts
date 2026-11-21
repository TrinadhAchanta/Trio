import { expect, type Page } from "@playwright/test";

export function uniqueEmail(prefix = "e2e") {
  return `${prefix}-${Date.now()}-${Math.floor(Math.random() * 1e6)}@example.com`;
}

export async function registerViaUI(page: Page, email = uniqueEmail()) {
  await page.goto("/register");
  await page.getByRole("textbox", { name: "Name" }).fill("Test User");
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await passwordField(page).fill("e2e-password-123");
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/chat$/);
  return email;
}

export async function sendMessage(page: Page, text: string) {
  await page.getByTestId("composer").fill(text);
  await page.getByTestId("send").click();
}

// Next.js keeps recently visited routes mounted but hidden, so target the visible field only.
export function passwordField(page: Page) {
  return page.locator("input[type=password]:visible");
}

export function alertText(page: Page, text: string | RegExp) {
  return page.getByRole("alert").filter({ hasText: text });
}
