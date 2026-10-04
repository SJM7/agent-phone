# Grok Build setup

[Grok Build](https://x.ai) works with Agent Phone the same way Claude Code
does: bind with `#`, lamp blinks when a turn finishes, `*` focuses, and
the receiver drives **native** dictation unless the daemon was started
with `--stt whistle`, which pastes a local transcript instead of holding
F8. The headset button always records and pastes, including in a Grok
terminal.

Grok has hold-to-talk (`Ctrl+Space` or `F8`) and Claude-shaped
`Stop` / `UserPromptSubmit` hooks, so the same `hooks/agent-phone-hook.sh`
script is enough.

## 1. Hook configuration

Grok reads `~/.grok/hooks/*.json`. Create
`~/.grok/hooks/agent-phone.json` (adjust the repo path):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh stop grok",
            "timeout": 5
          }
        ]
      }
    ],
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh user-prompt-submit grok",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

The `grok` argument tags the harness so the daemon holds **F8**
instead of Claude's Space, unless `--stt whistle` is on, in which case
the handset pastes and does not hold F8. Confirm the file loaded with
`/hooks`.

Grok's hook JSON uses camelCase (`sessionId`); the daemon accepts that
and Claude's `session_id`. `Stop` also fires an observe-only event at
session end and inside subagents — the daemon ignores those so the lamp
only blinks on a real main-agent turn (`reason` is `end_turn`).

If Claude Code hooks are already on this machine, Grok may load them too
(Claude compatibility is on by default). Tty detection still picks the
Grok binary for dictation; the Grok-native file above is what you want
for the `?agent=grok` tag.

## 2. Voice dictation through the receiver

On the F8 path, Grok transcribes through xAI speech-to-text from the
macOS default input, not whisper.cpp. The daemon does not change that
default. Point System Settings → Sound → Input at Polycom CX300 when you
want the handset mic. With `--stt whistle`, the handset opens that device
by name instead.

One-time Grok settings — both live in `~/.grok/config.toml`:

```toml
[ui]
voice_capture_mode = "hold"
voice_keybind_enabled = true    # Ctrl+Space / F8
```

AppleScript cannot `key down` a Control+Space chord (syntax error -2740),
so the daemon holds **F8** (Grok's other binding, key code 100) instead.
Lift = F8 down, hang-up = F8 up. Esc also cancels Grok voice.

Then: `*` to the terminal, lift, speak, hang up, Redial. The transcript
lands in the prompt for review; hang-up does not send.

To use local Whistle instead of xAI speech-to-text, start the daemon with
`--stt whistle`. The interpreter is
`~/.agent-phone/whistle-venv/bin/python` (see
[Getting started](getting-started.md#2-run-the-daemon)). A Grok handset
then records the Polycom CX300 by name. The worker transcribes while you
talk, and hang-up pastes once. F8 is not held. Codex and Hermes share
that worker when the flag is on. The headset button uses it too, and
always pastes. If the worker fails, the daemon falls back to
`--stt-command`. Bindings reset on any restart, so press `#` again.

Press F8 from the keyboard once to confirm Grok voice works. The daemon
uses this binding to avoid macOS Control+Space input-source switching.

Grant the **terminal app that hosts Grok** Microphone permission on first
use. `/doctor` while voice is on shows which input Grok would record.

## 3. Testing manually

With the daemon running (default port 8489):

```sh
echo '{"sessionId": "test-session", "hook_event_name": "Stop", "reason": "end_turn"}' \
  | /Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh stop grok
```

`curl http://127.0.0.1:8489/health` should return `{"ok": true}`.
