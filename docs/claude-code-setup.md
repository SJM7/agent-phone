# Claude Code setup

Wire Claude Code's `Stop` and `UserPromptSubmit` hooks to the agent-phone daemon so the
phone LED can blink when a terminal's LLM turn finishes (and clear when a new turn starts).

## 1. Hook configuration

Add this to the `hooks` section of `~/.claude/settings.json` (adjust the repo path):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh stop",
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
            "command": "/Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh user-prompt-submit",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

Notes on the schema (per <https://code.claude.com/docs/en/hooks>):

- `Stop` and `UserPromptSubmit` have **no matcher support** — they always fire, so the
  `matcher` field is omitted.
- Each entry is an object with a `hooks` array of `{"type": "command", "command": ...}`.
- The script exits 0 unconditionally and curls with `--max-time 2`, so a stopped daemon
  never blocks or breaks the Claude Code session.

## 2. What the hooks send

Claude Code writes the hook input as JSON to the script's stdin; the script forwards it
verbatim to `http://127.0.0.1:8489/hook/stop` or `/hook/user-prompt-submit`.

Both events carry the common fields `session_id`, `transcript_path`, `cwd`,
`permission_mode`, and `hook_event_name` (`"Stop"` / `"UserPromptSubmit"`);
`UserPromptSubmit` adds `prompt` (the submitted text) and `Stop` adds
`last_assistant_message`.

Agent-phone cares about **`session_id`**: it is stable for the life of a terminal's
Claude Code session and is what the daemon uses to identify which bound terminal
finished (or started) a turn. `hook_event_name` and `cwd` are useful for
debugging/labels; the rest is ignored.

## 3. Voice dictation through the receiver

Claude Code has native voice dictation (v2.1.69+, [docs](https://code.claude.com/docs/en/voice-dictation.md)).
The daemon's default `--voice claude` mode drives it from the phone: lifting
the receiver holds Claude Code's push-to-talk key, hanging up releases it —
the transcript lands in the prompt box for review, and you press Enter on the
keyboard to send. That is the whole flow: `*` to the terminal that needs you,
lift, speak, hang up, Enter.

One-time setup:

1. In a Claude Code session run `/voice hold` once to enable hold-to-record
   mode (persists via settings). Leave `autoSubmit` off so hang-up never
   sends anything by itself.
2. Grant your terminal app Microphone permission when macOS prompts on the
   first `/voice` use. Voice requires signing in with a Claude.ai account
   (API-key auth doesn't have it).
3. Make sure the phone is the system default input (macOS usually does this
   automatically when a USB audio device with a mic is attached: System
   Settings, Sound, Input, "Polycom CX300"). Claude Code records from the
   system default, so this routes the handset mic into it.

The push-to-talk key defaults to Space; the daemon holds Space accordingly.
If Space bothers you (it types spaces when a prompt already has text),
rebind `voice:pushToTalk` in `~/.claude/keybindings.json` and pass the same
key to the daemon with `--dictation-key`.

Codex CLI note: Codex currently has no native voice input (experimental
voice shipped in v0.105 and was removed in v0.118), so for Codex-only
terminals run the daemon with `--voice record --stt-command '...'` to use
local transcription and clipboard paste instead.

## 4. Testing manually

With the daemon running (default port 8489):

```sh
curl -i http://127.0.0.1:8489/health

curl -i -X POST http://127.0.0.1:8489/hook/stop \
  -H 'Content-Type: application/json' \
  -d '{"session_id": "test-session", "transcript_path": "/tmp/t.jsonl", "cwd": "/tmp", "hook_event_name": "Stop"}'

curl -i -X POST http://127.0.0.1:8489/phone/event \
  -H 'Content-Type: application/xml' \
  -d '<PolycomIPPhone><OffHookEvent><PhoneIP>192.168.1.50</PhoneIP><MACAddress>0004f2abcdef</MACAddress><TimeStamp>2026-08-27T18:00:00</TimeStamp><LineNumber>1</LineNumber></OffHookEvent></PolycomIPPhone>'
```

`/health` returns `200 {"ok": true}`; the two POSTs return `204 No Content` and fire the
daemon's callbacks. You can also exercise the hook script itself:

```sh
echo '{"session_id": "test-session", "hook_event_name": "Stop"}' \
  | /Users/teloshome/Programming/agent-phone/hooks/agent-phone-hook.sh stop
```
