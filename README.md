# Agent Phone

<p align="center">
  <img src="assets/pepe-phone.png" alt="Taking the call" width="300">
  <img src="assets/polycom-cx300.webp" alt="Polycom CX300 desk phone" width="380">
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

## The keypad

| Control | Action |
|---|---|
| `#` | bind the focused terminal |
| `*` | next terminal needing attention; browses all bound terminals when quiet |
| `1`–`9` | speed dial: jump straight to the Nth bound terminal |
| `0` | minimize every bound terminal |
| Redial | press Enter in the focused terminal (send the dictated prompt) |
| Hold | press Escape (interrupt a running agent) |
| Delete | clear the input line (Ctrl+U) |
| Receiver | dictate; hang up to finish |
| Mute | hardware-mutes the handset mic mid-dictation |
| Lamp | blinking red: a terminal needs you; steady green: all clear |

The full loop never touches the keyboard: lamp blinks, `*`, read, lift,
talk, hang up, Redial.

## Supported harnesses

Agent Phone works with the major agent harnesses, side by side in the same
session — the daemon detects which one lives in each terminal and adapts:

| | Claude Code | Codex CLI | Hermes |
|---|---|---|---|
| Lamp on turn finished | `Stop` hook | `Stop` hook | `post_llm_call` + settle debounce |
| Session linking | `UserPromptSubmit` hook | `UserPromptSubmit` hook | `pre_llm_call` / `pre_tool_call` |
| Receiver dictation | native dictation (push-to-talk held for you) | local whisper.cpp, pasted for review | local whisper.cpp, pasted for review |
| Setup | [docs/claude-code-setup.md](docs/claude-code-setup.md) | [docs/codex-setup.md](docs/codex-setup.md) | [docs/hermes-setup.md](docs/hermes-setup.md) |

All three pass hook payloads as JSON on stdin with the same core fields, so
one tiny hook script serves them all. Which harness runs in a terminal is
detected from its tty's process list at bind time, so dictating your very
first prompt into a fresh terminal picks the right mode.

## How it works

The daemon (`python -m agent_phone.daemon`) is plain Python. Terminal windows
are tracked and focused through macOS Automation (Terminal.app and iTerm2),
and each agent session announces itself over the two hooks above: *turn
started* and *turn finished*.

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

## CX300 HID protocol notes

The CX300 (USB VID `095d`, PID `9201`) exposes four interfaces: USB Audio
Class 1.0 control/capture/playback (16 kHz, 16-bit, mono — class-compliant,
driverless) and one HID interface carrying two top-level collections:
Telephony (usage page `0x0B`) for input, and a vendor page (`0xFF99`) for the
display and status LED. macOS presents both collections as a single
IOHIDDevice, so one `hid_open(0x095d, 0x9201)` covers everything.

None of this is documented by Polycom: the protocol exists in public because
**Bobbie Smulders** reverse-engineered it (CX300Control, 2018 — his repo and
writeup have since vanished from the live web; archived links are in the
full doc), **probonopd** documented it and built
[OpenPhone](https://github.com/probonopd/OpenPhone),
**OE4AMW** preserved and extended the work in
[cx300-control](https://github.com/OE4AMW/cx300-control), and
**Tomasz Ostrowski** added a tSIP plugin and hardware teardown. This repo
carries the complete protocol — with history, archived sources, and known
unknowns — in [docs/cx300-hid-protocol.md](docs/cx300-hid-protocol.md) so it
can't vanish again. Everything below is verified against this repo's own
phone.

### Input report `0x01` (8 bytes total, interrupt IN)

Byte positions verified live against this repo's phone (the community docs
have flags and keypad swapped due to ambiguous indexing):

| Byte | Meaning |
|---|---|
| 0 | report ID `0x01` |
| 1 | flags: `0x01` off-hook, `0x02` hold/flash, `0x04` redial, `0x08` long-press, `0x10` mute key, `0x20` delete |
| 2 | keypad code: `0x00` none, `0x01`–`0x0A` digits `0`–`9`, `0x0B` `*`, `0x0C` `#` |
| 3 | audio session: `0x00` enabled, `0x03` disabled |
| 4 | active transducer: `0x40` handset, `0x50`/`0x52` speakerphone, `0x60` headset, `0x00` none |
| 5–6 | volume level (handled inside the phone) |
| 7 | mic muted: `0x00`/`0x01` |

Only one key registers at a time; a fast hook-flash arrives as the hold code.

### Output and feature reports (byte 0 is the report ID)

| Report | Bytes | Meaning |
|---|---|---|
| `0x13` output | `[0x13, mode]` | display mode: `0x00` clear, `0x0D` four corners, `0x15` two lines |
| `0x14` output | `[0x14, area, 0x80]` | select write area: `0x01`–`0x04` corners (TL, BL, TR, BR), `0x05` top line, `0x0A` bottom line |
| `0x15` output | `[0x15, flag]` + up to 8 chars UTF-16LE | write text; flag `0x00` = more chunks follow, `0x80` = final chunk |
| `0x16` output | `[0x16, color]` | status LED: `0x01` green, `0x03` red, `0x04` orange-red, `0x05` orange, `0x06` DND pattern, `0x07` off, `0x08` green/orange |
| `0x17` feature | `[0x17, lcid, 0x04, 0x01, 0x02]` | init/keepalive; `lcid` is a Microsoft locale id (`0x09` = English). Must be resent every ~30 s or the phone reverts to its Lync sign-in screen |

Audio needs no initialization and the phone routes handset/speaker/headset
itself, merely reporting the active route. Ringing is host-driven: set an LED
state and play ring audio to the speaker yourself.

The codec for all of this lives in `agent_phone/cx300_protocol.py`; the
device lifecycle (reader thread, keepalive, reconnect) in `agent_phone/cx300.py`.

## Speech to text

Two modes:

The mode below is chosen automatically per terminal; `--voice` sets the
fallback for windows the daemon can't identify.

- **`--voice claude`** (default) — uses Claude Code's own built-in dictation:
  lifting the receiver holds its push-to-talk key, hanging up releases it,
  and the transcript appears in the prompt box for review before you press
  Enter. The handset mic is just the system default input, so no extra STT
  engine is needed. Setup notes in
  [docs/claude-code-setup.md](docs/claude-code-setup.md).
- **`--voice record`** — for terminals without native dictation (e.g. Codex):
  the daemon records the handset audio and shells out to `--stt-command`,
  replacing `{wav}` with the recording path and pasting the transcript from
  stdout into the focused terminal.

```sh
python -m agent_phone.daemon                    # Claude Code dictation
python -m agent_phone.daemon --voice record --stt-command 'whisper-cli -nt -f {wav}'
```

## Development

```sh
uv run --group dev pytest
```

Python 3.11+, stdlib-only runtime so far. 108 tests.

## License

[PolyForm Noncommercial 1.0.0](LICENSE.md): use it, study it, fork it, run
it on your own desk — anything noncommercial. Commercial use of this code
requires a separate license from the author.

The protocol documentation in
[docs/cx300-hid-protocol.md](docs/cx300-hid-protocol.md) is additionally
released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) —
it preserves community reverse-engineering and should stay as free as the
work it builds on.
