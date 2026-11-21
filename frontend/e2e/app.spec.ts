import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import { alertText, passwordField, registerViaUI, sendMessage, uniqueEmail } from "./helpers";

test("unauthenticated users are sent to sign in", async ({ page }) => {
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/login$/);
});

test("register, chat with streaming, and history persists after reload", async ({ page }) => {
  await registerViaUI(page);
  await expect(page.getByTestId("demo-banner")).toBeVisible();
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Hello Trio, are you there?");

  const live = page.getByTestId("streaming-reply");
  await expect(live).toBeVisible();
  const reply = page.getByTestId("assistant-message").first();
  await expect(reply).toContainText("Mock response from `mock-echo`".replace(/`/g, ""), { timeout: 15_000 });
  await expect(reply).toContainText("Mock, not a real AI");
  await expect(page).toHaveURL(/\/chat\/[0-9a-f-]{36}$/);

  // Markdown table and code highlighting rendered
  await expect(reply.locator("table")).toBeVisible();
  await expect(reply.locator("pre code")).toContainText("syntax highlighting works");

  await sendMessage(page, "Second message");
  await expect(page.getByTestId("assistant-message")).toHaveCount(2, { timeout: 15_000 });
  await expect(page.getByTestId("assistant-message").nth(1)).toContainText("2 earlier message(s)");

  await page.reload();
  await expect(page.getByTestId("user-message")).toHaveCount(2);
  await expect(page.getByTestId("conversation-list")).toContainText("Hello Trio, are you there?");
});

test("regenerate creates a second version you can switch between", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Regenerate me");
  await expect(page.getByTestId("assistant-message")).toContainText("mock-echo", { timeout: 15_000 });
  await page.getByTestId("model-picker").selectOption("mock:mock-deep");
  await page.getByRole("button", { name: "Regenerate reply" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("Mock deep response", { timeout: 15_000 });
  await expect(page.getByTestId("assistant-message")).toContainText("2/2");
  await page.getByRole("button", { name: "Previous version" }).click();
  await expect(page.getByTestId("assistant-message")).toContainText("1/2");
  await expect(page.getByTestId("assistant-message")).not.toContainText("Mock deep response");
});

test("provider failure shows a clear error and is recorded in usage", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Please fail [[mock-error]]");
  await expect(page.getByTestId("assistant-message")).toContainText("This reply failed", { timeout: 15_000 });
  await page.goto("/usage");
  // the chosen model and its automatic fallback both failed, so two failed attempts are listed
  await expect(page.getByText("provider_unavailable").first()).toBeVisible();
});

test("artifact renders in a sandboxed preview, runs, and can be edited", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Build me an html counter app");
  const panel = page.getByTestId("artifact-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByRole("heading", { name: "Counter app" })).toBeVisible();

  const frame = page.frameLocator('[data-testid="artifact-frame"]');
  await expect(frame.locator("#count")).toHaveText("0");
  await frame.getByRole("button", { name: "Add one" }).click();
  await frame.getByRole("button", { name: "Add one" }).click();
  await expect(frame.locator("#count")).toHaveText("2");

  // The sandbox has an opaque origin, so artifact code cannot read the app's cookies or storage.
  const sandbox = await page.getByTestId("artifact-frame").getAttribute("sandbox");
  expect(sandbox).not.toContain("allow-same-origin");

  await panel.getByRole("button", { name: "Code" }).click();
  const editor = panel.getByLabel("Artifact source code");
  await editor.fill('<h1 id="t">Edited by hand</h1><script>undefinedFunction()</script>');
  await expect(frame.locator("#t")).toHaveText("Edited by hand");
  await expect(panel.getByRole("status")).toContainText("problem", { timeout: 5_000 });
  await panel.getByRole("button", { name: "Save version" }).click();
  await expect(panel.getByRole("combobox")).toHaveValue("2");
});

test("file upload attaches valid files and rejects invalid ones", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await page.getByTestId("file-input").setInputFiles({ name: "notes.csv", mimeType: "text/csv", buffer: Buffer.from("a,b\n1,2") });
  await expect(page.getByTestId("pending-attachments")).toContainText("notes.csv");
  await sendMessage(page, "Summarize the attached file");
  await expect(page.getByTestId("user-message")).toContainText("1 attachment", { timeout: 15_000 });

  await page.getByTestId("file-input").setInputFiles({ name: "evil.png", mimeType: "image/png", buffer: Buffer.from("<script>") });
  await expect(alertText(page, "does not match")).toBeVisible();
});

test("model comparison streams side by side and combines answers", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/compare");
  await page.getByTestId("compare-model-0").selectOption("mock:mock-echo");
  await page.getByTestId("compare-model-1").selectOption("mock:mock-deep");
  await page.getByTestId("compare-prompt").fill("What is the capital of France?");
  await page.getByTestId("compare-run").click();
  const cols = page.getByTestId("compare-column");
  await expect(cols).toHaveCount(2);
  await expect(cols.nth(0)).toContainText("Est. cost", { timeout: 15_000 });
  await expect(cols.nth(1)).toContainText("Est. cost", { timeout: 15_000 });
  await expect(page.getByText("fastest")).toHaveCount(1);
  await page.getByTestId("synthesize").click();
  await expect(page.getByTestId("synthesis")).toContainText("Mock response", { timeout: 15_000 });
});

test("sign out and sign back in", async ({ page }) => {
  const email = await registerViaUI(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await passwordField(page).fill("wrong-password-0");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(alertText(page, "Incorrect email or password")).toBeVisible();
  await passwordField(page).fill("e2e-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/chat$/);
});

test("command palette opens with Ctrl+K and navigates", async ({ page }) => {
  await registerViaUI(page);
  await expect(page.getByTestId("composer")).toBeVisible();
  await page.keyboard.press("Control+k");
  await expect(page.getByRole("dialog", { name: "Command palette" })).toBeVisible();
  await page.keyboard.type("compare");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/compare$/);
});

test("dark theme can be selected and persists", async ({ page }) => {
  await registerViaUI(page);
  await page.goto("/settings");
  await page.getByRole("radio", { name: "dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await page.reload();
  await expect(page.locator("html")).toHaveClass(/dark/);
});

for (const [name, path] of [
  ["sign-in page", "/login"],
  ["chat page", "/chat"],
  ["compare page", "/compare"],
  ["agents page", "/agents"],
  ["research page", "/research"],
  ["knowledge page", "/knowledge"],
  ["documents page", "/documents"],
  ["images page", "/images"],
  ["settings page", "/settings"],
] as const) {
  test(`no serious accessibility violations on the ${name}`, async ({ page }) => {
    if (path !== "/login") await registerViaUI(page, uniqueEmail("a11y"));
    await page.goto(path);
    await page.waitForLoadState("networkidle");
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze();
    const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
    expect(serious.map((v) => `${v.id}: ${v.help} (${v.nodes.length})`)).toEqual([]);
  });
}

test("when the chosen model fails, the next model answers and the reply says so", async ({ page }) => {
  await registerViaUI(page);
  await page.getByTestId("model-picker").selectOption("mock:mock-echo");
  await sendMessage(page, "Hello [[mock-error:mock-echo]]");
  const reply = page.getByTestId("assistant-message").last();
  await expect(reply).toContainText(/mock deep response/i, { timeout: 15_000 });
  await expect(reply.getByTestId("fallback-note")).toContainText("Mock Deep (demo) answered instead");
  await page.goto("/settings");
  await expect(page.getByTestId("routing-settings").getByTestId("allow-fallback")).toBeChecked();
});
