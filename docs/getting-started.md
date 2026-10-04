# Getting started

Everything needed to go from a phone in a shipping box to dictating at your
agents. Written for macOS with a Polycom CX300; for VVX-series network
phones, do this guide first and then [phone-setup.md](phone-setup.md) for
the SIP side.

## 1. Prerequisites

```sh
brew install hidapi ffmpeg          # HID access + audio capture
brew install whisper-cpp            # default local speech-to-text; Whistle falls back to it
mkdir -p ~/.agent-phone/models
curl -L -o ~/.agent-phone/models/ggml-base.en.bin \
  https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin
```

Python 3.11+ via [uv](https://docs.astral.sh/uv/). Plug the phone into USB.
macOS enumerates its audio with no drivers. A handset recording opens the
device named Polycom CX300, so that device does not have to be the system
default. Claude's and Grok's native dictation record the system default:
set Input to the phone when you want the handset mic on that path. The
headset button records whatever is already the default.

## 2. Run the daemon

```sh
git clone https://github.com/SJM7/agent-phone.git && cd agent-phone
uv sync
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run python -m agent_phone.daemon -v
```

The phone's screen shows the dashboard and the lamp turns green when the
daemon connects. Leave it running (a terminal tab, tmux, or a launchd
agent).

Whistle is optional. Pass `--stt whistle` and it transcribes while you
talk and pastes once at the end. That covers the headset button, and the
handset on Codex, Hermes, and Grok.

```sh
python3 -m venv ~/.agent-phone/whistle-venv
~/.agent-phone/whistle-venv/bin/pip install 'cactus-needle==3.1.0'
DYLD_LIBRARY_PATH=/opt/homebrew/lib uv run python -m agent_phone.daemon -v --stt whistle
```

The daemon looks for `~/.agent-phone/whistle-venv/bin/python` unless you
pass `--whistle-python`. If the worker fails, it falls back to whisper.cpp.

## 3. macOS permissions

Three one-time grants for the terminal app that runs the daemon, all under
System Settings, Privacy & Security:

| Permission | Why | When prompted |
|---|---|---|
| Automation | window focus/minimize via AppleScript | first `#` or `*` press |
| Accessibility | push-to-talk and Redial/Hold/Delete keystrokes | add manually if key events fail with error 1002 |
| Microphone | dictation capture | first recording |

## 4. Hook up your agents

Each harness tells the daemon when a turn starts and ends through the same
tiny script, `hooks/agent-phone-hook.sh`. Configure the ones you use:

### Claude Code

Add to the `hooks` section of `~/.claude/settings.json` (full JSON in
[claude-code-setup.md](claude-code-setup.md)): `Stop` and `UserPromptSubmit`
entries running `agent-phone-hook.sh stop` / `agent-phone-hook.sh
user-prompt-submit`. Then run `/voice hold` once inside Claude Code to
enable its native dictation.

### Grok Build

Drop `~/.grok/hooks/agent-phone.json` (exact JSON in
[grok-setup.md](grok-setup.md)) and set `[ui] voice_capture_mode = "hold"`
in `~/.grok/config.toml`. By default the daemon holds Grok's `F8`
push-to-talk and xAI does the transcription. `--stt whistle` skips F8
and pastes a local transcript instead.

### Codex CLI

Append the `[[hooks.Stop]]` / `[[hooks.UserPromptSubmit]]` tables to
`~/.codex/config.toml` (exact TOML in [codex-setup.md](codex-setup.md)) and
approve the hooks when Codex prompts.

### Hermes

Append the `hooks:` block to `~/.hermes/config.yaml` (exact YAML in
[hermes-setup.md](hermes-setup.md)) and approve once with
`hermes --accept-hooks`. Hermes has no single turn-end event, so the daemon
infers it from a quiet gap after the last LLM call.

## 5. Drive

Open agent terminals, then from the phone:

1. Focus a terminal, press `#` to bind it. Repeat for each. Press `0` to
   sweep them all into the Dock.
2. Lamp blinks red: someone finished. Press `*` (or the terminal's number)
   to bring it up and read.
3. Dictate one of two ways. Lift the receiver, speak, hang up: that uses
   the phone mic. Claude Code and default Grok Build terminals stream
   through native dictation; Codex, Hermes, and Grok with `--stt whistle`
   get a local transcript pasted in. Or press Headset to record the Mac's
   current microphone, and press it again to paste. The phone lights that
   button's lamp while the route is active. Review, then press Redial (or
   Enter) to send.
4. Hold interrupts a runaway agent; Delete clears a bad transcript.

The full keypad reference is in the [README](../README.md#the-keypad).

## Troubleshooting

- **Phone screen stuck on a sign-in message**: the daemon isn't running or
  lost the device; restart it. The screen repaints within seconds.
- **`#`/`*` do nothing**: grant Automation permission; check the daemon log.
- **Space characters typed into a terminal on receiver lift**: the daemon
  misidentified the harness — check `ps -t <tty>` shows the agent process,
  and the daemon log for `detected ...` lines.
- **Dictation pastes nothing**: a handset recording needs the input named
  Polycom CX300, not the system default. The headset button records the
  current default input. The default engine is whisper.cpp
  (`~/.agent-phone/models/ggml-base.en.bin`). `--stt whistle` uses
  `~/.agent-phone/whistle-venv` and falls back to that model if the worker
  fails.
- **Hooks silent**: `curl http://127.0.0.1:8489/health` should return
  `{"ok": true}`; each harness has its own hook-listing command
  (`/hooks` in Claude Code or Grok Build, `hermes hooks doctor`).
