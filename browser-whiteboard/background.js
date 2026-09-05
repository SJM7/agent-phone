/* Local-only storage. No host access until the user invokes the extension. */
const PREFIX = "whiteboard:";
importScripts("core.js");
// Pairing credentials are available only to trusted extension contexts.
chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
let queue = Promise.resolve();
let lastCapture = 0;
const WATCH_PREFIX = "whiteboard-tab:";
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  if (change.status !== "complete") return;
  (async () => {
    const key = WATCH_PREFIX + tabId;
    const origin = (await chrome.storage.session.get(key))[key];
    if (!origin) return;
    if (!tab.url || new URL(tab.url).origin !== origin) {
      await chrome.storage.session.remove(key);
      return;
    }
    await activate(tab);
  })().catch(error => console.warn("Whiteboard restore failed", error));
});
chrome.tabs.onRemoved.addListener(tabId => {
  chrome.storage.session.remove(WATCH_PREFIX + tabId).catch(() => {});
});

async function activate(tab) {
  if (!tab?.id) return;
  try {
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["core.js", "overlay.js"] });
    await chrome.action.setBadgeText({ tabId: tab.id, text: "" });
  } catch (error) {
    await chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
    await chrome.action.setTitle({ tabId: tab.id, title: "Cannot annotate this page. Open an ordinary http(s) page." });
    console.warn(error);
  }
}
chrome.action.onClicked.addListener(activate);

async function runCommand(command, tab) {
  if (!tab?.id) [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  if (!tab?.id) return;
  await chrome.storage.session.set({ lastCommand: { command, at: new Date().toISOString(), status: "received" } });
  try {
    const [installed] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => Boolean(globalThis.__agentPhoneWhiteboard?.command)
    });
    if (!installed.result) {
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["core.js", "overlay.js"] });
    }
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: command => globalThis.__agentPhoneWhiteboard.command(command),
      args: [command]
    });
    await chrome.action.setBadgeText({ tabId: tab.id, text: "" });
    await chrome.storage.session.set({ lastCommand: { command, at: new Date().toISOString(), status: "applied" } });
    return { ok: true };
  } catch (error) {
    await chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
    await chrome.action.setTitle({ tabId: tab.id, title: "Whiteboard: " + error.message });
    console.warn(error);
    await chrome.storage.session.set({ lastCommand: { command, at: new Date().toISOString(), status: "failed", error: error.message } });
    return { ok: false, error: error.message };
  }
}
chrome.commands.onCommand.addListener(runCommand);

async function getSheet(id) {
  const key = PREFIX + id;
  const sheet = (await chrome.storage.local.get(key))[key];
  if (!sheet) throw new Error("Sheet not found.");
  return sheet;
}
async function putSheet(sheet) {
  await chrome.storage.local.set({ [PREFIX + sheet.id]: sheet });
  return sheet;
}
async function listSheets() {
  return Object.entries(await chrome.storage.local.get(null))
    .filter(([key]) => key.startsWith(PREFIX)).map(([, value]) => value)
    .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}
async function openDraft(tab, page) {
  const sheets = await listSheets();
  const existing = sheets.find(s => !s.finishedAt && s.tabId === tab.id && s.url === page.url);
  if (existing) return existing;
  return putSheet({
    id: crypto.randomUUID(), version: 1, tabId: tab.id,
    iteration: 1 + Math.max(0, ...sheets.filter(s => s.url === page.url).map(s => s.iteration)),
    title: page.title, url: page.url, createdAt: new Date().toISOString(),
    finishedAt: null, nextNumber: 1, narration: "", marks: []
  });
}
async function capture(tab, mark) {
  // Chrome allows two captures per second. Also verify the tab before AND after:
  // captureVisibleTab takes a window ID and would otherwise capture a different tab.
  await new Promise(resolve => setTimeout(resolve, Math.max(0, 600 - (Date.now() - lastCapture))));
  const assertActive = async () => {
    const [active] = await chrome.tabs.query({ active: true, windowId: tab.windowId });
    if (active?.id !== tab.id) throw new Error("Keep this tab active until the mark is saved.");
    const state = await chrome.tabs.sendMessage(tab.id, { channel: "agent-whiteboard-capture", mark });
    if (!state?.ready) throw new Error("The page moved during capture. Please make this mark again.");
  };
  await assertActive();
  lastCapture = Date.now();
  const image = await chrome.tabs.captureVisibleTab(tab.windowId, { format: "png" });
  await assertActive();
  return image;
}

async function handle(message, sender) {
  const tab = sender.tab;
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "connection") {
    const paired = Boolean((await chrome.storage.local.get("phoneBridgeToken")).phoneBridgeToken);
    if (!paired) return { message: "Phone not paired yet." };
    try {
      await bridgeRequest({ op: "heartbeat", active: false });
      return { message: "Phone connected · pairing saved across page refreshes." };
    } catch (error) {
      return { message: "Pairing saved · " + error.message + ". Refreshing the page does not erase your token." };
    }
  }
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "pair") {
    const token = String(message.token).trim();
    await bridgeRequest({ op: "heartbeat", active: false }, token);
    await chrome.storage.local.set({ phoneBridgeToken: token });
    return true;
  }
  if (message.type === "bridge" && tab) return bridgeTick(message, tab);
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "control") {
    return runCommand(message.command, await chrome.tabs.get(message.tabId));
  }
  const isReview = !tab && sender.url?.startsWith(chrome.runtime.getURL("review.html"));
  // Extension pages may also have sender.tab, so use the URL to identify review.
  const review = isReview || sender.url?.startsWith(chrome.runtime.getURL("review.html"));
  if (message.type === "watch" && tab) {
    if (message.visible) await chrome.storage.session.set({ [WATCH_PREFIX + tab.id]: new URL(tab.url).origin });
    else await chrome.storage.session.remove(WATCH_PREFIX + tab.id);
    return true;
  }
  if (message.type === "open" && tab) {
    await chrome.storage.session.set({ [WATCH_PREFIX + tab.id]: new URL(tab.url).origin });
    return openDraft(tab, message.page);
  }
  if (message.type === "review" && tab) {
    await chrome.tabs.create({ url: chrome.runtime.getURL("review.html") + "#" + message.id });
    return true;
  }
  if (review && message.type === "list") return (await listSheets()).map(({ marks, ...s }) => ({ ...s, count: marks.length }));
  if (review && message.type === "get") return getSheet(message.id);
  if (review && message.type === "delete") {
    const s = await getSheet(message.id);
    if (!s.finishedAt) throw new Error("Finish this sheet on the page before deleting it.");
    await chrome.storage.local.remove(PREFIX + s.id);
    return true;
  }
  if (review && message.type === "notes") {
    const s = await getSheet(message.id);
    // Narration is separate from the immutable visual evidence.
    s.narration = String(message.narration).slice(0, 100000);
    return putSheet(s);
  }
  if (!tab || !message.id) throw new Error("Unsupported request.");
  const s = await getSheet(message.id);
  if (s.tabId !== tab.id || s.finishedAt) throw new Error("This sheet is already finished or belongs to another tab.");
  if (message.type === "mark") {
    if (s.marks.length >= 100) throw new Error("Finish this sheet to start another (100 marks per sheet).");
    const mark = message.mark;
    if (!["element", "region", "ink"].includes(mark?.kind)) throw new Error("Invalid mark.");
    mark.image = await capture(tab, mark);
    mark.capturedAt = new Date().toISOString();
    mark.number = s.nextNumber++;
    s.marks.push(mark);
    return putSheet(s);
  }
  if (message.type === "undo") {
    s.marks.pop(); // Never reuse a spoken reference number.
    return putSheet(s);
  }
  if (message.type === "finish") {
    if (!s.marks.length) throw new Error("Make a mark before finishing this sheet.");
    s.finishedAt = new Date().toISOString();
    await putSheet(s);
    return { finished: s, next: await openDraft(tab, message.page) };
  }
  throw new Error("Unsupported request.");
}

async function bridgeRequest(body, token) {
  token ||= (await chrome.storage.local.get("phoneBridgeToken")).phoneBridgeToken;
  if (!token) return null;
  const response = await fetch("http://127.0.0.1:8489/whiteboard", {
    method: "POST", headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
    body: JSON.stringify(body), signal: AbortSignal.timeout(15000)
  });
  if (!response.ok) throw new Error(`Phone bridge ${response.status}; check service and pairing`);
  return response.json();
}
async function annotated(mark) {
  const bitmap = await createImageBitmap(await (await fetch(mark.image)).blob());
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext("2d");
  ctx.drawImage(bitmap, 0, 0); bitmap.close();
  AgentWhiteboardCore.paint(ctx, mark, canvas.width / mark.viewport.width);
  const bytes = new Uint8Array(await (await canvas.convertToBlob({ type: "image/png" })).arrayBuffer());
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return { ...mark, image: "data:image/png;base64," + btoa(binary) };
}
async function bridgeTick(message, tab) {
  const sheet = await getSheet(message.id);
  if (sheet.tabId !== tab.id) throw new Error("Wrong whiteboard tab");
  const window = await chrome.windows.get(tab.windowId);
  const current = await chrome.tabs.get(tab.id);
  const state = await bridgeRequest({ op: "heartbeat", sheetId: sheet.id,
    active: Boolean(message.active && current.active && window.focused && !sheet.finishedAt) });
  if (!state) return { phase: "unpaired" };
  if (state.phase === "finishing" && !state.visualsSaved) {
    // This runs inside the same queue as captures and undo. A pending mark is
    // fully saved before this snapshot, and subsequent marks use the next sheet.
    const frozen = { ...sheet, finishedAt: sheet.finishedAt || new Date().toISOString() };
    const marks = [];
    for (const mark of frozen.marks) marks.push(await annotated(mark));
    const result = await bridgeRequest({ op: "freeze", id: state.id,
      sheet: { ...frozen, marks }, brief: AgentWhiteboardCore.summary(frozen) });
    if (!result?.ok) throw new Error("Phone did not acknowledge the sheet; original retained");
    // Do not rotate until terminal delivery completes; status stays visible and
    // retries remain idempotent while transcription runs.
    await putSheet(frozen);
  }
  if (["delivered", "failed"].includes(state.phase) && sheet.finishedAt) {
    const next = await openDraft(tab, { title: sheet.title, url: sheet.url });
    return { ...state, next: { ...next, marks: [] } };
  }
  return state;
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.channel !== "agent-whiteboard") return;
  // Controls can inject an overlay whose command awaits its initial `open`
  // message. Holding the storage queue here would deadlock that nested request.
  // Only trusted popup controls bypass the queue; sheet writes stay serialized.
  const control = sender.url === chrome.runtime.getURL("popup.html") && message.type === "control";
  const task = control ? handle(message, sender) : queue.then(() => handle(message, sender));
  if (!control) queue = task.catch(() => {});
  task.then(value => {
    // The live overlay needs geometry only, not megabytes of archived pixels.
    const light = s => ({ ...s, marks: s.marks.map(({ image, ...m }) => m) });
    if (sender.tab && !sender.url?.startsWith(chrome.runtime.getURL("review.html"))) {
      if (value?.marks) value = light(value);
      else if (value?.finished) value = { finished: light(value.finished), next: light(value.next) };
    }
    respond({ ok: true, value });
  }, error => respond({ ok: false, error: error.message }));
  return true;
});
