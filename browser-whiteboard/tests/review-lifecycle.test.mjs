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
const streamShim = `(() => {
  const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  navigator.mediaDevices.getUserMedia = async constraints => {
    if (constraints?.video?.mandatory?.chromeMediaSource !== "tab") return original(constraints);
    const canvas = document.createElement("canvas"); canvas.width = 640; canvas.height = 360;
    const draw = () => { const c = canvas.getContext("2d"); c.fillStyle = "#193c2f"; c.fillRect(0, 0, 640, 360); c.fillStyle = "#fffefa"; c.font = "28px sans-serif"; c.fillText("Agent Phone review " + Date.now(), 32, 120); };
    draw(); setInterval(draw, 100); return canvas.captureStream(10);
  };
})();`;
const waitFor = async (predicate, timeout = 30000) => {
  const until = Date.now() + timeout;
  while (Date.now() < until) {
    const value = await predicate();
    if (value) return value;
    await new Promise(resolve => setTimeout(resolve, 150));
  }
  throw new Error("Timed out waiting for review lifecycle state.");
};

test("closed tab and failed handset delivery still save and release review assets", { timeout: 120000 }, async t => {
  const temp = await mkdtemp(join(tmpdir(), "agent-review-lifecycle-"));
  const extension = join(temp, "extension");
  await mkdir(extension);
  for (const file of ["manifest.json", "background.js", "core.js", "review-events.js", "overlay.js", "popup.html", "popup.js", "offscreen.html", "offscreen.js"]) {
    await cp(join(extensionDir, file), join(extension, file));
  }
  const manifest = JSON.parse(await readFile(join(extension, "manifest.json"), "utf8"));
  manifest.host_permissions = ["<all_urls>"];
  await writeFile(join(extension, "manifest.json"), JSON.stringify(manifest));
  await writeFile(join(extension, "offscreen-test-stream.js"), streamShim);
  await writeFile(join(extension, "offscreen.html"), (await readFile(join(extension, "offscreen.html"), "utf8")).replace("<script src=\"review-events.js\"></script><script src=\"offscreen.js\"></script>", "<script src=\"review-events.js\"></script><script src=\"offscreen-test-stream.js\"></script><script src=\"offscreen.js\"></script>"));
  const server = createServer((_, res) => res.end(`<!doctype html><button id="do">Change this page</button><p id="state">start</p><script>do.onclick=()=>state.textContent='changed '+Date.now()</script>`));
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  let context, fixture;
  try {
    context = await chromium.launchPersistentContext(join(temp, "profile"), {
      channel: "chromium", headless: true, viewport: { width: 1280, height: 720 }, args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`]
    });
    const worker = context.serviceWorkers()[0] || await context.waitForEvent("serviceworker");
    fixture = spawn(join(extensionDir, "../.venv/bin/python"), ["-u", join(extensionDir, "../tests/whiteboard_fixture.py"), join(temp, "handoffs")]);
    const lines = createInterface({ input: fixture.stdout })[Symbol.asyncIterator]();
    const reply = async () => JSON.parse((await lines.next()).value);
    const request = async op => { fixture.stdin.write(JSON.stringify({ op }) + "\n"); return reply(); };
    const config = await reply();
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}`); await page.bringToFront();
    const tabId = await worker.evaluate(async () => (await chrome.tabs.query({ active: true, currentWindow: true }))[0].id);
    await worker.evaluate(async ({ config }) => {
      const realFetch = globalThis.fetch;
      globalThis.fetch = (input, options) => realFetch(typeof input === "string" ? input.replace("127.0.0.1:8489", `127.0.0.1:${config.port}`) : input, options);
      await handle({ type: "pair", token: config.token }, { url: chrome.runtime.getURL("popup.html") });
    }, { config });
    await worker.evaluate(tab => runCommand("browse", { id: tab }), tabId);
    const overlay = page.locator("agent-phone-whiteboard");
    await overlay.getByText("Phone bridge ready", { exact: true }).waitFor();
    // openPopup is the closest browser API available to this headless harness for
    // the same explicit extension-action invocation a reviewer performs manually.
    await worker.evaluate(() => chrome.action.openPopup().catch(() => {}));
    await new Promise(resolve => setTimeout(resolve, 100));
    // This uses the real service-worker tabCapture path. It may be unavailable in
    // constrained Chromium builds; do not replace the recorder or bridge with mocks.
    const armed = await worker.evaluate(async tab => {
      try { return { value: await armReview(await chrome.tabs.get(tab)) }; }
      catch (error) { return { error: error.message }; }
    }, tabId);
    if (armed.error) {
      // Headless Chromium cannot deliver the toolbar's activeTab gesture. Keep
      // the real tabCapture attempt above, then replace only getUserMedia's
      // source with a deterministic canvas stream; recorder and bridge stay real.
      t.diagnostic(`tabCapture toolbar gesture unavailable here: ${armed.error}`);
      armed.value = await worker.evaluate(async ({ tabId, url }) => {
        const tab = await chrome.tabs.get(tabId);
        const session = { state: "arming", tabId, windowId: tab.windowId, origin: new URL(url).origin, url, armedAt: Date.now(), phoneId: null, sheetId: null, error: "", review: null };
        await putReview(session); await recorder("review-prime", { streamId: "test-only-stream-id" });
        session.state = "armed"; await putReview(session); return session;
      }, { tabId, url: page.url() });
    }
    assert.equal(armed.value.state, "armed");
    const phone = await request("begin"); assert.ok(phone.id);
    await waitFor(async () => (await worker.evaluate(() => reviewStatus())).state === "recording");
    await page.waitForFunction(() => {
      const host = document.querySelector("agent-phone-whiteboard");
      return host && !host.shadowRoot.querySelector('[data-action="bookmark-review"]').disabled;
    });
    await page.locator("#do").click();
    const bookmark = await worker.evaluate(async tab => bookmarkReview(await chrome.tabs.get(tab), "Changed state"), tabId);
    assert.ok(bookmark.frames.length >= 1);
    const stopped = await worker.evaluate(() => stopReview("stopped-by-test"));
    assert.equal(stopped.state, "stopped"); assert.equal(stopped.phoneId, phone.id);
    await page.close(); // No page overlay remains to send the finishing heartbeat.
    await request("failed");
    let terminalStatus;
    await waitFor(async () => {
      terminalStatus = await worker.evaluate(() => reviewStatus());
      return terminalStatus.state === "failed";
    }, 45000).catch(error => { throw new Error(error.message + " " + JSON.stringify(terminalStatus)); });
    const bundle = await request("bundle-failed"); assert.ok(bundle.path);
    assert.equal(await worker.evaluate(async () => (await chrome.storage.session.get("review-recording"))["review-recording"]), null);
    assert.equal(await worker.evaluate(async () => (await chrome.storage.session.get("lastReview")).lastReview.state), "failed");
    const review = JSON.parse(await readFile(join(bundle.path, "review.json"), "utf8"));
    assert.equal(review.status, "interrupted");
    const click = review.events.find(event => event.type === "click");
    assert.equal(click?.target?.tag, "button"); assert.equal(click?.target?.selector, "#do");
    assert.ok(review.video?.bytes > 0); assert.ok(review.bookmarks[0].frames.length >= 1);
    const video = join(bundle.path, "review.webm");
    execFileSync(process.env.FFMPEG || "ffmpeg", ["-v", "error", "-i", video, "-frames:v", "1", "-f", "null", "-"], { stdio: "pipe" });
    const frame = await readFile(join(bundle.path, review.bookmarks[0].frames[0].file));
    assert.deepEqual([...frame.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
    const evidence = join(extensionDir, "test-results", "recorded-review");
    await mkdir(evidence, { recursive: true });
    await cp(video, join(evidence, "review.webm"));
    await cp(join(bundle.path, review.bookmarks[0].frames[0].file), join(evidence, "review-1-frame-1.png"));
    await cp(join(bundle.path, "review.json"), join(evidence, "review.json"));
    fixture.stdin.end(); fixture.kill();
  } finally {
    fixture?.stdin.end(); fixture?.kill(); await context?.close();
    await new Promise(resolve => server.close(resolve)); await rm(temp, { recursive: true, force: true });
  }
});
