# The Polycom CX300 HID protocol

*This document is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
— copy it, republish it, build on it, commercially or not, with attribution.
It preserves community reverse-engineering and should stay as free as the
work it builds on, regardless of the license on this repository's code.*

A standalone reference for driving the Polycom CX300 USB desk phone without
Microsoft Lync, and a record of the people who made that possible. The
protocol is not documented by Polycom anywhere public; everything below
exists because a handful of people reverse-engineered it and published what
they found. Some of that original material has already disappeared from the
web once, which is why this document aims to be self-contained rather than a
list of links.

## Provenance

**Bobbie Smulders** did the original reverse engineering of the CX300's
vendor HID protocol and released **CX300Control** on GitHub, with a writeup
at bsmulders.com (April 2018). Both are gone from the live web — the repo
was deleted and the blog is offline — but the Wayback Machine holds
snapshots:

- Blog post: <http://web.archive.org/web/20190917045022/https://bsmulders.com/2018/04/polycom-cx300/>
- Repository: <http://web.archive.org/web/20190720150328/https://github.com/bsmulders/CX300Control>

One practical trick of his survives in gist comments: Skype for Business
**for Mac** writes to the CX300's display from userspace even before
sign-in, which made the protocol observable on a Mac without any kernel
tooling.

**probonopd** (Simon Peter) documented the protocol in the de-facto
reference gist ["Polycom CX300 Linux HID"](https://gist.github.com/probonopd/a93f65560de35ebba095f7c97a68db54)
and built [OpenPhone](https://github.com/probonopd/OpenPhone), which turns a
CX300 into a standalone SIP phone on Linux (Python, hidapi + pjsua) — proof
the phone has a useful life long after Lync.

**OE4AMW** preserved and extended Smulders' work in
[cx300-control](https://github.com/OE4AMW/cx300-control) (Java, hid4java)
after the original repo vanished: firmware 01.10.6.03 quirks, UTF-16 display
text, the voicemail LED, extra LED colors, and udev rules.

**Tomasz Ostrowski (tomek-o)** wrote a Windows driver-plugin for the tSIP
softphone ([tSIP-plugin-PhonePolycomCX300](https://github.com/tomek-o/tSIP-plugin-PhonePolycomCX300))
and published a [hardware teardown](https://tomeko.net/software/SIPclient/Polycom_CX300/):
Atmel AT91SAM7SE256 MCU, TI TLV320AIC33 codec, TPA2013 speaker amp. His
`_doc/notes.txt` contains the only public `HidP_GetCaps` dump of the
report descriptor's structure.

Agent Phone's implementation (`agent_phone/cx300_protocol.py`) was written
against their combined findings and re-verified live on this project's own
CX300 in August 2026: keepalive, display writes, and LED control all
confirmed byte-for-byte.

## Device overview

USB VID `0x095d` (Polycom, Inc.), PID `0x9201`, USB 2.0 bus-powered
(500 mA max). Four interfaces:

| Interface | Class | Function |
|---|---|---|
| 0 | Audio Control (1/1) | control for the audio function |
| 1 | Audio Streaming | capture (microphone side) |
| 2 | Audio Streaming | playback (speaker side) |
| 3 | HID (bcdHID 1.11) | keypad, hook, LED, LCD |

Audio is USB Audio Class 1.0, mono, 16-bit PCM, 16000 Hz only, both
directions — class-compliant, so it enumerates driverless on Linux
(snd-usb-audio) and macOS (AppleUSBAudio). Isochronous endpoints EP1 OUT /
EP2 IN, 32-byte max packets. The HID interface has EP3 IN (interrupt,
8 bytes, bInterval 8) and EP4 OUT (interrupt, 64 bytes); the report
descriptor is 529 bytes.

The descriptor defines two top-level application collections:

- **Telephony** (usage page `0x0B`): input report 0x01, output report 0x02,
  a 64-byte feature report; hook switch, keypad, and call controls.
- **Vendor** (usage page `0xFF99`): output and feature reports 0x13–0x17;
  display, status LED, and initialization.

Windows exposes the two collections as two separate HID paths (open both);
macOS presents the whole interface as a single IOHIDDevice, so one
`hid_open(0x095d, 0x9201)` reaches everything — always put the report ID in
byte 0 of writes and feature reports.

## Input report `0x01` (8 bytes total, including the report ID)

Byte positions below were verified live against this project's CX300
(August 2026) and **differ from the community documentation**, which lists
the keypad code before the flags — an artifact of ambiguous byte indexing
in the original notes. On real hardware the report is 8 bytes total with
the report ID in byte 0:

| Byte | Meaning |
|---|---|
| 0 | report ID `0x01` |
| 1 | flags: `0x01` off-hook, `0x02` hold/flash, `0x04` redial, `0x08` long-press, `0x10` mute key, `0x20` delete |
| 2 | keypad code: `0x00` none, `0x01`–`0x0A` digits `0`–`9` (digit *d* is code *d*+1), `0x0B` `*`, `0x0C` `#` |
| 3 | audio session: `0x00` enabled, `0x03` disabled |
| 4 | active transducer: `0x40` handset, `0x50` (some firmware `0x52`) speakerphone, `0x60` headset, `0x00` none |
| 5–6 | volume level (differs per active transducer) |
| 7 | microphone muted: `0x00` no, `0x01` yes |

Live captures for reference: `01 00 0c 00 00 3c b5 00` is `#` pressed while
on-hook; `01 01 00 00 40 d5 5a 00` is the receiver lifted (off-hook flag
set, handset transducer active). A key press is reported once with the key
code and again with `0x00` on release.

Notes: only one simultaneous keypress registers; a quick hook-flash is
reported as the hold code; the hook switch is a transmissive optical sensor.
Volume and audio routing are handled entirely inside the phone — the host
only observes them.

## Output report `0x02` (telephony collection)

`[0x02, 0x00]` speakerphone LED off, `[0x02, 0x01]` on.

## Vendor collection: reports `0x13`–`0x17`

| Report | Bytes | Meaning |
|---|---|---|
| `0x13` output | `[0x13, mode]` | display mode: `0x00` clear, `0x0D` four-corner layout, `0x15` two-line layout |
| `0x14` output | `[0x14, area, 0x80]` | select write area: `0x01` top-left, `0x02` bottom-left, `0x03` top-right, `0x04` bottom-right, `0x05` top line, `0x0A` bottom line |
| `0x15` output | `[0x15, flag]` + up to 8 chars UTF-16LE | write text to the selected area; flag `0x00` = more chunks follow, `0x80` = final chunk. Non-ASCII works (UTF-16) |
| `0x16` output | `[0x16, color]` | status LED: `0x01` green, `0x03` red, `0x04` orange-red, `0x05` orange, `0x06` DND pattern, `0x07` off, `0x08` green/orange |
| `0x17` feature | `[0x17, lcid, 0x04, 0x01, 0x02]` | initialization and keepalive; `lcid` is a Microsoft locale ID and sets the phone's UI language (`0x09` English) |

A live probe on this project's phone (August 2026) found **no native
flash/blink pattern**: codes `0x02` and `0x09`–`0x0B` did nothing visible,
and `0x06`/`0x08` showed steady output, not blinking. To blink the LED,
toggle `0x16` color/off from the host (~1 Hz works well).

The `0x17` feature report is the only handshake the phone needs, and it
must be **resent roughly every 30 seconds**. Without it the phone abandons
the host and falls back to its built-in "please upgrade Office
Communicator" / sign-in screen. Audio needs no initialization at all.

Ringing is host-driven: there is no autonomous ringer command — set an LED
state and play ring audio out of the speaker yourself.

## Unknowns

- The CX300 **R2** revision has no public descriptor dump; firmware
  differences within the same PID are documented (OE4AMW vs. the original
  work), so treat exact byte values as probe-at-runtime.
- No complete usage-by-usage annotation of the 529-byte report descriptor
  has ever been published — community code parses raw report bytes.
- The digit portion of the keypad table (`0x01` = "0" … `0x0A` = "9")
  follows the community documentation; confirm against your firmware the
  first time you press a key.

## Related family

The CX500/CX600/CX3000 are *not* USB phones — they are standalone Lync
Phone Edition IP phones whose USB port is only for "Better Together" PC
tethering; none of this protocol applies to them. The CX300's actual USB
siblings are the CX100 and CX200.
