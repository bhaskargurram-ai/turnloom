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
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(url);
  const button = (name) => page.getByRole("button", { name, exact: true });
  const composer = page.getByRole("textbox", { name: "Message to agent" });
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
  await page.setViewportSize({ width: 420, height: 820 });
  await button("agent").click();
  await composer.fill("Start another turn");
  await button("Send message").click();
  await button("Approve").waitFor();
  await button("Stop turn").click();
  await page.getByText("Turn stopped by operator.").waitFor();
  assert.equal(await button("Approve").count(), 0);
  await page.getByRole("checkbox", { name: "VerifyDoc via Unwind" }).check();
  await composer.fill("Verify receipt");
  await button("Send message").click();
  await button("Approve").click({timeout:110_000});
  await page.getByText("Receipt verified.").waitFor();
  page.once("dialog", (dialog) => dialog.accept());
  await button("Close Studio").click();
  await page.getByText("Studio closed", { exact: true }).waitFor();
  assert.deepEqual(errors, []);
  console.log("Passed desktop and mobile Studio smoke tests");
} finally {
  await browser?.close();
  lines.close();
  proc.kill();
}
