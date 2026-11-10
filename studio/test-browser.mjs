import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const proc = spawn(
  process.env.PYTHON || "python",
  ["-u", "../tests/studio_browser_fixture.py"],
  {
    stdio: ["ignore", "pipe", "inherit"],
  },
);
const lines = createInterface({ input: proc.stdout });
let browser;
try {
  const [url] = await Promise.race([
    once(lines, "line"),
    once(proc, "exit").then(() => {
      throw Error("Fixture exited before startup");
    }),
  ]);
  browser = await chromium.launch({
    channel: process.env.BROWSER_CHANNEL || undefined,
  });
  const page = await browser.newPage({
    viewport: { width: 1360, height: 860 },
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  const button = (name) => page.getByRole("button", { name, exact: true });
  const composer = page.getByRole("textbox", { name: "Message to agent" });

  // Editor: edit, save, reload.
  await page.locator(".cm-content").getByText("Studio workspace").waitFor();
  await page.locator(".cm-content").click();
  await page.keyboard.press("End");
  await page.keyboard.type(" saved");
  await button("Save").click();
  await page.reload();
  await page
    .locator(".cm-content")
    .getByText("Studio workspace saved")
    .waitFor();

  // A bounded turn with approvals, grouped tool activity and archive recall.
  await composer.fill("Review the workspace");
  await button("Send message").click();
  for (let i = 1; i <= 4; i++) {
    await page.getByText("Awaiting approval", { exact: true }).waitFor();
    await button(i === 4 ? "Reject" : "Approve").click();
    await page
      .locator(".activity > summary")
      .filter({ hasText: i + " tool action" })
      .waitFor();
  }
  await page.getByText("Reviewed available evidence.").waitFor();
  assert.equal(await page.locator(".activity-item").count(), 0);
  await page.locator(".activity > summary").click();
  await page.locator(".activity-item").nth(3).waitFor({ state: "attached" });
  assert.equal(await page.locator(".activity-item").count(), 4);
  await page.getByTitle("Read archived result").first().click();
  await page.getByRole("dialog", { name: "Archived result" }).waitFor();
  await button("Close archived result").click();

  // Cost view: the meter saw five model calls and the controls persist.
  await button("Cost").click();
  await page.getByRole("heading", { name: "Cost and tokens" }).waitFor();
  await page.getByText("5 calls").waitFor();
  await page
    .getByRole("combobox", { name: "Reasoning effort" })
    .selectOption("low");
  await page.reload();
  await button("Cost").click();
  await page.waitForFunction(
    () =>
      document.querySelector('select[aria-label="Reasoning effort"]')
        ?.value === "low",
  );

  // Agents: search the vendored catalog and switch persona.
  await button("Agents").click();
  await page
    .getByRole("textbox", { name: "Search personas" })
    .fill("code reviewer");
  await page.locator(".persona").filter({ hasText: "Code Reviewer" }).click();
  await button("Use Code Reviewer").click();
  await page
    .getByRole("button", { name: "Agent persona" })
    .getByText("Code Reviewer")
    .waitFor();

  // Command palette navigation.
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder("Go to a view").fill("undo ledger");
  await page.keyboard.press("Enter");
  await page.getByRole("heading", { name: "Undo ledger" }).waitFor();
  await page.keyboard.press("Control+k");
  await page.getByPlaceholder("Go to a view").fill("go changes");
  await page.keyboard.press("Enter");
  await page.getByText("not a Git repository").waitFor();

  // Documents: VerifyDoc through Unwind with no model call.
  await button("Documents").click();
  await button("Pasted text").click();
  await page
    .getByRole("textbox", { name: "Document text" })
    .fill("Merchant: Corner Cafe\nDate: 2026-11-02\nTotal: 42.50\nTax: 3.10");
  await button("Receipt").click();
  await button("Verify document").click();
  await page.locator(".source mark").first().waitFor({ timeout: 110_000 });
  assert.equal(await page.locator(".table.extracted tbody tr").count(), 4);
  await button("Tools").click();
  await page.locator(".server").getByText("Running").waitFor();

  // Policy: create the starter Provio policy, test a call, and let the policy decide.
  await button("Policy").click();
  await button("Create starter policy").click();
  await page.getByText("packs: [floor, secrets-guard]").waitFor();
  await page.getByRole("textbox", { name: "Call to test" }).fill("rm -rf ~");
  await button("Test").click();
  await page.locator(".verdict-card.deny").waitFor();
  await page.getByRole("radio", { name: /Let the policy decide/ }).click();
  await page.getByText("Provio gate on").waitFor();
  await page.getByRole("radio", { name: /Confirm every call/ }).click();
  await page.waitForFunction(
    () => document.querySelector(".modes label.on b")?.textContent === "Confirm every call",
  );

  // Narrow window: one pane at a time, stop wakes a pending approval.
  await page.setViewportSize({ width: 420, height: 820 });
  await button("Chat").click();
  await composer.fill("Start another turn");
  await button("Send message").click();
  await button("Approve").waitFor();
  await button("Stop turn").click();
  await page.getByText("Turn stopped by operator.").waitFor();
  assert.equal(await button("Approve").count(), 0);
  // The box reflects the saved setting, so it turns on once the server confirms.
  await page.getByRole("checkbox", { name: "VerifyDoc via Unwind" }).click();
  await page.getByRole("checkbox", { name: "VerifyDoc via Unwind", checked: true }).waitFor();
  await composer.fill("Verify receipt");
  await button("Send message").click();
  await page.getByText("Call this tool?").waitFor({ timeout: 110_000 });
  await page.locator(".risk.R0").waitFor();
  await button("Approve").click();
  await page.getByText("Receipt verified.").waitFor();
  await page
    .getByRole("navigation", { name: "Views" })
    .getByRole("button", { name: "Policy" })
    .click();
  await page.locator(".kind.allow").first().waitFor();
  page.once("dialog", (dialog) => dialog.accept());
  await button("Close Studio").click();
  await page.getByText("Studio closed", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log("Passed desktop and narrow-window Studio smoke tests");
} finally {
  await browser?.close();
  lines.close();
  proc.kill();
}
