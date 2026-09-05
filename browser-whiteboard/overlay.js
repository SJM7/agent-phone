(() => {
  const KEY = "__agentPhoneWhiteboard";
  if (globalThis[KEY]) return; // Restore and manual activation may race.
  const { rect } = AgentWhiteboardCore;
  const NS = "http://www.w3.org/2000/svg";
  const host = document.createElement("agent-phone-whiteboard");
  host.style.cssText = "all:initial!important;position:fixed!important;inset:0!important;z-index:2147483647!important;pointer-events:none!important;";
  document.documentElement.append(host);
  const root = host.attachShadow({ mode: "open" });
  root.innerHTML = `
    <style>
      :host { color-scheme:light; }
      * { box-sizing:border-box; }
      .surface { position:fixed;inset:0;pointer-events:none; }
      .surface.armed { pointer-events:auto;cursor:crosshair;touch-action:none; }
      svg { position:absolute;inset:0;width:100%;height:100%;overflow:hidden;pointer-events:none; }
      .dock { position:fixed;bottom:20px;left:50%;transform:translateX(-50%);pointer-events:auto;
        display:flex;align-items:center;gap:5px;padding:7px;border:1px solid #ddd9d0;border-radius:14px;
        background:#fffefa;color:#262a26;box-shadow:0 8px 40px #18201924;font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;width:max-content;max-width:calc(100vw - 20px);flex-wrap:wrap;justify-content:center; }
      button, select { font:inherit;color:inherit;border:0;cursor:pointer; }
      button { background:transparent;border-radius:8px;padding:9px 11px;white-space:nowrap; }
      button:hover { background:#eeece5; }
      button:focus-visible, select:focus-visible { outline:2px solid #df4f30;outline-offset:2px; }
      button[aria-pressed="true"] { background:#f9e7df;color:#a32e18; }
      button:disabled { opacity:.45;cursor:default; }
      .brand { padding:0 10px;font-weight:650;white-space:nowrap; }
      .brand span { color:#9b9b91;margin-left:5px;font-weight:450; }
      .divider { width:1px;height:24px;background:#e4e1d9;margin:0 3px; }
      .finish { background:#283c32;color:white; }
      .finish:hover { background:#375443; }
      .hint { position:fixed;bottom:85px;left:50%;transform:translateX(-50%);padding:7px 12px;border-radius:8px;
        background:#fffefaed;border:1px solid #e4e1d9;color:#54574f;max-width:90vw;text-align:center;font:12px/1.5 -apple-system,BlinkMacSystemFont,sans-serif; }
      .hint.error { color:#a32e18; }
      .gone { display:none!important; }
      select { background:#f0eee7;border-radius:6px;padding:6px;max-width:95px; }
      @media(max-width:600px) { .dock { bottom:8px;gap:1px;padding:5px;width:max-content; } button { padding:8px; } .brand { display:none; } .hint { bottom:115px; } }
    </style>
    <div class="surface"><svg aria-hidden="true"></svg></div>
    <div class="hint" role="status" aria-live="polite">Opening a sheet…</div>
    <nav class="dock" aria-label="Page whiteboard">
      <div class="brand">Whiteboard <span class="iteration">01</span></div>
      <button data-tool="browse" aria-pressed="true" title="Use the page normally · Escape">Browse</button>
      <button data-tool="element" aria-pressed="false" title="Click an element to mark it">Point</button>
      <button data-tool="region" aria-pressed="false" title="Drag a boundary">Box</button>
      <button data-tool="ink" aria-pressed="false" title="Draw a freehand stroke">Ink</button>
      <span class="divider"></span>
      <select aria-label="Hold-to-mark key" title="Map a mouse button to this held key"><option value="Alt">Hold ⌥ / Alt</option><option value="F8">Hold F8</option></select>
      <button data-action="undo" title="Undo the last mark · Ctrl/⌘ Z while drawing">↶ Undo</button>
      <button data-action="review" title="Review sheets and export for your model">Sheets</button>
      <button data-action="finish" class="finish" title="Save this iteration and start a clean sheet">Finish sheet</button>
      <button data-action="hide" aria-label="Hide whiteboard" title="Reopen with extension icon or Alt Shift W">×</button>
    </nav>`;
  const surface = root.querySelector(".surface"), svg = root.querySelector("svg"), dock = root.querySelector(".dock"), hint = root.querySelector(".hint");
  let sheet, tool = "browse", held = false, visible = true, busy = false, gesture = null, hover = null, suppressClick = false;
  let modifier = "Alt", frame = 0, stableViewport = null;
  const anchors = new Map();
  const bridgeLabel = document.createElement("div");
  bridgeLabel.style.cssText = "position:fixed;top:8px;right:8px;background:#fffefa;color:#283c32;padding:6px 10px;border:1px solid #cbd4c4;border-radius:6px;font:12px system-ui;pointer-events:none";
  root.append(bridgeLabel);
  let bridgePolling = false, bridgeLocked = false;
  const page = () => ({ url: location.href, title: document.title });
  const viewport = () => ({ width: innerWidth, height: innerHeight, dpr: devicePixelRatio, scrollX, scrollY });
  const sameView = (a, b) => a.width === b.width && a.height === b.height && a.scrollX === b.scrollX && a.scrollY === b.scrollY;
  chrome.runtime.onMessage.addListener((message, _sender, respond) => {
    if (message?.channel === "agent-whiteboard-capture") {
      respond({ ready: busy && !document.hidden && message.mark.page.url === location.href && sameView(message.mark.viewport, viewport()) });
    }
  });
  async function send(type, extra = {}) {
    const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type, id: sheet?.id, ...extra });
    if (!result?.ok) throw new Error(result?.error || "Extension disconnected. Reload the page and reopen it.");
    return result.value;
  }
  function status(text, error = false) { hint.textContent = text; hint.classList.toggle("error", error); }
  function defaultHint() {
    if (!sheet) return;
    const restored = sheet.marks.filter(m => !anchors.has(m.id)).length;
    status(`${sheet.marks.length} mark${sheet.marks.length === 1 ? "" : "s"} · Hold ${modifier === "Alt" ? "⌥ / Alt" : modifier}: click to point, drag to box · Esc to browse${restored ? ` · ${restored} earlier mark(s) in Sheets` : ""}`);
  }
  function update() {
    surface.classList.toggle("armed", visible && !busy && (held || tool !== "browse"));
    for (const button of root.querySelectorAll("[data-tool]")) {
      button.setAttribute("aria-pressed", String(button.dataset.tool === tool));
      button.disabled = busy;
    }
    root.querySelector('[data-action="undo"]').disabled = busy || !sheet?.marks.length;
    root.querySelector('[data-action="finish"]').disabled = busy || !sheet?.marks.length;
    root.querySelector('[data-action="review"]').disabled = busy || !sheet;
    root.querySelector(".iteration").textContent = String(sheet?.iteration || 1).padStart(2, "0");
    schedule();
  }
  function toggle() {
    if (busy) return;
    visible = !visible; held = false; gesture = null; hover = null;
    send("watch", { visible }).catch(err => status(err.message, true));
    host.style.setProperty("display", visible ? "block" : "none", "important");
    update();
  }
  async function command(name) {
    await ready;
    if (busy || bridgeLocked) return;
    const tools = { "toggle-point": "element", "toggle-box": "region", "toggle-ink": "ink" };
    if (tools[name]) {
      const wasVisible = visible;
      if (!visible) toggle();
      tool = wasVisible && tool === tools[name] ? "browse" : tools[name];
      held = false; gesture = null; hover = null; update();
      status(tool === "browse" ? "Browsing · page clicks work normally" : `${{ element: "Point", region: "Box", ink: "Ink" }[tool]} enabled · press the same mouse button again to browse`);
    } else if (name === "browse") {
      if (!visible) toggle();
      tool = "browse"; held = false; gesture = null; hover = null; update(); defaultHint();
    } else if (name === "hide") { if (visible) toggle(); }
    else if (name === "undo-mark") await perform("undo");
    else if (name === "finish-sheet") await perform("finish");
    else if (name === "open-sheets") await perform("review");
  }
  globalThis[KEY] = { toggle, command };
  const svgNode = (name, attrs = {}) => {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  };
  function drawGeometry(kind, g, number, faint = false) {
    const group = svgNode("g", { opacity: faint ? ".55" : "1" });
    const style = { stroke: "#df4f30", "stroke-width": "2", fill: "#df4f3010", "stroke-linecap": "round", "stroke-linejoin": "round" };
    let x = g.x, y = g.y;
    if (kind === "ink") {
      group.append(svgNode("polyline", { ...style, fill: "none", points: g.points.map(p => `${p.x},${p.y}`).join(" ") }));
      x = g.points[0].x; y = g.points[0].y;
    } else {
      group.append(svgNode("rect", { ...style, ...g, rx: 3, "stroke-dasharray": faint ? "5 4" : "none" }));
    }
    if (number) {
      x = Math.max(14, Math.min(innerWidth - 14, x)); y = Math.max(14, y);
      group.append(svgNode("circle", { cx: x, cy: y, r: 12, fill: "#df4f30" }));
      const label = svgNode("text", { x, y: y + 4, "text-anchor": "middle", fill: "white", "font-family": "system-ui", "font-size": 12, "font-weight": 650 });
      label.textContent = String(number); group.append(label);
    }
    svg.append(group);
  }
  function box(el) {
    const b = el.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height };
  }
  function translateGeometry(mark, dx, dy) {
    const g = mark.geometry;
    return mark.kind === "ink" ? { points: g.points.map(p => ({ x: p.x + dx, y: p.y + dy })) } : { ...g, x: g.x + dx, y: g.y + dy };
  }
  function render() {
    frame = 0; svg.replaceChildren();
    if (!visible || busy || bridgeLocked) return;
    for (const mark of sheet?.marks || []) {
      const anchor = anchors.get(mark.id);
      if (!anchor || mark.page.url !== location.href) continue;
      if (mark.kind === "element") {
        // Preserve actual node identity; never rebind a selector after React replaces it.
        if (!anchor.node?.isConnected) continue;
        const g = box(anchor.node);
        if (g.width && g.height) drawGeometry(mark.kind, g, mark.number);
      } else if (mark.viewport.width === innerWidth && mark.viewport.height === innerHeight && !anchor.stale) {
        drawGeometry(mark.kind, translateGeometry(mark, mark.viewport.scrollX - scrollX, mark.viewport.scrollY - scrollY), mark.number);
      }
    }
    if (gesture) {
      const kind = gesture.kind === "auto" ? "region" : gesture.kind;
      if (kind === "element" && gesture.node) drawGeometry("element", box(gesture.node), null, true);
      else if (kind === "ink") drawGeometry("ink", { points: gesture.points }, null, true);
      else drawGeometry("region", rect(gesture.start, gesture.end), null, true);
    } else if (hover?.isConnected && (held || tool === "element")) drawGeometry("element", box(hover), null, true);
  }
  function schedule() { if (!frame) frame = requestAnimationFrame(render); }
  function elementAt(x, y) {
    // Disabling the host briefly makes elementFromPoint reach the actual page.
    host.style.setProperty("display", "none", "important");
    let el = document.elementFromPoint(x, y);
    host.style.removeProperty("display");
    while (el?.shadowRoot?.elementFromPoint) {
      const inner = el.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === el) break;
      el = inner;
    }
    return el;
  }
  function describe(el) {
    if (!el) return null;
    const parts = [];
    let n = el;
    for (let depth = 0; n && depth < 6; depth++, n = n.parentElement) {
      if (n.id) { parts.unshift(`#${CSS.escape(n.id)}`); break; }
      let segment = n.localName;
      if (n.parentElement) {
        const siblings = [...n.parentElement.children].filter(s => s.localName === n.localName);
        if (siblings.length > 1) segment += `:nth-of-type(${siblings.indexOf(n) + 1})`;
      }
      parts.unshift(segment);
    }
    return { tag: el.localName, selector: parts.join(" > "), text: (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 500), role: el.getAttribute("role"), ariaLabel: el.getAttribute("aria-label"), shadowRoot: el.getRootNode() !== document };
  }
  function ownEvent(e) { return e.composedPath().includes(host); }
  function editable(e) { return e.composedPath().some(n => n instanceof Element && (n.matches("input,textarea,select") || n.isContentEditable)); }
  function stop(e) { e.preventDefault(); e.stopImmediatePropagation(); }
  function point(e) { return { x: e.clientX, y: e.clientY }; }
  window.addEventListener("keydown", e => {
    if (!visible || busy) return;
    if (e.key === "Escape") {
      if (held || tool !== "browse" || gesture) { stop(e); held = false; gesture = null; tool = "browse"; hover = null; update(); defaultHint(); }
      return;
    }
    if (editable(e)) return;
    if (e.key === modifier) { stop(e); held = true; update(); }
    if ((held || tool !== "browse") && (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "z") { stop(e); perform("undo"); }
  }, true);
  window.addEventListener("keyup", e => {
    if (e.key === modifier && held) { stop(e); held = false; hover = null; update(); }
  }, true);
  window.addEventListener("blur", () => { held = false; gesture = null; hover = null; update(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden) { held = false; gesture = null; update(); } });
  surface.addEventListener("pointerdown", e => {
    if (busy || bridgeLocked || e.button !== 0) return;
    stop(e); suppressClick = true;
    const p = point(e), node = elementAt(p.x, p.y);
    gesture = { kind: held && tool === "browse" ? "auto" : tool, start: p, end: p, points: [p], node, view: viewport(), page: page(), createdAt: new Date().toISOString() };
    surface.setPointerCapture(e.pointerId); schedule();
  });
  surface.addEventListener("pointermove", e => {
    if (busy) return;
    if (!gesture) { hover = elementAt(e.clientX, e.clientY); schedule(); return; }
    stop(e); gesture.end = point(e);
    const last = gesture.points.at(-1);
    if (Math.hypot(last.x - e.clientX, last.y - e.clientY) > 2 && gesture.points.length < 5000) gesture.points.push(point(e));
    schedule();
  });
  surface.addEventListener("pointercancel", () => { gesture = null; schedule(); });
  surface.addEventListener("pointerup", async e => {
    if (!gesture) return;
    stop(e);
    const g = gesture; gesture = null; hover = null;
    if (surface.hasPointerCapture(e.pointerId)) surface.releasePointerCapture(e.pointerId);
    const distance = Math.hypot(e.clientX - g.start.x, e.clientY - g.start.y);
    const kind = g.kind === "auto" ? (distance < 5 ? "element" : "region") : g.kind;
    if ((kind === "ink" && g.points.length < 2) || (kind === "region" && distance < 5) || (kind === "element" && !g.node)) { schedule(); return; }
    if (!sameView(g.view, viewport()) || g.page.url !== location.href) { status("The page moved while drawing. Please mark it again.", true); schedule(); return; }
    const geometry = kind === "element" ? box(g.node) : kind === "ink" ? { points: g.points } : rect(g.start, point(e));
    const mark = { id: crypto.randomUUID(), kind, geometry, createdAt: g.createdAt, page: g.page, viewport: viewport(), element: kind === "element" ? describe(g.node) : null };
    busy = true; stableViewport = viewport(); update(); status("Saving reference…");
    // The surface blocks page clicks until capture completes; only the visuals hide.
    svg.style.visibility = "hidden"; dock.style.visibility = "hidden"; hint.style.visibility = "hidden";
    bridgeLabel.style.visibility = "hidden";
    surface.classList.add("armed");
    try {
      if (!sheet || sheet.url !== location.href) sheet = await send("open", { page: page() });
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const saved = await send("mark", { mark });
      sheet = saved;
      anchors.set(mark.id, { node: g.node, stale: !sameView(stableViewport, viewport()) });
      if (kind === "element" && g.node) resizeObserver.observe(g.node);
      if (!sameView(stableViewport, viewport())) status("Page moved during capture. Check this reference in Sheets before using it.", true);
      else status(`Reference ${sheet.marks.at(-1).number} saved · keep talking, or make another mark`);
    } catch (err) { status(err.message, true); }
    finally { busy = false; stableViewport = null; svg.style.visibility = ""; dock.style.visibility = ""; hint.style.visibility = ""; bridgeLabel.style.visibility = ""; update(); }
  });
  // Prevent the click following pointerup from reaching a page handler, even if
  // the hold key was released during the gesture. Leave ordinary page clicks alone.
  window.addEventListener("click", e => { if (suppressClick && !e.composedPath().includes(dock)) { stop(e); suppressClick = false; } }, true);
  window.addEventListener("pointerdown", e => { if (!ownEvent(e)) suppressClick = false; }, true);
  window.addEventListener("wheel", e => { if (gesture || busy) stop(e); }, { capture: true, passive: false });
  window.addEventListener("scroll", e => {
    if (e.target !== document) for (const anchor of anchors.values()) anchor.stale = true;
    schedule();
  }, true);
  window.addEventListener("resize", () => {
    for (const anchor of anchors.values()) anchor.stale = true;
    schedule();
  });
  // Track element layout through React updates / animations without rebinding old nodes.
  const observer = new MutationObserver(() => {
    for (const anchor of anchors.values()) anchor.stale = true;
    schedule();
  });
  observer.observe(document.body || document.documentElement, { childList: true, subtree: true, attributes: true, characterData: true });
  const resizeObserver = new ResizeObserver(schedule);
  resizeObserver.observe(document.documentElement);
  root.querySelector("select").addEventListener("change", e => { modifier = e.target.value; held = false; e.target.blur(); update(); defaultHint(); });
  root.addEventListener("click", e => {
    const button = e.target.closest("button");
    if (!button || button.disabled) return;
    if (button.dataset.tool) { tool = button.dataset.tool; held = false; hover = null; update(); defaultHint(); }
    else perform(button.dataset.action);
  });
  async function perform(action) {
    if (busy || !sheet || (bridgeLocked && action !== "review")) return;
    if (action === "hide") return toggle();
    busy = true; update();
    try {
      if (action === "undo" && sheet.marks.length) { anchors.delete(sheet.marks.at(-1).id); sheet = await send("undo"); defaultHint(); }
      if (action === "review") await send("review");
      if (action === "finish") {
        const result = await send("finish", { page: page() });
        sheet = result.next; anchors.clear(); tool = "browse"; held = false;
        status(`Iteration ${result.finished.iteration} saved in Sheets. A fresh sheet is ready.`);
      }
    } catch (err) { status(err.message, true); }
    finally { busy = false; update(); }
  }
  const ready = send("open", { page: page() }).then(value => { sheet = value; update(); defaultHint(); }).catch(err => { status(err.message, true); throw err; });
  ready.catch(() => {});
  setInterval(async () => {
    if (!sheet || busy || gesture || bridgePolling) return;
    bridgePolling = true;
    try {
      const state = await send("bridge", { active: visible && !document.hidden && document.hasFocus() });
      if (state.phase !== "ready" || !bridgeLabel.textContent) bridgeLabel.textContent = state.message || "";
      bridgeLocked = state.phase === "finishing";
      if (bridgeLocked) { tool = "browse"; held = false; hover = null; }
      if (state.next) { sheet = state.next; anchors.clear(); tool = "browse"; bridgeLocked = false; defaultHint(); }
      update();
    } catch (error) {
      bridgeLabel.textContent = "Phone bridge offline · marks still saved in Sheets";
    } finally { bridgePolling = false; }
  }, 750);
})();
