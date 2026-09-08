import { test } from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const extensionDir = dirname(dirname(fileURLToPath(import.meta.url)));

async function loadHelpers() {
  const source = await readFile(join(extensionDir, "review-events.js"), "utf8");
  const context = { URL };
  context.globalThis = context;
  vm.runInNewContext(source, context, { filename: "review-events.js" });
  return context.AgentReviewEvents;
}

test("normalizeEvent emits only bounded review-safe navigation metadata", async () => {
  const { normalizeEvent } = await loadHelpers();
  const event = normalizeEvent({
    type: "keydown",
    atMs: 1250.5,
    key: "ArrowDown",
    url: "https://user:secret@example.com/a/path?token=hidden#private",
    viewport: { width: 999999, height: 844 },
    scroll: { x: -200000, y: 42 },
    target: {
      tag: "BUTTON\u0000ignored",
      role: "button",
      name: "N".repeat(100),
      testId: "open-menu",
      selector: "#open-menu",
      bounds: { x: 10, y: 20, width: 30, height: 40 },
      expanded: true,
      disabled: false,
      value: "must not survive",
      text: "must not survive",
      password: "must not survive",
    },
    unknown: "discard me",
  });

  assert.deepEqual(JSON.parse(JSON.stringify(event)), {
    type: "keydown",
    atMs: 1250.5,
    url: "https://example.com/a/path",
    viewport: { width: 20000, height: 844 },
    scroll: { x: -100000, y: 42 },
    target: {
      tag: "BUTTONignored",
      role: "button",
      name: "N".repeat(80),
      testId: "open-menu",
      selector: "#open-menu",
      bounds: { x: 10, y: 20, width: 30, height: 40 },
      expanded: true,
      disabled: false,
    },
    key: "ArrowDown",
  });
  assert.equal(normalizeEvent({ type: "keydown", atMs: 1, key: "a" }), null);
  assert.equal(normalizeEvent({ type: "input", atMs: 1 }), null);
  assert.equal(normalizeEvent({ type: "click", atMs: -1 }), null);
  assert.equal(normalizeEvent({ type: "click", atMs: 1, url: "javascript:alert(1)" }).url, undefined);
  assert.equal(normalizeEvent({ type: "click", atMs: 1, key: "Enter" }).key, undefined);
});

test("normalizeEvent suppresses likely text-entry identifiers and tolerates partial geometry", async () => {
  const { normalizeEvent } = await loadHelpers();
  for (const target of [
    { tag: "INPUT", name: "email", testId: "email", selector: "#email" },
    { tag: "textarea", name: "notes", testId: "notes", selector: "#notes" },
    { tag: "DIV", role: "textbox", name: "search words", testId: "search", selector: ".search" },
    { tag: "DIV", role: "searchbox", name: "secret", testId: "query", selector: "[role=searchbox]" },
  ]) {
    const event = normalizeEvent({ type: "click", atMs: 0, target });
    assert.equal(event.target.name, undefined);
    assert.equal(event.target.testId, undefined);
    assert.equal(event.target.selector, undefined);
  }

  const partial = normalizeEvent({
    type: "scroll",
    atMs: 2,
    viewport: { width: 0, height: Number.NaN },
    scroll: { x: Number.POSITIVE_INFINITY, y: 5 },
    target: { bounds: { x: 1, y: 2, width: -3, height: 4 }, expanded: "true", disabled: 1 },
  });
  assert.deepEqual(JSON.parse(JSON.stringify(partial)), {
    type: "scroll",
    atMs: 2,
    scroll: { y: 5 },
    target: { bounds: { x: 1, y: 2, height: 4 } },
  });
});

test("selectFrames returns the five distinct temporal samples in chronological order", async () => {
  const { selectFrames } = await loadHelpers();
  const frames = [
    { atMs: 3600, file: "after-1500.png" },
    { atMs: 0, file: "before-2000.png" },
    { atMs: 2000, file: "at.png" },
    { atMs: 500, file: "before-500.png" },
    { atMs: 2600, file: "after-500.png" },
    { atMs: Number.NaN, file: "invalid.png" },
  ];
  const before = frames.slice();
  const selected = selectFrames(frames, 2000);
  assert.deepEqual(Array.from(selected, frame => frame.file), [
    "before-2000.png", "before-500.png", "at.png", "after-500.png", "after-1500.png",
  ]);
  assert.equal(selected[2], frames[2]);
  assert.deepEqual(frames, before);

  const same = { atMs: 2000, file: "same.png" };
  const single = selectFrames([same], 2000);
  assert.equal(single.length, 1);
  assert.equal(single[0], same);
  assert.equal(selectFrames([], 2000).length, 0);
  assert.equal(selectFrames([{ atMs: 1 }], Number.NaN).length, 0);
});
