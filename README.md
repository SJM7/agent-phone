# Agent Phone

Turn a Polycom VVX 300 desk phone into a physical control surface for parallel
Claude Code / Codex terminal sessions on a Mac.

- **`#`** — bind the phone to the currently focused terminal (repeat for as many
  terminals as you like, then minimize them).
- **LED blinks red** — a bound terminal finished its LLM turn and needs you.
- **`*`** — bring the next terminal that needs attention to the front; keep
  pressing to cycle through competing terminals.
- **Pick up the receiver** — the handset mic streams to local speech-to-text;
  talk your prompt, hang up, review, press Enter on the keyboard to submit.

## How it works

A small Python daemon on the Mac acts as the phone's SIP registrar. The phone
registers to it directly — no PBX needed.

| Phone action | Mechanism |
|---|---|
| LED blink | unsolicited SIP `NOTIFY` (`Event: message-summary`, `Messages-Waiting: yes`) |
| `#` / `*` keys | DTMF via RTP telephone-events (RFC 4733) |
| Handset audio | G.711 RTP stream from an auto-answered call, fed to local STT |
| Turn-finished signal | Claude Code `Stop` hook POSTs to the daemon's local HTTP endpoint |
| Window focus | AppleScript/JXA against Terminal.app / iTerm2 |

## Status

Early development. Core protocol modules (SIP message handling, RTP/DTMF
decoding) and the macOS focus layer are in place; the daemon that ties them to
the phone is in progress.

## Development

```sh
uv run --group dev pytest
```

## License

MIT
