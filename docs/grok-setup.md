# Grok Build setup

[Grok Build](https://x.ai) works with Agent Phone the same way Claude Code
does: bind with `#`, lamp blinks when a turn finishes, `*` focuses, and
the receiver drives **native** dictation — not local whisper.

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
instead of Claude's Space. Confirm the file loaded with `/hooks`.

Grok's hook JSON uses camelCase (`sessionId`); the daemon accepts that
and Claude's `session_id`. `Stop` also fires an observe-only event at
session end and inside subagents — the daemon ignores those so the lamp
only blinks on a real main-agent turn (`reason` is `end_turn`).

If Claude Code hooks are already on this machine, Grok may load them too
(Claude compatibility is on by default). Tty detection still picks the
Grok binary for dictation; the Grok-native file above is what you want
for the `?agent=grok` tag.

## 2. Voice dictation through the receiver

Grok transcribes through xAI speech-to-text (the system default mic), not
whisper.cpp. The phone should already be the default input when the CX300
is plugged in (System Settings, Sound, Input).

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

To try local Whistle instead of xAI speech-to-text, start the daemon with
`--stt whistle` (python at `~/.agent-phone/whistle-venv`). A Grok terminal
then records the handset, a warm Whistle process transcribes while you
talk, and hang-up pastes the text once. F8 is not held on that path.
Codex and Hermes use the same worker. Whisper remains the fallback if
the worker fails. Bindings reset on restart, so press `#` again.

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
