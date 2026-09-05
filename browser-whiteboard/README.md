# Browser whiteboard

A transparent review sheet over a live webpage. Point to an element, draw a
boundary, or sketch while dictating into Agent Phone. Each numbered reference
saves its own clean viewport screenshot and visual mark, with a timestamp,
URL, viewport, and (for elements) a selector hint and short text excerpt.

This is an independent, local-only Chromium extension. It requires no server,
build, account, or changes to your web application or phone daemon.

## Load it

1. Open `chrome://extensions` (or the equivalent in your Chromium browser).
2. Enable **Developer mode**, click **Load unpacked**, and select this
   `browser-whiteboard` directory.
3. Open the page you want to review. Click the extension icon, then choose
   **Point**, **Box**, or **Ink**. Pin it for easy access. `Alt+Shift+W` opens these controls; edit that
   shortcut at `chrome://extensions/shortcuts` if it conflicts.

To try a sample, run from this directory:

```sh
python3 -m http.server 8766 --bind 127.0.0.1
```

Then open **http://127.0.0.1:8766/demo.html** and activate the extension.

## Mouse controls

### Corsair Scimitar side buttons

In Corsair iCUE, select the Scimitar and assign these keystroke combinations
to three side buttons. On Mac, **Control means ⌃, not Command (⌘)**:

| Side button | Send | Whiteboard action |
| --- | --- | --- |
| 1 | Control + Shift + 7 | Point tool on/off |
| 2 | Control + Shift + 8 | Box tool on/off |
| 3 | Control + Shift + 9 | Ink tool on/off |
| 4 (optional) | Option + Shift + W | Open whiteboard controls |

These are single presses. Press a side button, use your normal left mouse
button to mark/draw, and press the same side button to return to browsing.
They can activate the extension on a fresh page and do not depend on the
page receiving a held modifier. Chrome must be focused.

Check the actual registered bindings at `chrome://extensions/shortcuts`.
Chrome may leave a shortcut unassigned if it conflicts with another extension.
The same page exposes optional commands for undo, finishing a sheet, and
opening sheets; you can assign shortcuts to those and map more mouse buttons.

Use iCUE's **Device Memory Mode** to save supported assignments to the mouse
if you want them to work without iCUE running. This extension does not program
Corsair firmware; a label such as “button 1” does not guarantee that button
already emits a key. Corsair's instructions:
[Scimitar remapping](https://help.corsair.com/hc/en-us/articles/360041744451-Assign-key-remaps-and-macros-for-the-Scimitar-RGB-Elite)
and [onboard assignments](https://help.corsair.com/hc/en-us/articles/5052849588493-iCUE-How-to-set-up-onboard-key-assignments-in-iCUE).

After updating this extension, reload it in `chrome://extensions` and refresh
your working page once. The toolbar and registered shortcuts should now be
version 0.2.1. The icon popup includes a **Shortcut check** with the last
command received, its result, and Chrome's actual bindings. If a shortcut
appears to do nothing, open the icon popup and inspect that result. You can
also choose Ink directly there to test drawing independently of mouse macros.

### Optional held modifier

Start in **Browse**, where normal clicks and scrolling go through to the page.
Hold **Option (⌥)** on Mac or **Alt** on Windows/Linux. You can also map a
programmable mouse button to that held modifier, with key-up on release
(a one-shot key macro will not hold marking mode). While holding it:

- Click: numbered element reference.
- Drag: rectangular region reference.

If Option/Alt conflicts with your workflow, choose **Hold F8** in the dock and
map a mouse button to held F8 instead. The dock's **Point**, **Box**, and **Ink** buttons also
provide persistent tools without needing a held key.

- **Ink:** drag a freehand stroke. Each completed stroke is one reference.
- **Escape / Browse:** return to normal page interaction.
- **Undo:** remove the latest reference. Cmd/Ctrl+Z also works while a drawing
  tool or the hold key is active, except inside text inputs.
- **×:** hide both marks and dock; reopen by selecting a tool in the icon popup.
- **Finish sheet:** save the current visual iteration and start a clean sheet.
- **Sheets:** open the archive, add narration, and export.

Reference numbers are never reused within a sheet after Undo: if you have
already spoken about reference 3, a later mark must not silently become 3.

## Handoff to the model

1. Narrate normally with the phone: “Reference 1 needs more space. Keep the
   change inside region 2.” There is no automatic phone-transcript binding yet.
2. Finish the sheet. Open **Sheets** and select the saved iteration.
3. Optionally paste the transcript into **Your narration** and save it.
4. **Export sheet .zip**, extract it into your project, and tell your agent:

   “Read `path/to/sheet/brief.md` and inspect its numbered PNGs. Use these
   references alongside my instructions.”

The ZIP contains `brief.md`, `context.json`, and a PNG for each reference with
its numbered mark composited over the captured page. Individual PNGs can also
be saved or attached to a conversation. **Copy model brief** copies text only;
it does not place images on the clipboard or send anything to the model.

Screenshots and geometry in finished sheets are immutable. Narration can be
added later without altering the visual evidence. Exports of unfinished drafts
are allowed and labeled as drafts.

## How references behave

- Element outlines follow their actual DOM nodes during scroll and layout
  changes. A replaced node is not rebound automatically to a new element with
  the same selector. The original screenshot remains in Sheets.
- Boxes and ink are spatial marks, not semantic element groups. They move with
  document scrolling, but are hidden after detected DOM changes, nested
  scrolling, or viewport resize to avoid suggesting they still describe the
  changed layout. The saved view remains available in Sheets.
- After a full reload, reactivate the extension. It resumes the draft for that
  tab and URL; earlier references are available in Sheets rather than placed
  over a potentially different page.
- Screenshots are taken after the pointer is released, with the dock/overlay
  hidden. This is not a video freeze: animations can advance during capture.
  Mark and capture timestamps are both recorded. Stay on the tab until saved.
  Captures are spaced to respect Chrome's screenshot rate limit. Viewport or
  tab changes during capture fail rather than attaching an unrelated view.
- Each screenshot covers the visible viewport only. Scroll to the next region
  and create another reference to include it.

## Phone bridge (opt-in prototype)

Version 0.3.2 restores an enabled whiteboard automatically after a same-origin
page refresh, resuming the saved draft and phone connection. Pairing is stored in
extension-local storage, not the webpage; the popup now reports saved connection
status separately from its intentionally blank token-entry field. **Hide** disables
automatic restoration for that tab until you enable the tools again. A new site
still requires activation; no additional host permissions were added. This follows
[Chrome's same-origin activeTab access](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab).

Start the daemon with `--voice record --whiteboard-dir ~/.agent-phone/handoffs`.
In the extension popup, expand **Connect phone**, paste the contents of
`~/.agent-phone/handoffs/bridge-token`, and connect once. Reload the extension
and working page after upgrading to 0.3.0; approve loopback site access if asked.

1. Focus the intended terminal prompt and press **#** on the phone once. Phone
   speed-dial digits and `*` also select the destination when they focus a terminal.
2. Return to the page and enable the whiteboard. Wait for **Phone bridge ready**.
3. Lift the receiver, draw, and dictate. The badge confirms **Recording + whiteboard**.
4. Hang up with the browser still open. The bridge finishes any pending capture,
   freezes the sheet, transcribes locally, and writes a UUID-named handoff folder.
5. The selected terminal is brought forward and the transcript plus absolute
   `brief.md` path is pasted, **not submitted**. Press Redial to send while that
   terminal remains focused. Returning to the page exposes a clean next sheet.

The destination is selected explicitly, not inferred from old saved bindings, and
is pinned per recording. A window/tab/TTY mismatch prevents pasting. No destination,
transcription failure, or missing screenshots produces a failure badge and retains
recoverable files; the system never silently pastes into the browser instead.
The badge reports local paste success, not confirmation that the model read it.

Each handoff contains `brief.md`, `narration.txt`, `references.md`, `context.json`,
annotated `mark-N.png` files, `session.json`, and `status.json`. No ZIP export or
manual narration entry is needed. Existing sheets remain in the archive.
Keep the page open while finishing. The bridge supports one recording at a time;
wait for completion before lifting the receiver again. Selecting another browser
tab does not switch the sheet already pinned to a recording. Polling is 750 ms;
hang-up freezes when the page next services the request, after any in-flight mark.
No exact word-to-stroke audio alignment is claimed.

The bridge forces local recording for its sessions, including Claude Code, so the
transcript and visuals can be bundled. Other phone sessions retain their existing
voice behavior. Omit `--whiteboard-dir` to restore the ordinary phone-only daemon.

The reusable skill source is `skills/agent-phone-whiteboard/SKILL.md` at the repo
root. Install it in `~/.agents/skills/agent-phone-whiteboard/` for Codex and
`~/.claude/skills/agent-phone-whiteboard/` for Claude Code. The prompt also contains
basic reading instructions so the bundle does not depend on skill discovery.
These are local-file workflows: remote agents need an explicit file transfer or
shared filesystem. The bridge does not upload evidence to a model provider.

Skill discovery references: [Codex](https://learn.chatgpt.com/docs/build-skills)
and [Claude Code](https://code.claude.com/docs/en/skills).

## Limits of this first version

- Chromium browsers only; it has not been ported to Safari/Firefox.
- Browser internal pages, the Chrome Web Store, and other protected pages
  cannot be annotated. File URLs require explicitly allowing file access.
- Cross-origin iframes and closed shadow roots can be marked visually, but
  their internal element metadata is unavailable. Input capture over frames
  depends on activating a tool or holding the modifier before entering them.
- Browser fullscreen/top-layer dialogs may cover the overlay. Touch/pinch
  zoom and mobile emulation are not a validated input workflow yet.
- Screenshots are separate captured moments; freehand strokes do not yet
  combine into a single multi-stroke sketch screenshot.
- No automatic submission, exact speech alignment, background terminal input,
  or app-specific breakpoint macros. Terminal routing requires the opt-in bridge.

## Storage and permissions

`activeTab` allows access only when you invoke the extension on a page.
`scripting` injects the overlay. `storage` and `unlimitedStorage` keep potentially
large screenshot sheets locally without the normal 10 MB extension quota.
The only persistent host permission is `http://127.0.0.1/*` (Chrome match patterns
cannot restrict it to one port). Code sends only to the authenticated bridge on
port 8489, after one-time pairing. There are no remote uploads. The bridge binds
loopback, requires a random bearer token, rejects ordinary website origins, and
limits request bodies to 64 MiB. Keep the token private; any local process with
access to it is trusted. Token storage is restricted to extension contexts.

Captures include the visible page as you see it. Finish and delete unwanted
sheets from the archive to reclaim disk space. Export before removing the
extension or clearing its storage. A sheet holds up to 100 references.

## Verification

No dependencies are needed to load the extension. For development only:

```sh
npm ci
npx playwright install chromium
npm test
```

The integration test runs in a disposable Chromium profile, verifies ordinary
clicks versus marking, all three tools, undo numbering, reload persistence,
scroll/replacement behavior, finishing an iteration, narration, and ZIP/PNG
handoff. The disposable test manifest adds host permission to substitute for
the toolbar click that grants `activeTab`; production permissions stay narrow.
The same test also runs an isolated real Python bridge, verifies pairing, receiver
state transitions, annotated PNG handoff, and the next iteration. Terminal pasting
is stubbed in unit tests, not performed against your live prompt during testing.
Screenshots and a sample ZIP are written under ignored `test-results/`.
