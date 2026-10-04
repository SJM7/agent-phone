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
locally and pastes the transcript:

1. `brew install whisper-cpp`
2. Put a model at `~/.agent-phone/models/ggml-base.en.bin`
   (`curl -L -o ~/.agent-phone/models/ggml-base.en.bin
   https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin`);
   the daemon picks it up automatically, or point `--stt-command` anywhere.
3. Lift the receiver in a Codex terminal, talk, hang up. The daemon
   records the Polycom CX300 by name and pastes one transcript. Enter, or
   Redial, sends. `--stt whistle` transcribes during the recording instead
   of after hang-up, and falls back to whisper.cpp if that worker fails.

The daemon picks the mode from the harness on that terminal's tty. Live
detection wins over a stale hook tag. Codex always records and pastes.
The headset button records the Mac's current default input and pastes on
the second press, including while Claude Code is frontmost. Codex audio
stays on the machine.
