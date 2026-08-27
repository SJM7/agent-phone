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

## 3. Testing manually

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
