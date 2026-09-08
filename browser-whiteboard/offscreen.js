/* Hidden MediaRecorder owner. It deliberately has no microphone or tab audio. */
(() => {
  const CHANNEL = "agent-whiteboard";
  const MAX_VIDEO = 128 * 1024 * 1024;
  const MAX_TOTAL = 192 * 1024 * 1024;
  const MAX_FRAME = 2 * 1024 * 1024;
  const MAX_RING_FRAME = 1024 * 1024;
  const MAX_FRAME_TOTAL = MAX_TOTAL - MAX_VIDEO;
  const MAX_BOOKMARKS = 50;
  const MAX_EVENTS = 1000;
  const MAX_FRAME_SLOTS = 250;
  const CHUNK_BYTES = 512 * 1024;
  const video = document.querySelector("video");
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d", { alpha: false });
  let state = emptyState();

  function emptyState() {
    return { phase: "idle", stream: null, recorder: null, chunks: [], videoBytes: 0,
      startedAt: null, daemonStartedAt: null, endedAt: null, phoneId: null, sheetId: null,
      events: [], bookmarks: [], frames: [], assets: new Map(), sampleTimer: null, limitTimer: null,
      sampleBusy: false, stoppedReason: "", status: "interrupted", lastError: "", lastClick: null,
      stopping: null, pendingBookmarks: new Set(), heartbeatTimer: null };
  }
  function nowOffset() { return state.startedAt ? Math.max(0, Date.now() - state.startedAt) : 0; }
  function notify(type, extra = {}) {
    chrome.runtime.sendMessage({ channel: CHANNEL, type, target: "review-recorder", ...extra }).catch(() => {});
  }
  function stopTracks() {
    for (const track of state.stream?.getTracks() || []) track.stop();
    state.stream = null; video.srcObject = null;
  }
  function heartbeat() {
    if (!state.phoneId || state.heartbeatTimer) return;
    state.heartbeatTimer = setInterval(() => notify("review-recorder-heartbeat"), 750);
  }
  async function stop(reason = "stopped") {
    if (state.phase === "idle" || state.phase === "stopped") return summary();
    if (state.stopping) return state.stopping;
    state.stoppedReason ||= reason;
    state.stopping = (async () => {
      clearInterval(state.sampleTimer); clearTimeout(state.limitTimer); state.sampleTimer = null; state.limitTimer = null;
      if (state.recorder && state.recorder.state !== "inactive") {
        await new Promise(resolve => {
          state.recorder.addEventListener("stop", resolve, { once: true });
          state.recorder.stop();
        });
      }
      state.endedAt ||= Date.now();
      stopTracks(); state.phase = "stopped";
      state.status = state.stoppedReason === "limit" ? "limit" : state.stoppedReason === "finished" ? "complete" : "interrupted";
      await persist();
      notify("review-recorder-update", { review: summary() });
      return summary();
    })();
    return state.stopping;
  }
  async function prime(message) {
    await stop("replaced");
    state = emptyState(); state.phase = "armed";
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: message.streamId } }
      });
      await attach(stream);
      return summary();
    } catch (error) {
      state.lastError = error.message || String(error); state.phase = "failed";
      notify("review-recorder-failed", { reason: "stream-unavailable", error: state.lastError });
      throw error;
    }
  }
  async function attach(stream) {
    state.stream = stream; video.srcObject = stream; await video.play();
    const [track] = stream.getVideoTracks();
    track?.addEventListener("ended", () => stop("stream-loss"));
  }
  function chooseMime() {
    return ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"].find(type => MediaRecorder.isTypeSupported(type)) || "";
  }
  async function start(message) {
    if (state.phase !== "armed" || !state.stream) throw new Error("Review capture is not armed. Arm Review this tab again.");
    state.phoneId = message.id; state.sheetId = message.sheetId;
    state.startedAt = Date.now(); state.daemonStartedAt = Number.isFinite(message.daemonStartedAt) ? message.daemonStartedAt : null;
    state.phase = "recording"; state.status = "interrupted";
    heartbeat();
    const mimeType = chooseMime();
    const recorder = new MediaRecorder(state.stream, mimeType ? { mimeType, videoBitsPerSecond: 2_000_000 } : { videoBitsPerSecond: 2_000_000 });
    state.recorder = recorder;
    recorder.addEventListener("dataavailable", event => {
      if (!event.data?.size || state.phase === "stopped") return;
      if (state.videoBytes + event.data.size > MAX_VIDEO) { stop("limit").catch(() => {}); return; }
      state.chunks.push(event.data); state.videoBytes += event.data.size;
    });
    recorder.addEventListener("error", () => stop("recorder-error").catch(() => {}));
    recorder.start(1000);
    state.sampleTimer = setInterval(() => sample().catch(() => {}), 500);
    state.limitTimer = setTimeout(() => stop("limit").catch(() => {}), 600000);
    await sample();
    notify("review-recorder-started", { review: summary() });
    return summary();
  }
  async function frameBlob(width) {
    const sourceWidth = video.videoWidth, sourceHeight = video.videoHeight;
    if (!sourceWidth || !sourceHeight) return null;
    const scale = Math.min(1, width / sourceWidth);
    canvas.width = Math.max(1, Math.floor(sourceWidth * scale));
    canvas.height = Math.max(1, Math.floor(sourceHeight * scale));
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    return new Promise(resolve => canvas.toBlob(resolve, "image/png"));
  }
  async function sample() {
    if (state.phase !== "recording" || state.sampleBusy) return null;
    state.sampleBusy = true;
    try {
      let blob = null;
      for (const width of [960, 720, 540, 400]) {
        blob = await frameBlob(width);
        if (!blob || blob.size <= MAX_RING_FRAME) break;
      }
      if (!blob || blob.size > MAX_RING_FRAME) return null;
      const frame = { atMs: nowOffset(), blob };
      state.frames.push(frame);
      while (state.frames.length && state.frames[0].atMs < frame.atMs - 20000) state.frames.shift();
      return frame;
    } finally { state.sampleBusy = false; }
  }
  function reserveAsset(name, blob) {
    if (!blob || blob.size > MAX_FRAME || state.assets.size >= MAX_FRAME_SLOTS) return false;
    const frameBytes = [...state.assets.values()].filter(asset => asset.kind === "frame").reduce((sum, asset) => sum + asset.blob.size, 0);
    if (frameBytes + blob.size > MAX_FRAME_TOTAL) return false;
    state.assets.set(name, { kind: "frame", blob }); return true;
  }
  async function bookmark(message) {
    if (state.phase !== "recording") throw new Error("Start handset recording before bookmarking.");
    if (state.bookmarks.length >= MAX_BOOKMARKS) { await stop("limit"); throw new Error("Review bookmark limit reached."); }
    const mark = { number: state.bookmarks.length + 1, atMs: nowOffset(), label: String(message.label || "Bookmark").slice(0, 160), frames: [], pending: true };
    state.bookmarks.push(mark);
    const capture = (async () => {
      await sample();
      await new Promise(resolve => setTimeout(resolve, 1600));
      await sample();
      const selected = globalThis.AgentReviewEvents?.selectFrames
        ? globalThis.AgentReviewEvents.selectFrames(state.frames, mark.atMs)
        : state.frames.slice(-5);
      const withRelevantAction = state.lastClick && mark.atMs - state.lastClick.atMs <= 20000
        ? [state.lastClick.frame, ...selected.filter(frame => frame !== state.lastClick.frame)] : selected;
      for (const [index, frame] of withRelevantAction.slice(0, 5).entries()) {
        const name = `review-${mark.number}-frame-${index + 1}.png`;
        if (!reserveAsset(name, frame.blob)) break;
        mark.frames.push({ file: name, atMs: frame.atMs });
      }
      delete mark.pending;
      notify("review-recorder-update", { review: summary() });
      return mark;
    })();
    state.pendingBookmarks.add(capture);
    try { return await capture; } finally { state.pendingBookmarks.delete(capture); }
  }
  async function event(message) {
    const input = message.event;
    if (!input || typeof input !== "object") return false;
    if (state.phase !== "recording" || state.events.length >= MAX_EVENTS) return false;
    const atMs = Number.isFinite(message.wallAt) ? Math.max(0, message.wallAt - state.startedAt) : nowOffset();
    state.events.push({ ...input, atMs });
    if (input.type === "click") {
      const frame = state.frames.filter(candidate => candidate.atMs <= atMs).at(-1) || await sample();
      if (frame) state.lastClick = { atMs, frame };
    }
    return true;
  }
  function videoAsset() {
    if (!state.chunks.length) return null;
    return new Blob(state.chunks, { type: state.recorder?.mimeType || "video/webm" });
  }
  function summary() {
    const videoBlob = videoAsset();
    return { version: 1, startedAt: state.startedAt, daemonStartedAt: state.daemonStartedAt,
      endedAt: state.endedAt, status: state.status, reason: state.stoppedReason || state.lastError || "",
      phoneId: state.phoneId, sheetId: state.sheetId, events: state.events, bookmarks: state.bookmarks.map(({ pending, ...bookmark }) => bookmark),
      video: videoBlob ? { file: "review.webm", bytes: videoBlob.size, mimeType: "video/webm" } : null,
      ...(state.status === "complete" && !videoBlob ? { status: "interrupted", reason: "No video data was produced." } : {}) };
  }
  async function prepare() {
    await Promise.all([...state.pendingBookmarks]);
    if (state.phase === "recording" || state.phase === "armed") await stop("finished");
    const videoBlob = videoAsset();
    if (videoBlob && videoBlob.size <= MAX_VIDEO) state.assets.set("review.webm", { kind: "video", blob: videoBlob });
    const total = [...state.assets.values()].reduce((sum, asset) => sum + asset.blob.size, 0);
    if (total > MAX_TOTAL) throw new Error("Review recording exceeded its storage limit.");
    await persist();
    return { review: summary(), assets: [...state.assets.entries()].map(([name, asset]) => ({ name, bytes: asset.blob.size, mimeType: asset.blob.type, chunkBytes: CHUNK_BYTES })) };
  }
  function acknowledge() {
    clearInterval(state.heartbeatTimer); state.heartbeatTimer = null;
    return true;
  }
  async function chunk(message) {
    const asset = state.assets.get(message.name);
    const offset = Math.max(0, Number(message.offset) || 0);
    const length = Math.min(CHUNK_BYTES, Math.max(0, Number(message.length) || CHUNK_BYTES));
    if (!asset || offset >= asset.blob.size) throw new Error("Recorded asset is unavailable.");
    const bytes = new Uint8Array(await asset.blob.slice(offset, offset + length).arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return { data: btoa(binary), size: asset.blob.size };
  }
  async function persist() {
    // Keep the stopped artifacts locally until the bridge acknowledges every chunk.
    // IndexedDB is best-effort; the live in-memory copies remain usable on quota errors.
    if (!state.phoneId || !globalThis.indexedDB) return;
    try {
      const db = await new Promise((resolve, reject) => {
        const request = indexedDB.open("agent-phone-review", 1);
        request.onupgradeneeded = () => request.result.createObjectStore("recordings");
        request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error);
      });
      const values = [...state.assets.entries()].map(([name, asset]) => [name, asset.kind, asset.blob]);
      await new Promise((resolve, reject) => {
        const tx = db.transaction("recordings", "readwrite");
        tx.objectStore("recordings").put({ review: summary(), assets: values }, state.phoneId);
        tx.oncomplete = resolve; tx.onerror = () => reject(tx.error);
      });
      db.close();
    } catch (_) {}
  }
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.channel !== CHANNEL || message.target !== "review-recorder") return;
    const actions = { "review-prime": prime, "review-start": start, "review-stop": ({ reason }) => stop(reason),
      "review-event": event, "review-bookmark": bookmark, "review-prepare": prepare, "review-chunk": chunk, "review-status": summary,
      "review-ack": acknowledge };
    const action = actions[message.type];
    if (!action) return;
    Promise.resolve(action(message)).then(value => respond({ ok: true, value }), error => respond({ ok: false, error: error.message }));
    return true;
  });
})();
