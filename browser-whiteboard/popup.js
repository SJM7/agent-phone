let target;
document.querySelector("#pair").addEventListener("click", async () => {
  try {
    const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "pair", token: document.querySelector("#bridge-token").value });
    if (!result?.ok) throw new Error(result?.error || "Pairing failed");
    document.querySelector("#bridge-token").value = "";
    status.textContent = "Phone connected. Select a terminal with #, then return to the page.";
  } catch (e) { status.textContent = e.message; }
});
const status = document.querySelector("#status");
const reviewStatus = document.querySelector("#review-status");
const reviewArm = document.querySelector("#review-arm");
const reviewBookmark = document.querySelector("#review-bookmark");
const reviewStop = document.querySelector("#review-stop");
function reviewText(review) {
  const state = review?.state || "idle";
  if (state === "armed") return "Review armed for this pinned tab. Start handset recording to begin video.";
  if (state === "recording") return "Recording this tab’s video. Bookmark moments from here, the page, or your assigned shortcut.";
  if (state === "upload-failed") return "Review stopped; assets remain on this computer for retry. " + (review.error || "");
  if (state === "failed") return "Review capture failed: " + (review.error || "Try arming again.");
  if (state === "stopped") return "Review stopped" + (review.error ? ": " + review.error : ".");
  return "No review is armed for this tab.";
}
async function refreshReview() {
  const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "review-status" });
  const review = result?.value || { state: "idle" };
  const ownsTab = review.tabId === target?.id;
  reviewStatus.textContent = reviewText(review);
  reviewStatus.className = review.state === "recording" ? "review-recording" : review.state === "failed" || review.state === "upload-failed" ? "review-error" : "";
  reviewArm.disabled = !/^https?:\/\//.test(target?.url || "") || ["arming", "armed", "recording"].includes(review.state);
  reviewBookmark.disabled = !(ownsTab && review.state === "recording");
  reviewStop.disabled = !(ownsTab && ["arming", "armed", "recording"].includes(review.state));
}
async function init() {
  chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "connection" })
    .then(result => { document.querySelector("#connection").textContent = result?.value?.message || result?.error || "Unable to check pairing"; })
    .catch(error => { document.querySelector("#connection").textContent = error.message; });
  [target] = await chrome.tabs.query({ active: true, currentWindow: true });
  document.querySelector("#page").textContent = target?.url || "No accessible page URL. Click the extension icon on your working page.";
  const last = (await chrome.storage.session.get("lastCommand")).lastCommand;
  if (last) document.querySelector("#last").textContent = `Last command: ${last.command}\n${new Date(last.at).toLocaleTimeString()} · ${last.status}${last.error ? "\n" + last.error : ""}`;
  const commands = await chrome.commands.getAll();
  document.querySelector("#keys").textContent = commands.filter(c => c.name.startsWith("toggle-")).map(c => `${c.name.replace("toggle-", "")}: ${c.shortcut || "NOT ASSIGNED"}`).join("\n");
  if (!/^https?:\/\//.test(target?.url || "")) {
    status.textContent = "Open an ordinary website first. Chrome’s New Tab and Extensions pages cannot be annotated.";
    for (const b of document.querySelectorAll("[data-command]")) b.disabled = true;
  }
  await refreshReview();
}
document.querySelectorAll("[data-command]").forEach(button => button.addEventListener("click", async () => {
  const buttons = [...document.querySelectorAll("[data-command]")]; buttons.forEach(b => b.disabled = true);
  status.textContent = "Activating…";
  try {
    const response = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "control", command: button.dataset.command, tabId: target.id });
    if (!response?.ok || !response.value?.ok) throw new Error(response?.error || response?.value?.error || "No response from extension.");
    window.close();
  } catch (error) { status.textContent = error.message; buttons.forEach(b => b.disabled = false); }
}));
document.querySelector("#shortcuts").addEventListener("click", () => chrome.tabs.create({ url: "chrome://extensions/shortcuts" }));
reviewArm.addEventListener("click", async () => {
  reviewArm.disabled = true; reviewStatus.textContent = "Arming review capture for this tab…";
  try {
    const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "review-arm", tabId: target.id });
    if (!result?.ok) throw new Error(result?.error || "Could not arm review capture.");
  } catch (error) { reviewStatus.textContent = error.message; reviewStatus.className = "review-error"; }
  await refreshReview();
});
reviewBookmark.addEventListener("click", async () => {
  reviewBookmark.disabled = true; reviewStatus.textContent = "Saving bookmark frames…";
  try {
    const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "review-bookmark", tabId: target.id });
    if (!result?.ok) throw new Error(result?.error || "Could not save a review bookmark.");
  } catch (error) { reviewStatus.textContent = error.message; reviewStatus.className = "review-error"; }
  await refreshReview();
});
reviewStop.addEventListener("click", async () => {
  reviewStop.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ channel: "agent-whiteboard", type: "review-stop" });
    if (!result?.ok) throw new Error(result?.error || "Could not stop review capture.");
  } catch (error) { reviewStatus.textContent = error.message; reviewStatus.className = "review-error"; }
  await refreshReview();
});
init().catch(error => status.textContent = error.message);
