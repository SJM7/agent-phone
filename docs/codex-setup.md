# Codex CLI setup

Codex works with Agent Phone the same way Claude Code does — bind with `#`,
lamp blinks when a turn finishes, `*` focuses, receiver dictates — with one
difference in how dictation lands.

## Hooks

Codex gained a Claude-Code-style hooks system in 2026 (stable, enabled by
default): `UserPromptSubmit` and `Stop` events, hook commands receive JSON
on stdin with `session_id`, `cwd`, and `hook_event_name`. That means the
same `hooks/agent-phone-hook.sh` script serves both harnesses; the second
argument tags which one is calling so the daemon knows what lives in each
terminal.

Append to `~/.codex/config.toml` (adjust the repo path):

```toml
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "/path/to/agent-phone/hooks/agent-phone-hook.sh user-prompt-submit codex"
timeout = 5
async = true

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = "/path/to/agent-phone/hooks/agent-phone-hook.sh stop codex"
timeout = 5
async = true
```

Codex may ask you to trust/review the hooks the next time it starts —
approve them once. The legacy `notify` setting is untouched (hooks are
additive), so anything already using it keeps working.

## Dictation

Codex has no native terminal dictation (it shipped experimentally in
v0.105, was removed in v0.118, and has not returned; the desktop app got
voice instead). So for Codex terminals the daemon records the handset audio
locally and transcribes it with whisper.cpp:

1. `brew install whisper-cpp`
2. Put a model at `~/.agent-phone/models/ggml-base.en.bin`
   (`curl -L -o ~/.agent-phone/models/ggml-base.en.bin
   https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin`);
   the daemon picks it up automatically, or point `--stt-command` anywhere.
3. Lift the receiver in a Codex terminal, talk, hang up: the transcript is
   pasted into the terminal for review, and Enter submits — same feel as
   the Claude Code flow, just transcribed on your Mac instead of streamed.

The daemon decides per terminal: windows that linked through Claude Code
hooks get push-to-talk into Claude's native dictation; windows that linked
through Codex hooks get record-and-paste. Everything is local for Codex —
audio never leaves the machine.
