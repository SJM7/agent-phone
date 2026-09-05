import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, cp, readFile, writeFile, mkdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:http";
import { execFileSync, spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { chromium } from "playwright";

const extensionDir = dirname(dirname(fileURLToPath(import.meta.url)));

test("live page → numbered references → frozen iteration → usable export", { timeout: 120000 }, async () => {
  const temp = await mkdtemp(join(tmpdir(), "agent-whiteboard-test-"));
  const extension = join(temp, "extension");
  await mkdir(extension);
  for (const file of ["manifest.json", "background.js", "core.js", "overlay.js", "review.html", "review.css", "review.js", "zip.js", "popup.html", "popup.js"]) await cp(join(extensionDir, file), join(extension, file));
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  // Headless automation cannot physically click Chrome's toolbar to grant activeTab.
  // Broader access exists ONLY in this disposable fixture; production is activeTab-only.
  manifest.host_permissions = ["<all_urls>"];
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  const html = await readFile(join(extensionDir, "demo.html"));
  const server = createServer((_, res) => { res.setHeader("content-type", "text/html"); res.end(html); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let context;
  try {
    context = await chromium.launchPersistentContext(join(temp, "profile"), {
      channel: "chromium", headless: true, viewport: { width: 1280, height: 900 }, acceptDownloads: true,
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
    });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    const page = await context.newPage();
    const errors = []; page.on("pageerror", e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.bringToFront();
    const targetId = await worker.evaluate(async () => {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      return tab.id;
    });
    // Exercise the real popup message path on a page with no overlay/draft.
    // Direct runCommand/injection skips the queue and previously hid a deadlock:
    // control -> command -> await ready -> open queued behind control.
    const popup = await context.newPage();
    await popup.goto(`chrome-extension://${new URL(worker.url()).host}/popup.html`);
    await page.bringToFront();
    const activated = await popup.evaluate(async tabId => Promise.race([
      chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "control", command: "browse", tabId }),
      new Promise((_, reject) => setTimeout(() => reject(new Error("Popup activation deadlocked")), 5000))
    ]), targetId);
    assert.equal(activated.ok, true); assert.equal(activated.value.ok, true);
    await popup.close();
    const overlay = page.locator("agent-phone-whiteboard");
    await overlay.getByText("0 marks", { exact: false }).waitFor();
    // Side-button macros target Chrome extension commands, bypassing page focus
    // and held-key event handling. Check registration plus the real dispatch path.
    const commands = await worker.evaluate(() => chrome.commands.getAll());
    for (const name of ["toggle-point", "toggle-box", "toggle-ink"]) assert.ok(commands.find(c => c.name === name)?.shortcut);
    await worker.evaluate(() => runCommand("toggle-box"));
    assert.equal(await overlay.getByRole("button", { name: "Box", exact: true }).getAttribute("aria-pressed"), "true");
    const diagnostic = await worker.evaluate(async () => (await chrome.storage.session.get("lastCommand")).lastCommand);
    assert.equal(diagnostic.command, "toggle-box"); assert.equal(diagnostic.status, "applied");
    await worker.evaluate(() => runCommand("toggle-box"));
    assert.equal(await overlay.getByRole("button", { name: "Browse", exact: true }).getAttribute("aria-pressed"), "true");
    await overlay.getByRole("button", { name: "Hide whiteboard", exact: true }).click();
    await worker.evaluate(() => runCommand("toggle-ink"));
    assert.equal(await overlay.getByRole("button", { name: "Ink", exact: true }).getAttribute("aria-pressed"), "true");
    await worker.evaluate(() => runCommand("toggle-ink"));
    const marks = async () => worker.evaluate(async () => Object.values(await chrome.storage.local.get(null)).flatMap(s => s.marks || []));
    const waitMarkCount = async n => {
      await page.waitForFunction(() => document.querySelector("agent-phone-whiteboard").shadowRoot.querySelector(".hint").textContent.includes("saved"));
      assert.equal((await marks()).length, n);
    };
    // Ordinary click passes through. A held-key mark on that same button does not.
    await page.locator("#shop").click();
    assert.match(await page.locator("#result").textContent(), /ready/);
    await page.evaluate(() => { document.querySelector("#result").textContent = ""; });
    const shop = await page.locator("#shop").boundingBox();
    await page.keyboard.down("Alt"); await page.mouse.click(shop.x + 25, shop.y + 15); await page.keyboard.up("Alt");
    await waitMarkCount(1);
    assert.equal(await page.locator("#result").textContent(), "");
    const first = (await marks())[0];
    assert.equal(first.kind, "element"); assert.equal(first.element.selector, "#shop"); assert.equal(first.number, 1);
    assert.match(first.image, /^data:image\/png;base64,/);
    // A region is one continuous drag and gets a separate reference.
    await page.keyboard.down("Alt"); await page.mouse.move(180, 190); await page.mouse.down(); await page.mouse.move(620, 390, { steps: 15 }); await page.mouse.up(); await page.keyboard.up("Alt");
    await waitMarkCount(2); assert.equal((await marks())[1].kind, "region");
    // Freehand + undo never recycles a number already used in narration.
    await overlay.getByRole("button", { name: "Ink", exact: true }).click();
    await page.mouse.move(720, 300); await page.mouse.down(); await page.mouse.move(850, 390, { steps: 15 }); await page.mouse.up();
    await waitMarkCount(3); assert.equal((await marks())[2].kind, "ink");
    await overlay.getByRole("button", { name: "Undo", exact: false }).click();
    await overlay.getByText("2 marks", { exact: false }).waitFor();
    await page.mouse.move(730, 310); await page.mouse.down(); await page.mouse.move(880, 430, { steps: 15 }); await page.mouse.up();
    await waitMarkCount(3); assert.deepEqual((await marks()).map(m => m.number), [1, 2, 4]);
    await page.keyboard.press("Escape");
    // Restore screenshots and metadata across a full reload, without retargeting old marks.
    await page.reload();
    // No manual injection: an activated tab restores itself on refresh.
    await overlay.getByText("3 marks", { exact: false }).waitFor();
    assert.equal((await marks())[0].image, first.image);
    assert.equal(await overlay.locator("svg circle").count(), 0);
    // New mark tracks the live element through scroll, but not a replacement DOM node.
    const head = await page.locator("#headline").boundingBox();
    await page.keyboard.down("Alt"); await page.mouse.click(head.x + 20, head.y + 20); await page.keyboard.up("Alt"); await waitMarkCount(4);
    const before = await overlay.locator("svg rect").getAttribute("y");
    await page.evaluate(() => scrollTo(0, 80)); await page.waitForTimeout(100);
    const after = await overlay.locator("svg rect").getAttribute("y");
    assert.ok(Number(after) < Number(before));
    await page.evaluate(() => { const n = document.querySelector("#headline"); n.replaceWith(n.cloneNode(true)); });
    await page.waitForTimeout(100); assert.equal(await overlay.locator("svg circle").count(), 0);
    await mkdir(join(extensionDir, "test-results"), { recursive: true });
    await page.screenshot({ path: join(extensionDir, "test-results", "overlay.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    const dockBounds = await overlay.locator(".dock").boundingBox();
    assert.ok(dockBounds.x >= 0 && dockBounds.x + dockBounds.width <= 391);
    await page.screenshot({ path: join(extensionDir, "test-results", "mobile.png") });
    await page.setViewportSize({ width: 1280, height: 900 });
    await overlay.getByRole("button", { name: "Finish sheet", exact: true }).click();
    await overlay.getByText("Iteration 1 saved", { exact: false }).waitFor();
    const sheets = await worker.evaluate(async () => Object.values(await chrome.storage.local.get(null)));
    const finished = sheets.find(s => s.finishedAt);
    assert.equal(finished.marks.length, 4); assert.equal(sheets.find(s => !s.finishedAt).marks.length, 0);
    const review = await context.newPage();
    review.on("pageerror", e => errors.push(e.message));
    await review.goto(`chrome-extension://${new URL(worker.url()).host}/review.html#${finished.id}`);
    await review.locator(".mark").nth(3).waitFor();
    await review.locator("#notes").fill("Reference 1: make this button quieter. Reference 2: tighten spacing within this region.");
    await review.locator("#save").click(); await review.getByText("Saved locally", { exact: true }).waitFor();
    await review.screenshot({ path: join(extensionDir, "test-results", "review.png"), fullPage: true });
    const [download] = await Promise.all([review.waitForEvent("download"), review.locator("#export").click()]);
    const zipPath = join(extensionDir, "test-results", "sheet.zip"); await download.saveAs(zipPath);
    assert.match(execFileSync("unzip", ["-t", zipPath], { encoding: "utf8" }), /No errors detected/);
    const brief = execFileSync("unzip", ["-p", zipPath, "brief.md"], { encoding: "utf8" });
    assert.match(brief, /make this button quieter/); assert.match(brief, /mark-4.png/);
    const exported = JSON.parse(execFileSync("unzip", ["-p", zipPath, "context.json"], { encoding: "utf8" }));
    assert.equal(exported.marks.length, 4); assert.equal(exported.marks[0].image, "mark-1.png");
    assert.deepEqual(errors, []);
    // Real extension → authenticated Python bridge → annotated local bundle.
    const fixture = spawn(join(extensionDir, "../.venv/bin/python"), ["-u", join(extensionDir, "../tests/whiteboard_fixture.py"), join(temp, "handoffs")]);
    const lines = createInterface({ input: fixture.stdout })[Symbol.asyncIterator]();
    const reply = async () => JSON.parse((await lines.next()).value);
    const request = async op => { fixture.stdin.write(JSON.stringify({ op }) + "\n"); return reply(); };
    try {
      const config = await reply();
      await worker.evaluate(async ({ port, token }) => {
        const originalFetch = globalThis.fetch;
        globalThis.fetch = (input, options) => originalFetch(typeof input === "string" ? input.replace("127.0.0.1:8489", `127.0.0.1:${port}`) : input, options);
        await handle({ type: "pair", token }, { url: chrome.runtime.getURL("popup.html") });
      }, config);
      await review.close(); await page.bringToFront();
      await overlay.getByText("Phone bridge ready", { exact: true }).waitFor();
      for (let refresh = 0; refresh < 2; refresh++) {
        await page.reload();
        await overlay.getByText("Phone bridge ready", { exact: true }).waitFor();
        const savedToken = await worker.evaluate(async () => (await chrome.storage.local.get("phoneBridgeToken")).phoneBridgeToken);
        assert.equal(savedToken, config.token);
        assert.equal(await page.locator("agent-phone-whiteboard").count(), 1);
      }
      const connection = await worker.evaluate(() => handle({ type: "connection" }, { url: chrome.runtime.getURL("popup.html") }));
      assert.match(connection.message, /Phone connected.*pairing saved/);
      assert.ok((await request("begin")).id);
      await overlay.getByText("Recording + whiteboard", { exact: true }).waitFor();
      await worker.evaluate(() => runCommand("toggle-box"));
      await page.mouse.move(180, 190); await page.mouse.down(); await page.mouse.move(620, 390, { steps: 10 }); await page.mouse.up();
      await overlay.getByText("Reference 1 saved", { exact: false }).waitFor();
      await request("finish");
      const bundle = await request("bundle");
      assert.match(bundle.prompt, /brief.md/);
      assert.match(await readFile(join(bundle.path, "narration.txt"), "utf8"), /tip of reference 2/);
      const meta = JSON.parse(await readFile(join(bundle.path, "context.json"), "utf8"));
      assert.equal(meta.marks.length, 1); assert.equal(meta.marks[0].image, undefined);
      const png = await readFile(join(bundle.path, "mark-1.png"));
      assert.equal(png.subarray(1, 4).toString(), "PNG");
      await cp(join(bundle.path, "mark-1.png"), join(extensionDir, "test-results", "bridge-mark.png"));
      await overlay.getByText("Test handoff saved", { exact: false }).waitFor();
      await overlay.getByText("0 marks", { exact: false }).waitFor();
      // Wait for the next sheet's heartbeat, not a stale pre-hangup lease.
      let nextRecording;
      for (let i = 0; i < 20; i++) {
        nextRecording = await request("begin");
        if (nextRecording.id) break;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      assert.ok(nextRecording.id, "fresh iteration can record again");
    } finally { fixture.stdin.end(); fixture.kill(); }
  } finally {
    await context?.close(); await new Promise(resolve => server.close(resolve));
    await rm(temp, { recursive: true, force: true });
  }
});
