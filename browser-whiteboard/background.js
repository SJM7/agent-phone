/* Local-only storage. No host access until the user invokes the extension. */
const PREFIX = "whiteboard:";
importScripts("core.js");
try { importScripts("review-events.js"); } catch (_) {}
// Pairing credentials are available only to trusted extension contexts.
chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
let queue = Promise.resolve();
let lastCapture = 0;
const WATCH_PREFIX = "whiteboard-tab:";
const REVIEW_SESSION = "review-recording";
let creatingOffscreen;

async function offscreen() {
  const url = chrome.runtime.getURL("offscreen.html");
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"], documentUrls: [url] });
  if (contexts.length) return;
  if (!creatingOffscreen) {
    creatingOffscreen = chrome.offscreen.createDocument({ url: "offscreen.html", reasons: ["USER_MEDIA"], justification: "Record an explicitly armed browser review without opening a tab." })
      .finally(() => { creatingOffscreen = null; });
  }
  await creatingOffscreen;
}
async function recorder(type, extra = {}) {
  await offscreen();
  const response = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", target: "review-recorder", type, ...extra });
  if (!response?.ok) throw new Error(response?.error || "Review recorder did not respond.");
  return response.value;
}
async function reviewSession() { return (await chrome.storage.session.get(REVIEW_SESSION))[REVIEW_SESSION] || null; }
async function putReview(session) { await chrome.storage.session.set({ [REVIEW_SESSION]: session }); return session; }
async function reviewStatus() {
  const session = await reviewSession();
  return session || (await chrome.storage.session.get("lastReview"))?.lastReview || { state: "idle" };
}
async function armReview(tab) {
  if (!tab?.id || !/^https?:\/\//.test(tab.url || "")) throw new Error("Open an ordinary http(s) page before arming review capture.");
  if (!(await chrome.storage.local.get("phoneBridgeToken")).phoneBridgeToken) throw new Error("Connect the handset first, then arm this tab.");
  const previous = await reviewSession();
  if (previous?.state === "recording" || previous?.state === "armed") throw new Error("A review is already armed. Stop it before recording another tab.");
  if (["upload-failed", "uploaded"].includes(previous?.state) || (previous?.state === "stopped" && previous.phoneId)) throw new Error("The previous review handoff is still finishing. Wait for the handset delivery before arming another tab.");
  await offscreen();
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  const session = { state: "arming", tabId: tab.id, windowId: tab.windowId, origin: new URL(tab.url).origin, url: tab.url,
    armedAt: Date.now(), phoneId: null, sheetId: null, error: "", review: null };
  await putReview(session);
  try {
    await recorder("review-prime", { streamId });
    session.state = "armed"; await putReview(session);
    await chrome.action.setBadgeText({ tabId: tab.id, text: "REC" });
    return session;
  } catch (error) {
    session.state = "failed"; session.error = error.message; await putReview(session);
    await chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
    throw error;
  }
}
async function stopReview(reason = "stopped") {
  const session = await reviewSession();
  if (!session) return { state: "idle" };
  let review = session.review;
  try { review = await recorder("review-stop", { reason }); } catch (error) { session.error = error.message; }
  const stopped = { ...session, state: "stopped", review, endedAt: Date.now(), error: session.error || "" };
  // A stopped stream can still belong to a live handset recording. Keep its
  // pinned sheet and local assets until the bridge accepts the final manifest.
  await chrome.storage.session.set({ [REVIEW_SESSION]: stopped, lastReview: stopped });
  if (session.tabId) await chrome.action.setBadgeText({ tabId: session.tabId, text: "" });
  return stopped;
}
async function bookmarkReview(tab, label = "Bookmark") {
  const session = await reviewSession();
  if (!session || session.state !== "recording" || session.tabId !== tab?.id) throw new Error("Start handset recording before bookmarking this tab.");
  const bookmark = await recorder("review-bookmark", { label });
  session.review = { ...(session.review || {}), bookmarks: [...(session.review?.bookmarks || []), bookmark] };
  await putReview(session); return bookmark;
}
async function startReviewForHeartbeat(state, sheet) {
  const session = await reviewSession();
  if (!session || session.state !== "armed") return null;
  if (session.tabId !== sheet.tabId || session.origin !== new URL(sheet.url).origin) return null;
  if (state.phase !== "recording" || !state.id) return null;
  const review = await recorder("review-start", { id: state.id, sheetId: sheet.id, daemonStartedAt: state.startedAt });
  try { await bridgeRequest({ op: "review_begin", id: state.id, sheetId: sheet.id }); }
  catch (error) { await recorder("review-stop", { reason: "review-begin-failed" }); throw error; }
  await putReview({ ...session, state: "recording", phoneId: state.id, sheetId: sheet.id, review, startedAt: review.startedAt, daemonStartedAt: state.startedAt });
  return review;
}
async function uploadReview(session, phoneId, sheetId) {
  if (session.phoneId !== phoneId || session.sheetId !== sheetId) throw new Error("Review recording is pinned to a different handset session or sheet.");
  const prepared = await recorder("review-prepare");
  if (prepared.review.phoneId !== phoneId || prepared.review.sheetId !== sheetId) throw new Error("Recorder handoff does not match the pinned review.");
  for (const asset of prepared.assets) {
    let offset = 0, index = 0;
    while (offset < asset.bytes) {
      const part = await recorder("review-chunk", { name: asset.name, offset, length: Math.min(512 * 1024, asset.bytes - offset) });
      const size = Math.floor(String(part.data || "").replace(/=+$/, "").length * 3 / 4);
      if (!size || size > 512 * 1024) throw new Error("Recorder produced an invalid upload chunk.");
      await bridgeRequest({ op: "review_asset", id: phoneId, name: asset.name, index, data: part.data, last: offset + size >= asset.bytes });
      offset += size; index++;
    }
  }
  return prepared.review;
}
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  (async () => {
    const recording = await reviewSession();
    if (recording?.tabId === tabId && tab.url && new URL(tab.url).origin !== recording.origin) {
      await stopReview("cross-origin-navigation");
    }
    if (change.status !== "complete") return;
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
  reviewSession().then(session => session?.tabId === tabId && stopReview("tab-closed")).catch(() => {});
});

async function activate(tab) {
  if (!tab?.id) return;
  try {
    const files = ["core.js", ...(globalThis.AgentReviewEvents ? ["review-events.js"] : []), "overlay.js"];
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files });
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
      const files = ["core.js", ...(globalThis.AgentReviewEvents ? ["review-events.js"] : []), "overlay.js"];
      await chrome.scripting.executeScript({ target: { tabId: tab.id }, files });
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
chrome.commands.onCommand.addListener((command, tab) => {
  if (command === "bookmark-review") {
    bookmarkReview(tab).catch(async error => {
      if (tab?.id) {
        await chrome.action.setBadgeText({ tabId: tab.id, text: "!" });
        await chrome.action.setTitle({ tabId: tab.id, title: "Review bookmark: " + error.message });
      }
    });
    return;
  }
  runCommand(command, tab);
});
chrome.tabCapture.onStatusChanged.addListener(info => {
  reviewSession().then(session => {
    if (session?.tabId === info.tabId && ["armed", "recording"].includes(session.state) && ["stopped", "error"].includes(info.status)) return stopReview(`stream-${info.status}`);
  }).catch(() => {});
});

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
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "review-status") return reviewStatus();
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "review-arm") {
    return armReview(await chrome.tabs.get(message.tabId));
  }
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "review-stop") return stopReview("stopped-by-user");
  if (sender.url === chrome.runtime.getURL("popup.html") && message.type === "review-bookmark") {
    return bookmarkReview(await chrome.tabs.get(message.tabId), message.label || "Bookmark");
  }
  if (message.type === "review-status" && tab) return reviewStatus();
  if (message.type === "review-bookmark" && tab) return bookmarkReview(tab, message.label || "Bookmark");
  if (message.type === "review-stop" && tab) {
    const session = await reviewSession();
    if (session?.tabId !== tab.id) throw new Error("This tab does not own the armed review.");
    return stopReview("stopped-by-user");
  }
  if (message.type === "review-event" && tab) {
    const session = await reviewSession();
    if (!session || session.state !== "recording" || session.tabId !== tab.id || session.origin !== new URL(tab.url).origin) return false;
    const event = globalThis.AgentReviewEvents?.normalizeEvent?.(message.event);
    if (!event) return false;
    return recorder("review-event", { event, wallAt: Number(message.wallAt) });
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
async function finalizeReview(state, sheet) {
  if (!["finishing", "failed"].includes(state.phase) || state.visualsSaved) return null;
  const frozen = { ...sheet, finishedAt: sheet.finishedAt || new Date().toISOString() };
  const marks = [];
  for (const mark of frozen.marks) marks.push(await annotated(mark));
  const session = await reviewSession();
  let review;
  if (session?.phoneId === state.id && session.sheetId === sheet.id) {
    if (session.state === "uploaded" && session.review) review = session.review;
    else {
      try {
        await putReview({ ...session, state: "finalizing" });
        review = await uploadReview(session, state.id, sheet.id);
        await putReview({ ...session, state: "uploaded", review });
      } catch (error) {
        await putReview({ ...session, state: "upload-failed", error: error.message });
        throw new Error("Review assets remain saved locally and will retry: " + error.message);
      }
    }
  }
  const result = await bridgeRequest({ op: "freeze", id: state.id,
    sheet: { ...frozen, marks }, brief: AgentWhiteboardCore.summary(frozen), ...(review ? { review } : {}) });
  if (!result?.ok) throw new Error("Phone did not acknowledge the sheet; original retained");
  await putSheet(frozen);
  return frozen;
}
async function acknowledgeTerminal(state) {
  const session = await reviewSession();
  if (!session?.phoneId || session.phoneId !== state.id) return;
  const terminal = { ...session, state: state.phase, endedAt: Date.now(),
    error: state.phase === "failed" ? (state.message || "Handset delivery failed; review saved locally.") : session.error || "" };
  await recorder("review-ack");
  await chrome.storage.session.set({ [REVIEW_SESSION]: null, lastReview: terminal });
}
async function finalizeFailedReview(state, sheet) {
  const session = await reviewSession();
  if (session?.phoneId === state.id && ["armed", "recording", "stopped", "finalizing", "upload-failed", "uploaded"].includes(session.state)) {
    if (!["stopped", "uploaded"].includes(session.state)) await stopReview("handoff-failed");
  }
  if (!state.visualsSaved) await finalizeReview(state, sheet);
  await acknowledgeTerminal(state);
}
async function finishReviewFromRecorder() {
  const session = await reviewSession();
  if (!session?.phoneId || !session.sheetId || !["recording", "stopped", "finalizing", "upload-failed", "uploaded"].includes(session.state)) return;
  const sheet = await getSheet(session.sheetId);
  const state = await bridgeRequest({ op: "heartbeat", sheetId: sheet.id, active: false });
  if (!state) return;
  if (state.phase === "finishing" && !state.visualsSaved) await finalizeReview(state, sheet);
  if (state.phase === "failed") return finalizeFailedReview(state, sheet);
  if (state.phase === "delivered") await acknowledgeTerminal(state);
}
async function bridgeTick(message, tab) {
  const sheet = await getSheet(message.id);
  if (sheet.tabId !== tab.id) throw new Error("Wrong whiteboard tab");
  const window = await chrome.windows.get(tab.windowId);
  const current = await chrome.tabs.get(tab.id);
  const state = await bridgeRequest({ op: "heartbeat", sheetId: sheet.id,
    active: Boolean(message.active && current.active && window.focused && !sheet.finishedAt) });
  if (!state) return { phase: "unpaired" };
  try {
    await startReviewForHeartbeat(state, sheet);
  } catch (error) {
    const session = await reviewSession();
    if (session) await putReview({ ...session, state: "failed", error: error.message });
  }
  if (state.phase === "finishing" && !state.visualsSaved) {
    // Do not rotate until terminal delivery completes; status stays visible and
    // retries remain idempotent while transcription runs.
    await finalizeReview(state, sheet);
  }
  if (state.phase === "failed") await finalizeFailedReview(state, sheet);
  if (["delivered", "failed"].includes(state.phase) && sheet.finishedAt) {
    if (state.phase === "delivered") await acknowledgeTerminal(state);
    const next = await openDraft(tab, { title: sheet.title, url: sheet.url });
    return { ...state, next: { ...next, marks: [] } };
  }
  return state;
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.channel !== "agent-whiteboard") return;
  if (message.type === "review-recorder-update" && sender.url === chrome.runtime.getURL("offscreen.html")) {
    reviewSession().then(async session => {
      if (!session) return;
      await putReview({ ...session, review: message.review || session.review, state: message.review?.status === "complete" ? "stopped" : session.state });
    }).catch(() => {});
    return;
  }
  if (message.type === "review-recorder-failed" && sender.url === chrome.runtime.getURL("offscreen.html")) {
    reviewSession().then(async session => session && putReview({ ...session, state: "failed", error: message.error || message.reason || "Recorder failed" })).catch(() => {});
    return;
  }
  if (message.type === "review-recorder-heartbeat" && sender.url === chrome.runtime.getURL("offscreen.html")) {
    const task = queue.then(() => finishReviewFromRecorder());
    queue = task.catch(() => {});
    task.then(() => respond({ ok: true }), error => respond({ ok: false, error: error.message }));
    return true;
  }
  if (message.target === "review-recorder") return;
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
