import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, cp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { chromium } from "playwright";

const extensionDir = dirname(dirname(fileURLToPath(import.meta.url)));

test("review controls are explicit, bookmark has no default shortcut, and idle recording leaves clicks alone", { timeout: 60000 }, async () => {
  const temp = await mkdtemp(join(tmpdir(), "agent-review-test-"));
  const extension = join(temp, "extension");
  await mkdir(extension);
  for (const file of ["manifest.json", "background.js", "core.js", "review-events.js", "overlay.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js"]) {
    await cp(join(extensionDir, file), join(extension, file));
  }
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  manifest.host_permissions = ["<all_urls>"];
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  const server = createServer((_, res) => res.end("<!doctype html><button id='normal'>ordinary click</button><p id='result'></p><script>normal.onclick=()=>result.textContent='clicked'</script>"));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let context;
  try {
    context = await chromium.launchPersistentContext(join(temp, "profile"), {
      channel: "chromium", headless: true, args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
    });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${new URL(worker.url()).host}/popup.html`);
    await popup.getByRole("button", { name: "Review this tab", exact: true }).waitFor();
    assert.match(await popup.locator(".review-note").textContent(), /selected tab’s video/);
    const commands = await worker.evaluate(() => chrome.commands.getAll());
    assert.equal(commands.find(command => command.name === "bookmark-review")?.shortcut, "");
    assert.deepEqual(await worker.evaluate(() => handle({ type: "review-status" }, { url: chrome.runtime.getURL("popup.html") })), { state: "idle" });
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`); await page.bringToFront();
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ active: true, currentWindow: true }))[0].id);
    await worker.evaluate(tab => runCommand("browse", { id: tab }), tabId);
    await page.locator("agent-phone-whiteboard").waitFor();
    assert.equal(await page.locator("agent-phone-whiteboard").getByRole("button", { name: "Bookmark review", exact: true }).isDisabled(), true);
    await page.locator("#normal").click();
    assert.equal(await page.locator("#result").textContent(), "clicked");
  } finally {
    await context?.close(); await new Promise(resolve => server.close(resolve));
    await rm(temp, { recursive: true, force: true });
  }
});
