# The CX300 HID report descriptor, annotated

*Like the [protocol document](cx300-hid-protocol.md), this file is released
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).*

To our knowledge this is the first complete public dump and annotation of
the Polycom CX300's 529-byte HID report descriptor. It was extracted from
the macOS IO registry (`ioreg -rc IOHIDDevice -l -w0`, `ReportDescriptor`
property) from a live CX300 in August 2026, then decoded item by item.

## What the descriptor reveals beyond the community protocol notes

- The input report's flag bits carry official usage names: our "hold" is
  Telephony **Flash** (0x0B/0x21), the "long-press" bit is actually
  **Speed Dial** (0x0B/0x50), "delete" is Button 1, and the mute key is
  **Phone Mute** declared *relative* (momentary). One flag bit (vendor
  usage 0xFF1E) has never been observed firing.
- The keypad is a proper Telephony Key Pad array (usages Phone Key 0
  through Phone Key A, logical 1-13) packed in a nibble - which is why
  digit *d* maps to code *d*+1.
- Output report **0x02** is an LED-page **Off-Hook** indicator we never
  used (most plausibly the speakerphone button light).
- Output report **0x16** (status LED) has a second byte the community
  protocol never sends: four vendor flag bits (0xFF1A, 0xFF1B, 0xFF1F,
  0xFF20) and a *relative* 2-bit field (0xFF1C, logical -1..1).
- Area select (report 0x14) is an array of usages 0x81-0x8A: **ten**
  addressable display areas, of which the known layouts use six.
- The display "mode" byte (report 0x13) is really three flag bits
  (usages 0x26, 0x25, 0xFF10) plus a 4-bit layout selector (0xFF11):
  known layouts are 1 (four corners) and 2 (two lines); values 3-15 are
  unprobed.
- The keepalive (feature 0x17) is a little-endian uint16 plus two bytes:
  the famous `[0x09, 0x04]` is **LCID 0x0409, English (United States)** -
  the "0x09 = English" folklore was the low byte of a Microsoft locale ID.
- Five feature reports nobody documented: 0x03 (63 bytes), 0x04 (7),
  0x0A (46, read-only, vendor usage 0x63), 0x11, 0x12, and 0x18 (33).

## Feature report contents (read from a live phone, firmware unknown)

```
0x11: 5e04 0002            uint16 LE 0x045E plus two bytes - version-shaped
0x12: 02 30 ff             usages 0x35/0x36 = 02 30, then flag bits
0x04: 20 00000000000000
0x18: ff 00...00           first byte 0xFF, rest zero
0x0A: 0a080100012e000000040200f01703040000
      00ec001405000b00ec001406000000ec0014
      0b04040020050008060029               read-only block - capability table?
0x03: 03060104ff83df00 db000000 db00330039 77d600
      da003500 3577d600 d9003400 016fd600
      d8003500 016fd600 d7003200 fc6ed600
      d6003300 fc6ed600 00000000            repeating 8-byte records with
                                            decrementing sequence numbers -
                                            reads like a diagnostic event log
```

Interpretations above the raw hex are hypotheses; the bytes are the facts.

## Probe results (live hardware, August 2026)

- Output 0x02 lights the **red message-waiting LED on the `1` key** (the
  voicemail symbol) — not the speakerphone button as community docs
  guessed.
- 0x16 byte 2, bits 4–5 (usage 0xFF1C, relative −1..1): **hardware mic
  mute** — +1 (`0x10`) mutes, −1 (`0x30`) unmutes, 0 is a no-op. The mute
  button's orange LED shows the true state.
- 0x16 byte 2 flag bits (0xFF1A/1B/1F/20): nothing visible on this
  firmware.

## Open questions

- Do display layout values 3-15 and areas 6-9 render anything?
- What do feature reports 0x03/0x04/0x0A/0x18 encode, and are 0x03/0x04/
  0x18 writable to any effect?
- What fires the never-observed input flag 0xFF1E?

## Full annotated decode

```
Usage Page = Telephony    [050b]
Usage = 0x1 (Phone)    [0901]
Collection (Application)    [a101]
  Collection (Logical)    [a102]
    Report ID = 0x2    [8502]
    Usage Page = LED    [0508]
    Logical Min = 0x0    [1500]
    Logical Max = 0x1    [2501]
    Report Size = 0x1    [7501]
    Report Count = 0x1    [9501]
    Usage = 0x17 (Off-Hook)    [0917]
    Output (Data,Var,Abs)    [9102]
    Report Count = 0x7    [9507]
    Output (Const,Var,Abs)    [9103]
  End Collection ()    [c0]
  Collection (Logical)    [a102]
    Report ID = 0x1    [8501]
    Usage Page = Telephony    [050b]
    Logical Max = 0x1    [2501]
    Report Size = 0x1    [7501]
    Report Count = 0x4    [9504]
    Usage = 0x20 (Hook Switch)    [0920]
    Usage = 0x21 (Flash)    [0921]
    Usage = 0x24 (Redial)    [0924]
    Usage = 0x50 (Speed Dial)    [0950]
    Input (Data,Var,Abs)    [8102]
    Report Count = 0x1    [9501]
    Usage = 0x2f (Phone Mute)    [092f]
    Input (Data,Var,Rel)    [8106]
    Report Count = 0x1    [9501]
    Usage = 0x7 (Programmable Button)    [0907]
    Usage Page = Button    [0509]
    Usage = 0x1    [0901]
    Input (Data,Var,Abs)    [8102]
    Usage Page = Vendor 0xFF99    [0699ff]
    Usage = 0xff1e    [0a1eff]
    Input (Data,Var,Abs)    [8102]
    Report Count = 0x1    [9501]
    Input (Const,Var,Abs)    [8103]
    Usage Page = Telephony    [050b]
    Usage = 0x6 (Telephony Key Pad)    [0906]
    Collection (Logical)    [a102]
      Usage Min = 0xb0 (Phone Key 0)    [19b0]
      Usage Max = 0xbc (Phone Key A)    [29bc]
      Logical Min = 0x1    [1501]
      Logical Max = 0xd    [250d]
      Report Count = 0x1    [9501]
      Report Size = 0x4    [7504]
      Input (Data,Array,Abs)    [8100]
    End Collection ()    [c0]
    Report Size = 0x4    [7504]
    Input (Const,Var,Abs)    [8103]
    Usage Page = Vendor 0xFF99    [0699ff]
    Usage = 0x60    [0960]
    Usage = 0x61    [0961]
    Logical Min = 0x0    [1500]
    Logical Max = 0x1    [2501]
    Report Size = 0x1    [7501]
    Report Count = 0x2    [9502]
    Input (Data,Var,Abs)    [8102]
    Report Count = 0x6    [9506]
    Input (Const,Var,Abs)    [8103]
    Usage = 0x62    [0962]
    Report Size = 0x8    [7508]
    Report Count = 0x4    [9504]
    Input (Data,Var,Abs)    [8102]
  End Collection ()    [c0]
  Report ID = 0x4    [8504]
  Usage Page = 0xa    [050a]
  Collection (Logical)    [a102]
    Logical Min = 0x0    [1500]
    Logical Max = 0xff    [26ff00]
    Report Size = 0x8    [7508]
    Report Count = 0x7    [9507]
    Usage = 0x1    [0901]
    Feature (Data,Var,Abs)    [b102]
  End Collection ()    [c0]
  Report ID = 0x3    [8503]
  Usage Page = 0xa    [050a]
  Collection (Logical)    [a102]
    Logical Min = 0x0    [1500]
    Logical Max = 0xff    [26ff00]
    Report Size = 0x8    [7508]
    Report Count = 0x3f    [953f]
    Usage = 0x1    [0901]
    Feature (Data,Var,Abs)    [b102]
  End Collection ()    [c0]
  Report ID = 0xa    [850a]
  Usage Page = Vendor 0xFF99    [0699ff]
  Collection (Logical)    [a102]
    Usage = 0x63    [0963]
    Logical Min = 0x0    [1500]
    Logical Max = 0xff    [26ff00]
    Report Size = 0x8    [7508]
    Report Count = 0x2e    [952e]
    Feature (Const,Var,Abs)    [b103]
  End Collection ()    [c0]
  Report ID = 0x18    [8518]
  Usage Page = 0xa    [050a]
  Collection (Logical)    [a102]
    Logical Min = 0x0    [1500]
    Logical Max = 0xff    [26ff00]
    Report Size = 0x8    [7508]
    Report Count = 0x21    [9521]
    Usage = 0x1    [0901]
    Feature (Data,Var,Abs)    [b102]
  End Collection ()    [c0]
End Collection ()    [c0]
Usage Page = Vendor 0xFF99    [0699ff]
Usage = 0x1    [0901]
Collection (Application)    [a101]
  Usage = 0xff00    [0a00ff]
  Collection (Logical)    [a102]
    Report ID = 0x11    [8511]
    Logical Min = 0x0    [1500]
    Logical Max = 0xffff    [27ffff0000]
    Report Count = 0x1    [9501]
    Report Size = 0x10    [7510]
    Usage = 0xff01    [0a01ff]
    Feature (Const,Var,Abs)    [b103]
    Logical Max = 0xff    [26ff00]
    Report Count = 0x2    [9502]
    Report Size = 0x8    [7508]
    Usage = 0xff02    [0a02ff]
    Feature (Const,Var,Abs)    [b103]
  End Collection ()    [c0]
  Usage = 0x20    [0920]
  Collection (Logical)    [a102]
    Report ID = 0x12    [8512]
    Usage = 0x35    [0935]
    Usage = 0x36    [0936]
    Logical Min = 0x0    [1500]
    Logical Max = 0xff    [26ff00]
    Report Count = 0x2    [9502]
    Report Size = 0x8    [7508]
    Feature (Const,Var,Abs)    [b103]
    Usage Min = 0x81    [1981]
    Usage Max = 0x8a    [298a]
    Logical Max = 0x1    [2501]
    Report Count = 0xa    [950a]
    Report Size = 0x1    [7501]
    Feature (Const,Var,Abs)    [b103]
    Report Count = 0x1    [9501]
    Report Size = 0x6    [7506]
    Feature (Const,Var,Abs)    [b103]
  End Collection ()    [c0]
  Usage = 0x24    [0924]
  Collection (Logical)    [a102]
    Report ID = 0x13    [8513]
    Usage = 0x26    [0926]
    Usage = 0x25    [0925]
    Usage = 0xff10    [0a10ff]
    Logical Min = 0x0    [1500]
    Logical Max = 0x1    [2501]
    Report Count = 0x3    [9503]
    Report Size = 0x1    [7501]
    Output (Data,Var,Abs)    [9102]
    Usage = 0xff11    [0a11ff]
    Logical Max = 0xf    [250f]
    Report Count = 0x1    [9501]
    Report Size = 0x4    [7504]
    Output (Data,Var,Abs)    [9102]
    Report Size = 0x1    [7501]
    Output (Const,Var,Abs)    [9103]
  End Collection ()    [c0]
  Usage = 0x48    [0948]
  Collection (Logical)    [a102]
    Report ID = 0x14    [8514]
    Usage Min = 0x81    [1981]
    Usage Max = 0x8a    [298a]
    Logical Min = 0x1    [1501]
    Logical Max = 0xa    [250a]
    Report Count = 0x1    [9501]
    Report Size = 0x4    [7504]
    Output (Data,Array,Abs)    [9100]
    Report Count = 0x1    [9501]
    Report Size = 0x4    [7504]
    Output (Const,Var,Abs)    [9103]
    Usage = 0xff23    [0a23ff]
    Logical Min = 0x0    [1500]
    Logical Max = 0x3    [2503]
    Report Count = 0x1    [9501]
    Report Size = 0x2    [7502]
    Output (Data,Var,Abs)    [9102]
    Report Count = 0x1    [9501]
    Report Size = 0x5    [7505]
    Output (Const,Var,Abs)    [9103]
    Usage = 0xff22    [0a22ff]
    Logical Max = 0x1    [2501]
    Report Count = 0x1    [9501]
    Report Size = 0x1    [7501]
    Output (Data,Var,Abs)    [9102]
  End Collection ()    [c0]
  Usage = 0x2b    [092b]
  Collection (Logical)    [a102]
    Report ID = 0x15    [8515]
    Report Count = 0x1    [9501]
    Report Size = 0x7    [7507]
    Output (Const,Var,Abs)    [9103]
    Usage = 0xff24    [0a24ff]
    Logical Min = 0x0    [1500]
    Logical Max = 0x1    [2501]
    Report Count = 0x1    [9501]
    Report Size = 0x1    [7501]
    Output (Data,Var,Abs)    [9102]
    Usage = 0xff2c    [0a2cff]
    Logical Max = 0xffff    [27ffff0000]
    Report Count = 0x8    [9508]
    Report Size = 0x10    [7510]
    Output (Data,Var,Abs)    [9102]
  End Collection ()    [c0]
  Usage = 0xff17    [0a17ff]
  Collection (Logical)    [a102]
    Report ID = 0x16    [8516]
    Usage = 0xff18    [0a18ff]
    Logical Min = 0x0    [1500]
    Logical Max = 0xf    [250f]
    Report Count = 0x1    [9501]
    Report Size = 0x4    [7504]
    Output (Data,Var,Abs)    [9102]
    Report Size = 0x4    [7504]
    Output (Const,Var,Abs)    [9103]
    Usage = 0xff1a    [0a1aff]
    Usage = 0xff1b    [0a1bff]
    Usage = 0xff1f    [0a1fff]
    Usage = 0xff20    [0a20ff]
    Logical Max = 0x1    [2501]
    Report Count = 0x4    [9504]
    Report Size = 0x1    [7501]
    Output (Data,Var,Abs)    [9102]
    Report Count = 0x1    [9501]
    Report Size = 0x2    [7502]
    Logical Min = 0xff    [15ff]
    Logical Max = 0x1    [2501]
    Usage = 0xff1c    [0a1cff]
    Output (Data,Var,Rel,NoPref)    [9126]
    Report Size = 0x2    [7502]
    Output (Const,Var,Abs)    [9103]
  End Collection ()    [c0]
  Collection (Logical)    [a102]
    Report ID = 0x17    [8517]
    Logical Min = 0x0    [1500]
    Logical Max = 0xffff    [27ffff0000]
    Report Count = 0x1    [9501]
    Report Size = 0x10    [7510]
    Usage = 0x64    [0964]
    Feature (Data,Var,Abs)    [b102]
    Logical Max = 0xff    [26ff00]
    Report Count = 0x2    [9502]
    Report Size = 0x8    [7508]
    Usage = 0x65    [0965]
    Feature (Data,Var,Abs)    [b102]
  End Collection ()    [c0]
End Collection ()    [c0]
```

## Raw descriptor (529 bytes)

```
050b0901a101a1028502050815002501750195010917910295079103c0a10285
01050b250175019504092009210924095081029501092f810695010907050909
0181020699ff0a1eff810295018103050b0906a10219b029bc1501250d950175
048100c0750481030699ff096009611500250175019502810295068103096275
0895048102c08504050aa102150026ff00750895070901b102c08503050aa102
150026ff007508953f0901b102c0850a0699ffa1020963150026ff007508952e
b103c08518050aa102150026ff00750895210901b102c0c00699ff0901a1010a
00ffa1028511150027ffff0000950175100a01ffb10326ff00950275080a02ff
b103c00920a102851209350936150026ff0095027508b1031981298a2501950a
7501b10395017506b103c00924a1028513092609250a10ff1500250195037501
91020a11ff250f95017504910275019103c00948a10285141981298a1501250a
9501750491009501750491030a23ff150025039501750291029501750591030a
22ff2501950175019102c0092ba10285159501750791030a24ff150025019501
750191020a2cff27ffff0000950875109102c00a17ffa10285160a18ff150025
0f950175049102750491030a1aff0a1bff0a1fff0a20ff250195047501910295
01750215ff25010a1cff912675029103c0a1028517150027ffff000095017510
0964b10226ff00950275080965b102c0c0```
