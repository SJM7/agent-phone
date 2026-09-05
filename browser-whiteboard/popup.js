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
init().catch(error => status.textContent = error.message);
