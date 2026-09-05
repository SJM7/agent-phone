const $ = s => document.querySelector(s);
let current, dirty = false, loading = 0;
const canvases = new Map();
async function send(type, extra = {}) {
  const r = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type, ...extra });
  if (!r?.ok) throw new Error(r?.error || "Could not reach whiteboard.");
  return r.value;
}
function text(tag, value, className) { const el = document.createElement(tag); el.textContent = value; if (className) el.className = className; return el; }
function status(value) { $("#status").textContent = value; }
async function saveNotes() {
  if (!current || !dirty) return;
  current = await send("notes", { id: current.id, narration: $("#notes").value });
  dirty = false; $("#note-status").textContent = "Saved locally";
}
async function refreshList() {
  const sheets = await send("list");
  $("#sheets").replaceChildren();
  for (const s of sheets) {
    const button = text("button", "", "sheet" + (s.id === current?.id ? " selected" : ""));
    button.append(text("strong", `Iteration ${String(s.iteration).padStart(2, "0")} · ${s.finishedAt ? "Saved" : "Draft"}`), text("span", s.title), text("span", `${s.count} references · ${new Date(s.createdAt).toLocaleString()}`));
    button.addEventListener("click", () => run(async () => { await saveNotes(); location.hash = s.id; }));
    $("#sheets").append(button);
  }
  return sheets;
}
async function makeCanvas(mark) {
  const img = new Image(); img.src = mark.image; await img.decode();
  const canvas = document.createElement("canvas"); canvas.width = img.naturalWidth; canvas.height = img.naturalHeight;
  canvas.setAttribute("role", "img"); canvas.setAttribute("aria-label", `Reference ${mark.number}: ${mark.kind} on ${mark.page.title}`);
  const ctx = canvas.getContext("2d"); ctx.drawImage(img, 0, 0);
  AgentWhiteboardCore.paint(ctx, mark, canvas.width / mark.viewport.width);
  return canvas;
}
function download(blob, name) {
  const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
const png = canvas => new Promise((resolve, reject) => canvas.toBlob(b => b ? resolve(b) : reject(new Error("Could not encode screenshot.")), "image/png"));
async function load(id) {
  const generation = ++loading;
  await saveNotes();
  const sheet = await send("get", { id });
  if (generation !== loading) return;
  current = sheet; canvases.clear(); dirty = false;
  $("#title").textContent = `Iteration ${String(sheet.iteration).padStart(2, "0")}`;
  $("#source").textContent = `${sheet.title} · ${sheet.url}`;
  $("#phase").textContent = sheet.finishedAt ? "SAVED VISUAL REFERENCES" : "DRAFT · FINISH ON THE PAGE WHEN READY";
  $("#notes").value = sheet.narration; $("#note-status").textContent = "";
  $("#empty").hidden = true; $("#detail").hidden = false;
  $("#delete").disabled = !sheet.finishedAt; $("#export").disabled = true;
  $("#marks").replaceChildren();
  for (const mark of sheet.marks) {
    const canvas = await makeCanvas(mark);
    if (generation !== loading) return;
    canvases.set(mark.id, canvas);
    const card = text("article", "", "mark"), head = document.createElement("header"), info = document.createElement("div");
    info.append(text("h2", mark.element?.text?.slice(0, 80) || ({ element: "Element", region: "Region boundary", ink: "Freehand sketch" })[mark.kind]), text("p", `${mark.viewport.width} × ${mark.viewport.height} · ${new Date(mark.createdAt).toLocaleTimeString()}`));
    const save = text("button", "Save PNG"); save.addEventListener("click", () => run(async () => download(await png(canvas), `mark-${mark.number}.png`)));
    head.append(text("span", String(mark.number), "number"), info, save);
    const context = text("div", "", "context");
    if (mark.element) context.append(text("code", mark.element.selector), text("div", mark.element.shadowRoot ? "Inside a shadow root; selector is relative to that root." : "Selector captured at marking time."));
    else context.textContent = "This boundary belongs to the captured view. It is not automatically reapplied to later layouts.";
    card.append(head, canvas, context); $("#marks").append(card);
  }
  $("#export").disabled = !sheet.marks.length;
  await refreshList();
}
async function run(fn) { try { status(""); await fn(); } catch (err) { status(err.message); } }
$("#notes").addEventListener("input", () => { dirty = true; $("#note-status").textContent = "Unsaved"; });
$("#save").addEventListener("click", () => run(saveNotes));
$("#copy").addEventListener("click", () => run(async () => {
  await saveNotes(); await navigator.clipboard.writeText(AgentWhiteboardCore.summary(current)); status("Brief copied. Attach the PNGs too, or give your agent the extracted export folder.");
}));
$("#export").addEventListener("click", () => run(async () => {
  $("#export").disabled = true;
  try {
    await saveNotes(); status("Packaging screenshots and context…");
    const files = [{ name: "brief.md", data: AgentWhiteboardCore.summary(current) }];
    const { tabId, marks, ...metadata } = current;
    files.push({ name: "context.json", data: JSON.stringify({ ...metadata, marks: marks.map(({ image, ...mark }) => ({ ...mark, image: `mark-${mark.number}.png` })) }, null, 2) });
    for (const mark of marks) files.push({ name: `mark-${mark.number}.png`, data: await png(canvases.get(mark.id)) });
    download(await WhiteboardZip(files), `whiteboard-iteration-${current.iteration}-${current.id.slice(0, 8)}.zip`);
    status("Export ready. Extract the ZIP into your project; ask the model to read brief.md and the numbered PNGs.");
  } finally { $("#export").disabled = false; }
}));
$("#delete").addEventListener("click", () => run(async () => {
  if (!confirm("Delete this saved sheet and its screenshots from this browser? Export it first if you want a copy.")) return;
  await send("delete", { id: current.id }); dirty = false; current = null;
  const sheets = await refreshList();
  if (sheets[0]) location.hash = sheets[0].id;
  else { history.replaceState(null, "", "review.html"); location.reload(); }
}));
window.addEventListener("hashchange", () => { if (location.hash.length > 1) run(() => load(location.hash.slice(1))); });
window.addEventListener("beforeunload", e => { if (dirty) { e.preventDefault(); e.returnValue = ""; } });
run(async () => {
  const sheets = await refreshList();
  const id = location.hash.slice(1) || sheets[0]?.id;
  if (id) await load(id);
});
