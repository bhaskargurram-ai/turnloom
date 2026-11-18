// End-to-end test of Studio in a real browser against the real server and a scripted model.
//
//   PYTHON=<interpreter with turnloom installed> node test-browser.mjs
//
// Optional: BROWSER_CHANNEL=chrome uses an installed Chrome; SHOTS=<folder> saves screenshots of
// each state; DOCS=<folder> also writes the five README screenshots there.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { appendFile, mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const SHOTS = process.env.SHOTS;
const DOCS = process.env.DOCS;
const fixture = spawn(process.env.PYTHON || "python", ["-u", "../tests/studio_browser_fixture.py"], {
  stdio: ["ignore", "pipe", "inherit"],
});
const lines = createInterface({ input: fixture.stdout });
let browser;
let current;
const pageErrors = [];
let stage = "starting";

function step(name) {
  stage = name;
  console.log("  " + name);
}

try {
  const [line] = await Promise.race([
    once(lines, "line"),
    once(fixture, "exit").then(() => {
      throw Error("The fixture exited before it printed its addresses.");
    }),
  ]);
  const info = JSON.parse(line);
  if (SHOTS) await mkdir(SHOTS, { recursive: true });
  browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || undefined });

  // Everything the page logs as an error, and every request that failed, is collected and
  // checked at the end. Requests the test makes fail on purpose are named in `expected`.
  const consoleErrors = [];
  const failures = [];
  const expected = new Set();
  function watch(page) {
    page.on("pageerror", (error) => pageErrors.push(`${stage}: ${error.message}`));
    page.on("console", (message) => {
      if (message.type() === "error" && !/Failed to load resource/.test(message.text())) consoleErrors.push(`${stage}: ${message.text()}`);
    });
    page.on("response", (response) => {
      const action = new URL(response.url()).pathname;
      if (response.status() >= 400 && !expected.has(action)) failures.push(`${stage}: ${response.status()} ${action}`);
    });
  }
  async function shot(page, name, docs = false) {
    if (SHOTS) await page.screenshot({ path: path.join(SHOTS, name + ".png") });
    if (docs && DOCS) await page.screenshot({ path: path.join(DOCS, name + ".png") });
  }
  const origin = new URL(info.url).origin;
  const call = (url, action, body = {}) =>
    fetch(new URL(url).origin + "/api/" + action, {
      method: "POST",
      headers: { Authorization: "Bearer " + new URL(url).hash.slice(1), "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });

  // ---- A tab without a session key ------------------------------------------------------
  step("tokenless tab");
  {
    const context = await browser.newContext();
    const page = await context.newPage();
    watch(page);
    const requests = [];
    page.on("request", (request) => request.url().includes("/api/") && requests.push(request.url()));
    await page.goto(origin + "/");
    await page.getByRole("heading", { name: "This tab has no Studio session" }).waitFor();
    await page.waitForTimeout(1200);
    assert.deepEqual(requests, [], "a tab without a key must not call the server");
    await shot(page, "no-session");
    await context.close();
  }

  // ---- The workbench --------------------------------------------------------------------
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const page = await context.newPage();
  current = page;
  watch(page);
  await page.goto(info.url);
  const button = (name, options = {}) => page.getByRole("button", { name, exact: true, ...options });
  const tab = (name) => page.getByRole("navigation", { name: "Views" }).getByRole("button", { name, exact: true });
  const dialog = (name) => page.getByRole("dialog", { name });
  const composer = page.getByRole("textbox", { name: "Task for the agent" });
  const log = page.getByRole("log", { name: "Agent log" });
  const editor = page.locator(".cm-content");
  const approval = page.getByRole("region", { name: "Approval needed" });
  const settled = () => page.waitForResponse((response) => response.url().endsWith("/api/settings") && response.ok());
  const idle = () => page.locator(".status").getByText("Ready", { exact: true }).waitFor({ timeout: 150_000 });
  // Each task text is used once per session, so waiting for it cannot match an earlier request.
  async function send(task) {
    const earlier = await log.locator(".entry.request").count();
    await composer.fill(task);
    await composer.press("Enter");
    await log.locator(".entry.request").nth(earlier).getByText(task, { exact: true }).waitFor();
  }
  const opened = (name) => page.getByRole("region", { name: "Editor" }).locator(".doc-name", { hasText: name });
  async function openFile(name) {
    await page.getByRole("region", { name: "Files" }).getByRole("button", { name, exact: true }).click();
    await opened(name.split("/").pop()).waitFor();
  }

  step("first load and empty states");
  await editor.getByText("Fixture project").waitFor();
  assert.match(await page.title(), /loomworks/);
  await button("Model connection: No model connected Connect").waitFor();
  await log.getByRole("heading", { name: "Work on loomworks" }).waitFor();
  await page.getByRole("region", { name: "Context Loom" }).getByText("No tool results yet").first().waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight), true, "the shell must fit the viewport");
  await shot(page, "empty-state");

  step("connection dialog");
  await button("Model connection: No model connected Connect").click();
  const connection = dialog("Model connection");
  await connection.waitFor();
  const provider = connection.getByLabel("Provider");
  await provider.locator('option[value="groq"]').waitFor({ state: "attached", timeout: 60_000 });
  assert.ok((await provider.locator("optgroup").count()) >= 2, "providers are grouped");
  await provider.selectOption("groq");
  await connection.getByText("lists its models only with a key").waitFor();
  assert.equal(await connection.getByRole("button", { name: "Connect", exact: true }).isDisabled(), true);
  await connection.getByText("Enter the API key, then load the models.").waitFor();
  await connection.getByText("https://api.groq.com/openai/v1").waitFor();
  await provider.selectOption("deepseek");
  assert.match(await connection.getByLabel("API key").getAttribute("placeholder"), /A key is saved/);
  await provider.selectOption("openrouter");
  await connection.getByRole("button", { name: "Sign in with OpenRouter and connect" }).waitFor();
  if (info.litellm) {
    await provider.selectOption("litellm:anthropic");
    await connection.getByText("through the LiteLLM library").waitFor();
    await connection.locator(".models tbody tr").first().waitFor();
  }
  // A provider whose key cannot be stored: the key is held for this session and the operator is told.
  await provider.selectOption("groq");
  await connection.getByLabel("API key").fill("sk-fixture");
  await connection.getByLabel("API key").press("Enter");
  await connection.getByRole("radio", { name: "fixture-coder-mini" }).check();
  await connection.getByRole("button", { name: "Connect", exact: true }).click();
  await connection.waitFor({ state: "hidden" });
  await page.getByText("the API key is held in memory only").waitFor();
  await button("Dismiss message").click();
  // A local provider needs no key and its models load by themselves, with price and context shown.
  await button("Model connection: fixture-coder-mini Change").click();
  await provider.selectOption("ollama");
  const models = connection.locator(".models tbody tr");
  await models.first().waitFor();
  assert.equal(await models.count(), 4);
  const row = await models.first().innerText();
  assert.match(row, /fixture-coder/);
  assert.match(row, /64K/);
  assert.match(row, /\$0\.5/);
  assert.match(row, /yes/);
  assert.equal(await connection.getByText("Free only").count(), 1);
  await connection.getByRole("radio", { name: "fixture-coder", exact: true }).check();
  await shot(page, "connection-dialog");
  // Escape closes the dialog and focus returns to the control that opened it.
  await page.keyboard.press("Escape");
  await connection.waitFor({ state: "hidden" });
  assert.equal(await page.evaluate(() => document.activeElement.className.includes("model-button")), true);
  await page.keyboard.press("Enter");
  await provider.selectOption("ollama");
  await connection.getByRole("radio", { name: "fixture-coder", exact: true }).check();
  await connection.getByRole("button", { name: "Connect", exact: true }).click();
  await connection.waitFor({ state: "hidden" });
  await button("Model connection: fixture-coder Change").waitFor();

  step("editor keeps its state across a save");
  await openFile("long.py");
  assert.equal(await editor.getAttribute("aria-label"), "Editor, long.py");
  await editor.click();
  await page.keyboard.press("Control+End");
  await page.keyboard.type("# edited");
  await page.getByText("Ln 401, Col 9").waitFor();
  await page.getByRole("status").filter({ hasText: "Unsaved changes" }).waitFor();
  const before = await page.locator(".cm-scroller").evaluate((node) => node.scrollTop);
  assert.ok(before > 1000, "the editor scrolled to the end of the file");
  // Ctrl+S with Caps Lock on reports the key as an upper-case S.
  await page.evaluate(() => window.dispatchEvent(new KeyboardEvent("keydown", { key: "S", ctrlKey: true, bubbles: true, cancelable: true })));
  await page.keyboard.type(" now");
  await page.locator(".doc-state", { hasText: /Saved|Unsaved changes/ }).waitFor();
  await page.waitForFunction(() => document.querySelector(".doc-state").textContent === "Unsaved changes");
  assert.equal(await page.locator(".cm-scroller").evaluate((node) => node.scrollTop), before, "saving must not move the editor");
  assert.equal(await page.evaluate(() => !!document.activeElement.closest(".cm-editor")), true, "saving must not take focus");
  await editor.getByText("# edited now").waitFor();
  await button("Save").click();
  await page.waitForFunction(() => document.querySelector(".doc-state").textContent === "Saved");
  assert.equal(await page.evaluate(() => !!document.activeElement.closest(".cm-editor")), true, "the Save button returns to the editor");
  await page.keyboard.press("Control+z");
  await page.waitForFunction(() => document.querySelector(".doc-state").textContent === "Unsaved changes", null, { timeout: 5000 });
  await page.keyboard.press("Control+y");
  await page.waitForFunction(() => document.querySelector(".doc-state").textContent === "Saved");

  step("save conflict");
  const disk = path.join(info.root, "long.py");
  await page.keyboard.type(" mine");
  await appendFile(disk, "# written by another program\n");
  expected.add("/api/save");
  await button("Save").click();
  const banner = page.getByRole("alert").filter({ hasText: "changed on disk" });
  await banner.waitFor();
  expected.delete("/api/save");
  await banner.getByRole("button", { name: "View diff" }).click();
  await dialog(/Disk and your version/).getByText("written by another program").waitFor();
  await shot(page, "save-conflict");
  await page.keyboard.press("Escape");
  await banner.getByRole("button", { name: "Overwrite with my version" }).click();
  await banner.waitFor({ state: "hidden" });
  await page.waitForFunction(() => document.querySelector(".doc-state").textContent === "Saved");
  // A clean buffer follows the file when it changes on disk.
  await appendFile(disk, "# appended later\n");
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await editor.getByText("# appended later").waitFor();

  step("file tree: create, rename, delete, refresh an open folder");
  const files = page.getByRole("region", { name: "Files" });
  await files.getByRole("button", { name: "notes", exact: true }).click();
  await files.getByRole("button", { name: "n1.md", exact: true }).waitFor();
  assert.equal(await files.getByRole("button", { name: "notes", exact: true }).getAttribute("aria-expanded"), "true");
  await writeFile(path.join(info.root, "notes", "extra.md"), "# Extra\n");
  await files.getByRole("button", { name: "Refresh files" }).click();
  await files.getByRole("button", { name: "extra.md", exact: true }).waitFor();
  await files.getByRole("button", { name: "New file" }).click();
  await files.getByRole("textbox", { name: "New file path" }).fill("scratch/draft.txt");
  await files.getByRole("textbox", { name: "New file path" }).press("Enter");
  await files.getByRole("button", { name: "draft.txt", exact: true }).waitFor();
  await opened("draft.txt").waitFor();
  await files.getByRole("button", { name: "Rename scratch/draft.txt" }).click();
  await files.getByRole("textbox", { name: "Rename draft.txt" }).fill("scratch/final.txt");
  await files.getByRole("textbox", { name: "Rename draft.txt" }).press("Enter");
  await files.getByRole("button", { name: "final.txt", exact: true }).waitFor();
  await opened("final.txt").waitFor();
  await files.getByRole("button", { name: "Delete scratch/final.txt" }).click();
  await dialog("Delete file").getByRole("button", { name: "Delete", exact: true }).click();
  await files.getByRole("button", { name: "final.txt", exact: true }).waitFor({ state: "hidden" });
  await page.getByText("No file open").waitFor();

  step("composer: quick start, mention, and the auto-approve warning");
  await openFile("app.py");
  await log.getByRole("button", { name: "review this workspace" }).click();
  assert.equal(await page.evaluate(() => document.activeElement.getAttribute("aria-label")), "Task for the agent");
  assert.match(await composer.inputValue(), /^Review this workspace/);
  await composer.fill("Look at");
  await button("Mention open file").click();
  assert.equal(await composer.inputValue(), "Look at app.py ");
  await composer.fill("");
  await page.getByLabel("Auto-approve everything").click();
  const warning = dialog("Auto-approve everything");
  await warning.getByText("without showing it to you first").waitFor();
  await warning.getByRole("button", { name: "Cancel" }).click();
  assert.equal(await page.getByLabel("Auto-approve everything").isChecked(), false);
  await Promise.all([settled(), page.getByLabel("Auto-approve edits").click()]);
  assert.equal(await page.getByLabel("Auto-approve edits").isChecked(), true);
  await Promise.all([settled(), page.getByLabel("Auto-approve edits").click()]);

  step("a turn that folds tool results into the Loom");
  await send("Survey the notes");
  await log.getByText("Surveyed six notes.").waitFor({ timeout: 60_000 });
  await idle();
  await log.getByRole("cell", { name: "warping" }).waitFor();
  const loom = page.getByRole("region", { name: "Context Loom" });
  await loom.getByText(/characters folded out of the prompt/).waitFor();
  const ledger = loom.locator("tbody tr");
  assert.equal(await ledger.count(), 8, "one row per tool result");
  assert.ok((await loom.getByRole("button", { name: /^Read original/ }).count()) >= 3, "folded results can be read back");
  assert.ok((await loom.locator(".thread").count()) > 10, "the figure draws threads");
  assert.ok((await loom.locator(".loom-legend li").count()) >= 3, "the legend names each tool family");
  await loom.getByLabel("Folded only").check();
  assert.ok((await ledger.count()) < 8);
  await loom.getByLabel("Folded only").uncheck();
  await shot(page, "loom-folded");
  await loom.getByRole("button", { name: /^Read original, row 3/ }).click();
  const archive = dialog(/Archived result of read_file notes\/n1\.md/);
  await archive.getByText("In warping, each thread is checked").first().waitFor();
  await shot(page, "loom-archive");
  await archive.getByLabel("Show as stored").check();
  await archive.getByText('"total_lines"').waitFor();
  await page.keyboard.press("Escape");
  await archive.waitFor({ state: "hidden" });

  step("streamed reply, a read, an edit shown as a diff, approval");
  await send("Bump the value in app.py");
  await page.locator(".entry.draft").waitFor();
  await approval.waitFor();
  await approval.getByRole("heading", { name: "Edit app.py?" }).waitFor();
  await approval.locator(".dl.del", { hasText: "value = 1" }).waitFor();
  await approval.locator(".dl.add", { hasText: "value = 2" }).waitFor();
  await log.getByText("I will read the file before changing it.").waitFor();
  assert.equal(await editor.getByText("value = 1").count(), 1, "nothing is written before approval");
  await page.locator(".status").getByText("Approval needed").waitFor();
  await shot(page, "approval-diff");
  await shot(page, "studio-preview", true);
  await approval.getByRole("button", { name: "Approve", exact: true }).click();
  await log.getByText("Done.", { exact: false }).waitFor();
  await idle();
  const answer = log.locator(".entry.answer").last();
  assert.equal(await log.locator(".entry.request .plain").last().evaluate((node) => getComputedStyle(node).whiteSpace), "pre-wrap");
  assert.equal(await answer.getByRole("link").count(), 1, "only web addresses become links");
  await answer.getByText("local note").waitFor();
  const link = answer.getByRole("link", { name: "Python docs" });
  assert.equal(await link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(await link.getAttribute("target"), "_blank");
  await answer.getByText("__init__.py").waitFor();
  assert.match(await answer.locator(".receipt").innerText(), /completed/i);
  assert.match(await answer.locator(".receipt").innerText(), /read from cache/);
  assert.match(await answer.locator(".receipt").innerText(), /\$0\.0/);
  await editor.getByText("value = 2").waitFor();
  await log.getByRole("button", { name: /edit_file app\.py \(\+1 −1\)/ }).click();
  await log.locator(".step.open .dl.add", { hasText: "value = 2" }).waitFor();

  step("the turn's edits: review and revert");
  const edits = page.getByRole("region", { name: "Edits from the latest turn" });
  await edits.getByRole("button", { name: "app.py" }).click();
  const review = dialog("Edits to app.py");
  await review.locator(".dl.add", { hasText: "value = 2" }).waitFor();
  await review.getByRole("button", { name: "Side by side" }).click();
  await review.locator(".diff.split .half.del", { hasText: "value = 1" }).waitFor();
  await shot(page, "edit-review");
  await review.getByRole("button", { name: "Unified" }).click();
  await page.keyboard.press("Escape");

  if (info.git) {
    step("Changes view");
    await tab("Changes").click();
    const changes = page.getByRole("region", { name: "Changes", exact: true });
    await changes.getByRole("button", { name: /app\.py/ }).click();
    await changes.locator(".dl.add", { hasText: "value = 2" }).waitFor();
    await changes.getByRole("button", { name: "Side by side" }).click();
    await changes.locator(".half.add", { hasText: "value = 2" }).waitFor();
    await shot(page, "view-changes");
    await changes.getByRole("button", { name: "Unified" }).click();
    await changes.getByRole("button", { name: "Open in editor" }).click();
    await opened("app.py").waitFor();
  }
  await tab("Code").click();
  await edits.getByRole("button", { name: "Revert edits" }).click();
  await dialog("Revert the agent's edits").getByRole("button", { name: "Revert edits" }).click();
  await log.getByText("Restored app.py.").waitFor();
  await editor.getByText("value = 1").waitFor();
  await edits.waitFor({ state: "hidden" });

  step("a rejected command");
  await send("Run the tests");
  await approval.getByRole("heading", { name: "Run this command in the workspace?" }).waitFor();
  await approval.getByText("python -m pytest -q").waitFor();
  await approval.getByRole("button", { name: "Reject" }).click();
  await log.getByText("Summary of what was done before tool use ended").waitFor();
  await idle();
  await log.locator(".step.failed").last().getByText("failed").waitFor();
  assert.match(await log.locator(".receipt").last().innerText(), /partial/i);

  step("a long command stays readable");
  await send("Show a long command");
  await approval.waitFor();
  const detail = approval.locator(".approval-detail");
  assert.ok(await detail.evaluate((node) => node.scrollHeight > node.clientHeight), "a long command scrolls inside its own box");
  assert.ok((await approval.locator(".ctl").count()) >= 3, "hidden characters are shown by name");
  assert.ok((await approval.boundingBox()).y >= 0, "the top of the request is in view");
  await approval.getByText(/1 line, [\d,]+ characters/).waitFor();
  await approval.getByText("echo part-000").waitFor();
  await shot(page, "approval-long-command");
  await approval.getByRole("button", { name: "Reject" }).click();
  await idle();

  step("stop during a pending approval");
  await send("Start a long check");
  await approval.waitFor();
  await button("Stop turn").click();
  await log.getByText("Turn stopped by operator.").waitFor();
  await approval.waitFor({ state: "hidden" });
  await idle();

  step("a running command with elapsed time, approved for the session");
  await send("Slow command");
  await approval.waitFor();
  await approval.getByRole("button", { name: "Approve and allow this exact command for this session" }).click();
  await log.locator(".step-line.running .elapsed").waitFor();
  await log.getByText("The slow command ran twice.").waitFor({ timeout: 30_000 });
  await idle();
  const slow = log.locator(".step").filter({ hasText: "time.sleep" });
  assert.equal(await slow.count(), 2, "the second identical command ran without asking");
  assert.equal(await slow.locator(".output").count(), 0, "tool output stays folded until asked for");
  await slow.last().getByRole("button").click();
  await slow.last().locator(".output").getByText("exit code 0").waitFor();
  assert.match(await slow.last().locator(".output").innerText(), /slow done/);
  assert.equal(await slow.last().locator(".output").evaluate((node) => getComputedStyle(node).whiteSpace), "pre-wrap");

  await page.locator(".composer").getByText("1 exact request allowed for this session").waitFor();

  step("edits allowed for this session only");
  await send("Bump the value once more");
  await approval.getByRole("button", { name: "Approve and allow edits for this session" }).click();
  await idle();
  await page.locator(".composer").getByText("Edits allowed for this session").waitFor();
  assert.equal(await page.getByLabel("Auto-approve edits").isChecked(), false, "the saved setting is not changed by an approval");
  await editor.getByText("value = 2").waitFor();
  await edits.getByRole("button", { name: "Revert edits" }).click();
  await dialog("Revert the agent's edits").getByRole("button", { name: "Revert edits" }).click();
  await editor.getByText("value = 1").waitFor();
  await send("Bump the value a third time");
  await idle();
  assert.equal(await approval.count(), 0, "the second edit of the session is not put to the operator");
  await editor.getByText("value = 2").waitFor();
  await edits.getByRole("button", { name: "Revert edits" }).click();
  await dialog("Revert the agent's edits").getByRole("button", { name: "Revert edits" }).click();
  await editor.getByText("value = 1").waitFor();
  await edits.waitFor({ state: "hidden" });

  step("the transcript follows only when the reader is at the end");
  await log.evaluate((node) => (node.scrollTop = 0));
  await send("Nothing in particular");
  await log.getByText("Nothing to do for that request.").waitFor();
  await idle();
  assert.ok(await log.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight < 60), "sending a task returns to the end");
  // A reader who scrolls back while a turn runs is left where they are, with a way to return.
  await send("Slow command, once more");
  await log.evaluate((node) => {
    node.scrollTop = 0;
    node.dispatchEvent(new Event("scroll"));
  });
  await idle();
  assert.ok(await log.evaluate((node) => node.scrollTop < 5), "new output must not pull a reader away from where they scrolled");
  await button("Jump to latest").click();
  assert.ok(await log.evaluate((node) => node.scrollHeight - node.scrollTop - node.clientHeight < 60));
  await button("Jump to latest").waitFor({ state: "hidden" });

  step("a failed turn");
  await send("Fail with 401");
  const failure = log.getByRole("alert").filter({ hasText: "Model HTTP 401" });
  await failure.waitFor();
  await idle();
  await failure.getByRole("button", { name: "Open connection" }).click();
  await connection.waitFor();
  await page.keyboard.press("Escape");
  await shot(page, "failed-turn");

  step("a long session: the Loom with more than two hundred rows");
  for (const pass of ["first", "second", "third", "fourth"]) {
    await send(`Read everything, ${pass} pass`);
    await log.getByText("Read the notes again.").nth(["first", "second", "third", "fourth"].indexOf(pass)).waitFor({ timeout: 120_000 });
    await idle();
  }
  assert.ok((await ledger.count()) > 200, "every result has its own row");
  assert.ok((await loom.locator(".thread").count()) < 80, "the figure stays a fixed number of threads");
  assert.ok((await loom.getByRole("button", { name: /^Read original/ }).count()) > 150, "every archived original stays reachable");
  assert.ok(await loom.locator(".loom-ledger").evaluate((node) => node.scrollHeight > node.clientHeight && node.scrollWidth <= node.clientWidth + 1));
  await loom.getByRole("button", { name: /^Read original, row 120,/ }).click();
  await dialog(/Archived result of read_file/).getByText("each thread is checked").first().waitFor();
  await page.keyboard.press("Escape");
  await shot(page, "loom-200-rows");

  step("sessions: new, list, resume");
  await button("New session").click();
  await log.getByRole("heading", { name: "Work on loomworks" }).waitFor();
  await page.locator(".composer").getByText("Edits allowed for this session").waitFor({ state: "hidden" });
  assert.equal(await page.locator(".composer").getByText(/allowed for this session/).count(), 0, "a new session clears what was allowed");
  await loom.getByText("No tool results yet").first().waitFor();
  await button("Sessions").click();
  const sessions = dialog("Sessions");
  await sessions.getByText("Survey the notes").waitFor();
  await shot(page, "sessions");
  await sessions.getByRole("button", { name: "Resume session: Survey the notes" }).click();
  await sessions.waitFor({ state: "hidden" });
  await log.getByText("Surveyed six notes.").waitFor();
  await log.getByText("Restored app.py.").waitFor({ state: "hidden" }).catch(() => {});
  assert.ok((await ledger.count()) >= 8, "the Loom is rebuilt for a resumed session");

  step("Cost view");
  await button("New session").click();
  await log.getByRole("heading", { name: "Work on loomworks" }).waitFor();
  await send("Bump the value in app.py");
  await approval.getByRole("button", { name: "Approve", exact: true }).click();
  await idle();
  await tab("Cost").click();
  const cost = page.getByRole("region", { name: "Cost and tokens" });
  await cost.getByText("This session", { exact: true }).waitFor();
  assert.match(await cost.locator(".figure-value").first().innerText(), /^\$0\.0\d+/);
  await cost.locator(".chart .mark").first().waitFor();
  assert.ok((await cost.locator(".chart .mark").count()) >= 3);
  await cost.locator(".composition-track .chip").first().waitFor();
  await cost.getByText("fixture-coder").first().waitFor();
  await cost.getByRole("button", { name: "Show table" }).click();
  await cost.getByRole("columnheader", { name: "Cached" }).waitFor();
  await cost.getByRole("button", { name: "Show chart" }).click();
  const budget = cost.getByLabel("Budget per turn, USD");
  await budget.fill("0.5");
  await Promise.all([settled(), budget.press("Enter")]);
  await Promise.all([settled(), cost.getByLabel("Reasoning effort").selectOption("low")]);
  await Promise.all([settled(), cost.getByLabel("Reasoning effort").selectOption("")]);
  await budget.fill("0");
  await Promise.all([settled(), budget.press("Enter")]);
  await cost.getByText("fixture-coder-mini").first().waitFor();
  await shot(page, "view-cost");
  await shot(page, "studio-cost", true);
  assert.match(await page.locator(".meter-button").innerText(), /tokens/);

  step("Agents view and a team run");
  await tab("Agents").click();
  const agents = page.getByRole("region", { name: "Agents", exact: true });
  await agents.locator(".gallery li").first().waitFor();
  assert.ok((await agents.locator(".gallery li").count()) >= 4);
  await agents.getByRole("searchbox").fill("code reviewer");
  await agents.getByRole("button", { name: /Code Reviewer/ }).first().click();
  const persona = dialog("Code Reviewer");
  await persona.locator(".prose").waitFor();
  await shot(page, "persona-preview");
  await persona.getByRole("button", { name: "Use Code Reviewer" }).click();
  await page.locator(".composer select").first().waitFor();
  assert.equal(await page.locator(".composer select").first().evaluate((node) => node.selectedOptions[0].textContent), "Code Reviewer");
  await agents.getByRole("searchbox").fill("");
  await Promise.all([settled(), agents.getByRole("button", { name: "Team of 3" }).click()]);
  await page.locator(".composer").getByText("plan, build, review").waitFor();
  await shot(page, "view-agents");
  await shot(page, "studio-agents", true);
  await send("Team task");
  await log.locator(".stages-strip").waitFor();
  await approval.getByRole("heading", { name: "Create team.txt?" }).waitFor({ timeout: 60_000 });
  await shot(page, "team-run");
  await approval.getByRole("button", { name: "Approve", exact: true }).click();
  await log.getByText("Team finished: completed.").waitFor({ timeout: 60_000 });
  await idle();
  assert.equal(await log.locator(".entry.staged").count(), 3, "one report per stage");
  await Promise.all([settled(), agents.getByRole("button", { name: "One agent" }).click()]);

  step("Tools view");
  await tab("Tools").click();
  const tools = page.getByRole("region", { name: "Tools", exact: true });
  await tools.getByRole("heading", { name: /Servers/ }).waitFor();
  // A server defined in the workspace: switched on, then its definition changes underneath.
  {
    const config = path.join(info.root, ".turnloom", "mcp.json");
    const define = (code) => writeFile(config, JSON.stringify({ mcpServers: { clock: { command: "python", args: ["-c", code] } } }));
    const clock = tools.locator(".records > li").filter({ has: page.getByRole("heading", { name: /clock/ }) });
    const available = clock.getByLabel(/Available to the agent/);
    await define("pass");
    await tools.getByRole("button", { name: "Refresh" }).click();
    await clock.getByText("python -c pass").waitFor();
    await Promise.all([settled(), available.click()]);
    await clock.locator("input:checked").waitFor();
    await define("print(1)");
    await tools.getByRole("button", { name: "Refresh" }).click();
    await clock.getByText("changed, switched off").waitFor();
    await clock.getByText("python -c print(1)").waitFor();
    assert.equal(await available.isChecked(), false, "a changed definition is off until it is switched on again");
    await shot(page, "tools-changed");
    await Promise.all([settled(), available.click()]);
    await clock.locator("input:checked").waitFor();
    await clock.getByText("changed, switched off").waitFor({ state: "hidden" });
    await Promise.all([settled(), available.click()]);
    await rm(config);
    await tools.getByRole("button", { name: "Refresh" }).click();
    await clock.waitFor({ state: "hidden" });
  }
  if (info.companions) {
    await tools.getByRole("heading", { name: /verifydoc/ }).waitFor();
    await tools.getByRole("button", { name: "Start and list tools" }).click();
    await tools.getByRole("rowheader", { name: "verify_extraction" }).waitFor({ timeout: 150_000 });
    await shot(page, "view-tools");

    step("Documents view");
    await tab("Documents").click();
    const documents = page.getByRole("region", { name: "Documents", exact: true });
    await documents.getByLabel("Path in the workspace").fill("docs/receipt.txt");
    await documents.getByLabel("Start from").selectOption("Receipt");
    await documents.getByRole("button", { name: "Verify document" }).click();
    await documents.getByRole("rowheader", { name: "total" }).waitFor({ timeout: 150_000 });
    assert.match(await documents.locator(".fields-table tbody").innerText(), /42\.5/);
    await documents.getByRole("button", { name: "total", exact: true }).click();
    await documents.locator(".source mark").first().waitFor();
    await shot(page, "view-documents");
    await shot(page, "studio-documents", true);

    step("an MCP tool call through Unwind, then the undo ledger");
    await tab("Tools").click();
    await Promise.all([settled(), tools.getByLabel(/Available to the agent/).click()]);
    await page.locator(".composer").getByText("MCP servers on: verifydoc").waitFor();
    await send("Verify the receipt");
    const verified = log.getByText("Receipt verified.");
    for (const deadline = Date.now() + 180_000; !(await verified.count()); ) {
      assert.ok(Date.now() < deadline, "the MCP turn did not finish");
      if (await approval.count()) {
        if (await approval.getByText("verify_extraction").count()) await shot(page, "approval-mcp");
        await approval.getByRole("button", { name: "Approve", exact: true }).click();
        await approval.waitFor({ state: "hidden" });
      } else {
        await page.waitForTimeout(200);
      }
    }
    await idle();
    await Promise.all([settled(), tools.getByLabel(/Available to the agent/).click()]);
    await tab("Undo ledger").click();
    const undo = page.getByRole("region", { name: "Undo ledger", exact: true });
    await undo.getByRole("heading", { name: /Recorded actions/ }).waitFor();
    await undo.getByRole("textbox", { name: "Checkpoint label" }).fill("before the refactor");
    await undo.getByRole("button", { name: "Create checkpoint" }).click();
    await undo.getByRole("heading", { name: /before the refactor/ }).waitFor({ timeout: 60_000 });
    await shot(page, "view-ledger");
  } else {
    console.log("  skipped Documents, MCP and the undo ledger: the documents extra is not installed");
    await tab("Documents").click();
    await page.getByText("is not installed").first().waitFor();
    await tab("Undo ledger").click();
    await page.getByRole("region", { name: "Undo ledger" }).getByText("is not installed").waitFor();
  }

  step("Policy view");
  await tab("Policy").click();
  const policy = page.getByRole("region", { name: "Policy", exact: true });
  if (info.guard) {
    await policy.getByRole("button", { name: "Create starter policy" }).click();
    await policy.getByRole("heading", { name: "provio.yaml" }).waitFor({ timeout: 30_000 });
    await policy.getByRole("textbox", { name: "Command, path or URL to test" }).fill('rm -rf "$HOME"');
    await policy.getByRole("button", { name: "Test", exact: true }).click();
    await policy.locator(".verdict-box .verdict.deny").waitFor({ timeout: 30_000 });
    assert.equal(await policy.getByRole("radio", { name: /Confirm every call/ }).isChecked(), true);
    await page.locator(".status").getByText("policy gate on").waitFor();
    // With the gate on, an irreversible command is put to the operator together with the rule that asked.
    await send("Policy check");
    await approval.getByText("git reset --hard").waitFor({ timeout: 60_000 });
    await approval.locator(".assessment").getByText("Policy").waitFor();
    await shot(page, "approval-policy");
    await approval.getByRole("button", { name: "Reject" }).click();
    await idle();
    // A call the policy denies never reaches the operator and is marked as blocked, not as failed.
    await send("Policy denial");
    await log.getByText("The policy handled the read.").waitFor({ timeout: 60_000 });
    await idle();
    await log.locator(".step").filter({ hasText: "id_rsa" }).getByText("blocked by policy").waitFor();
    await policy.getByRole("button", { name: "Refresh" }).click();
    await policy.getByRole("cell", { name: "git reset --hard" }).first().waitFor();
    await shot(page, "view-policy");
    await shot(page, "studio-policy", true);
    await Promise.all([settled(), policy.getByRole("radio", { name: /Gate off/ }).click()]);
    await tab("Code").click();
    await page.getByRole("region", { name: "Files" }).getByRole("button", { name: "provio.yaml", exact: true }).waitFor();
  } else {
    console.log("  skipped the policy gate: Provio is not installed");
    await policy.getByText("is not installed").waitFor();
  }

  step("command palette");
  await page.keyboard.press("Control+k");
  const palette = dialog("Search and commands");
  const search = palette.getByRole("combobox");
  await search.fill("cost");
  await palette.getByRole("option", { name: /Cost/ }).first().waitFor();
  await shot(page, "palette");
  await search.press("Enter");
  await palette.waitFor({ state: "hidden" });
  assert.equal(await tab("Cost").getAttribute("aria-current"), "page");
  await page.keyboard.press("Control+k");
  await search.fill("long.py");
  await palette.getByRole("option", { name: /long\.py/ }).waitFor();
  await search.press("Enter");
  await opened("long.py").waitFor();
  assert.equal(await tab("Code").getAttribute("aria-current"), "page");
  await page.keyboard.press("Control+k");
  await search.fill("debugger");
  await palette.getByRole("option", { name: /Debugger/ }).first().waitFor();
  await search.press("Enter");
  assert.equal(await page.locator(".composer select").first().evaluate((node) => node.selectedOptions[0].textContent), "Debugger");

  step("theme");
  await button("Colour theme: Auto").click();
  await button("Colour theme: Light").click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark");
  assert.equal(await page.evaluate(() => localStorage.getItem("turnloom-theme")), "dark");
  await tab("Code").click();
  await openFile("app.py");
  await shot(page, "desktop-dark");
  await tab("Cost").click();
  await shot(page, "view-cost-dark");
  await tab("Code").click();
  await page.reload();
  await editor.getByText("Fixture project").waitFor();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), "dark", "the theme survives a reload");
  // The persona control shows what the server last ran with, not a hard-coded default.
  await page.waitForFunction(() => document.querySelector(".composer select")?.selectedOptions[0]?.textContent === "Code Reviewer");
  await button("Colour theme: Dark").click();
  assert.equal(await page.evaluate(() => document.documentElement.dataset.theme), undefined);
  await page.emulateMedia({ colorScheme: "dark" });
  assert.equal(await page.evaluate(() => getComputedStyle(document.documentElement).colorScheme), "dark", "Auto follows the system");
  await page.emulateMedia({ colorScheme: "light" });
  await log.getByText("Team finished: completed.").waitFor();
  await shot(page, "desktop-light");

  step("keyboard only: reach and use the view switch");
  await page.locator(".brand").click();
  for (let presses = 0; presses < 12; presses++) {
    await page.keyboard.press("Tab");
    if (await page.evaluate(() => document.activeElement.textContent === "Agents")) break;
  }
  await page.keyboard.press("Enter");
  assert.equal(await tab("Agents").getAttribute("aria-current"), "page");
  await tab("Code").click();

  step("narrow widths");
  for (const width of [1000, 390, 360]) {
    await page.setViewportSize({ width, height: 780 });
    await page.waitForTimeout(150);
    assert.equal(
      await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight),
      true,
      `no page scrollbars at ${width}px`,
    );
    if (width === 1000) {
      await page.getByRole("navigation", { name: "Parts of the Code view" }).getByRole("button", { name: "Files" }).click();
      await page.getByRole("region", { name: "Files" }).getByRole("button", { name: "README.md", exact: true }).waitFor();
      await shot(page, "medium");
      continue;
    }
    await tab("Agent log").click();
    assert.equal(await tab("Agent log").getAttribute("aria-current"), "page");
    await composer.waitFor();
    await shot(page, "mobile-agent-" + width);
    await tab("Code").click();
    assert.equal(await tab("Code").getAttribute("aria-current"), "page");
    const parts = page.getByRole("navigation", { name: "Parts of the Code view" });
    await parts.getByRole("button", { name: "Files" }).click();
    await page.getByRole("region", { name: "Files" }).getByRole("button", { name: "app.py", exact: true }).click();
    await editor.getByText("value = 2").waitFor();
    await shot(page, "mobile-editor-" + width);
    await parts.getByRole("button", { name: "Context Loom" }).click();
    await page.getByRole("region", { name: "Context Loom" }).waitFor();
    await shot(page, "mobile-loom-" + width);
    await tab("Cost").click();
    await page.getByRole("region", { name: "Cost and tokens" }).waitFor();
    await shot(page, "mobile-cost-" + width);
    await tab("Agent log").click();
  }
  await page.setViewportSize({ width: 1600, height: 1000 });

  step("another workspace");
  await button("Workspace: loomworks Change").click();
  await button("Workspace: second-project Change").waitFor();
  await editor.getByText("Second project").waitFor();
  await log.getByRole("heading", { name: "Work on second-project" }).waitFor();
  assert.match(await page.title(), /second-project/);
  await button("Model connection: fixture-coder Change").waitFor();
  await shot(page, "second-workspace");

  // ---- An empty workspace, then a server that goes away ---------------------------------
  step("empty workspace");
  {
    const other = await context.newPage();
    watch(other);
    await other.goto(info.empty);
    await other.getByText("This folder is empty").waitFor();
    await other.getByText("No file open").waitFor();
    await other.getByText("No model connected").waitFor();
    // Inside the desktop window there is no Close control: the window itself closes Studio.
    await other.getByText("Close the window to stop Studio").waitFor();
    assert.equal(await other.getByRole("button", { name: "Close Studio" }).count(), 0);
    await shot(other, "empty-workspace");
    step("server gone");
    expected.add("/api/state");
    await call(info.empty, "close");
    await other.getByRole("heading", { name: "Studio is not running" }).waitFor({ timeout: 30_000 });
    await other.getByText("15 minutes").waitFor();
    await shot(other, "server-gone");
    await other.close();
    expected.delete("/api/state");
  }

  step("a policy file without Provio installed");
  {
    const third = await context.newPage();
    watch(third);
    await third.goto(info.guardless);
    const box = third.getByRole("textbox", { name: "Task for the agent" });
    await third.getByRole("button", { name: "Model connection: fixture-coder Change" }).waitFor();
    await box.fill("Nothing in particular");
    expected.add("/api/run");
    await box.press("Enter");
    const notice = third.getByRole("alert").filter({ hasText: "Provio is not installed" });
    await notice.waitFor();
    expected.delete("/api/run");
    await notice.getByRole("button", { name: "Open the Policy view" }).click();
    const gateless = third.getByRole("region", { name: "Policy", exact: true });
    await gateless.getByText("Provio, the policy gate, is not installed").waitFor();
    await gateless.getByText("turns will not start while the gate is on").waitFor();
    await shot(third, "policy-without-provio");
    await Promise.all([
      third.waitForResponse((response) => response.url().endsWith("/api/settings") && response.ok()),
      gateless.getByRole("button", { name: "Gate off" }).click(),
    ]);
    await box.press("Enter");
    await third.getByRole("log", { name: "Agent log" }).getByText("Nothing to do for that request.").waitFor();
    await third.close();
  }

  step("close Studio");
  await button("Close Studio").click();
  await dialog("Close Studio").getByRole("button", { name: "Close Studio" }).click();
  await page.getByRole("heading", { name: "Studio is closed" }).waitFor();
  await shot(page, "closed");

  assert.deepEqual(pageErrors, [], "uncaught errors in the page");
  assert.deepEqual(consoleErrors, [], "errors logged to the console");
  assert.deepEqual(failures, [], "requests that failed unexpectedly");
  console.log("Passed: Studio browser test");
} catch (error) {
  console.error("Failed during: " + stage);
  if (pageErrors.length) console.error("Errors in the page:\n  " + pageErrors.join("\n  "));
  if (SHOTS && current) await current.screenshot({ path: path.join(SHOTS, "failure.png") }).catch(() => {});
  throw error;
} finally {
  await browser?.close();
  lines.close();
  fixture.kill();
}
