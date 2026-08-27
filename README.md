# Agent Phone

<p align="center">
  <img src="assets/polycom-cx300.webp" alt="Polycom CX300 desk phone" width="420">
</p>

**A desk phone as a control surface for your AI coding agents.**

You're running four Claude Code sessions in four terminals. They're all off
thinking. You minimized them. Which one needs you? The one that made the desk
phone's light blink.

- **`#`** — bind the phone to the currently focused terminal. Repeat for every
  Claude Code / Codex session you're juggling, then minimize them all.
- **LED blinks** — a bound terminal finished its LLM turn and is waiting on you.
- **`*`** — bring the terminal that needs attention to the front. More than one
  waiting? Keep pressing `*` to cycle through them.
- **Pick up the receiver** — the handset mic becomes your prompt box. Talk,
  hang up, and the transcript lands in the focused terminal. Read it over,
  press Enter to send.

No PBX, no cloud telephony, no browser extension. One daemon on your Mac and
a $20-on-eBay desk phone.

## How it works

The daemon (`python -m agent_phone.daemon`) is plain Python. Terminal windows
are tracked and focused through macOS Automation (Terminal.app and iTerm2),
and each Claude Code session announces itself over two tiny
[hooks](docs/claude-code-setup.md): *turn started* (`UserPromptSubmit`) and
*turn finished* (`Stop`).

The phone side is pluggable, because "old desk phone" comes in two species:

### USB phones — Polycom CX300 (this repo's reference hardware)

The CX300 was built to be Microsoft Lync's sidecar, which makes it perfect for
this job: it's not a network phone at all, just a USB composite device —
a mono 16 kHz speaker/mic pair plus HID for the keypad, hook switch, and
indicator light. macOS enumerates the audio with zero drivers. The daemon
reads keypad and hook events over HID and captures dictation straight from
CoreAudio.

### SIP phones — Polycom VVX series

For network phones the daemon embeds a minimal SIP registrar the phone
registers to directly. The LED is driven by unsolicited message-summary
NOTIFYs, and because on-hook keypresses are invisible on the network, the
daemon holds a persistent auto-answered call so `#`/`*` arrive as RFC 2833
DTMF and handset audio streams as G.711. Setup: [docs/phone-setup.md](docs/phone-setup.md).

## Speech to text

Bring your own engine: the daemon shells out to `--stt-command`, replacing
`{wav}` with the recording path and reading the transcript from stdout.

```sh
python -m agent_phone.daemon --stt-command 'whisper-cli -nt -f {wav}'
```

## Development

```sh
uv run --group dev pytest
```

Python 3.11+, stdlib-only runtime so far. 108 tests.

## License

MIT
