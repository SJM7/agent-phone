# Hermes Agent setup

[Hermes](https://hermes-agent.nousresearch.com) (Nous Research) works with
Agent Phone like the other harnesses: bind with `#`, lamp blinks when a turn
finishes, receiver dictates via local whisper (Hermes has no native TUI
dictation, so it gets the same record-and-paste flow as Codex).

## Hooks

Hermes supports shell hooks declared in `~/.hermes/config.yaml`: scripts
receive a JSON payload on stdin with `session_id`, `cwd`, and
`hook_event_name` — deliberately Claude Code-compatible, so the same
`hooks/agent-phone-hook.sh` serves it too.

One wrinkle: Hermes has no single "turn finished" event. Instead its
lifecycle events map cleanly onto one: `pre_llm_call` and `pre_tool_call`
mean the agent is still working, and a `post_llm_call` that nothing follows
is the end of the turn. The daemon debounces accordingly — any activity
cancels a pending turn-end, and after ~4 quiet seconds the lamp fires.

Append to `~/.hermes/config.yaml` (adjust the repo path):

```yaml
hooks:
  pre_llm_call:
    - command: "/path/to/agent-phone/hooks/agent-phone-hook.sh user-prompt-submit hermes"
      timeout: 5
  pre_tool_call:
    - command: "/path/to/agent-phone/hooks/agent-phone-hook.sh user-prompt-submit hermes"
      timeout: 5
  post_llm_call:
    - command: "/path/to/agent-phone/hooks/agent-phone-hook.sh stop hermes"
      timeout: 5
```

Hermes asks for first-use consent per hook (its `hooks` security model);
approve once when prompted, start with `hermes --accept-hooks`, or set
`hooks_auto_accept: true`. Verify with `hermes hooks list` / `hermes hooks
doctor`.

## Dictation

Same as [Codex](codex-setup.md): the daemon detects `hermes` on the
terminal's tty, records the handset on receiver-lift, transcribes locally
with whisper.cpp, and pastes the transcript for review. Enter (or the
phone's Redial key) sends.
