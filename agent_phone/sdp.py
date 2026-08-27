"""Minimal SDP (RFC 4566 subset): just enough to offer PCMU + telephone-event
and read the phone's answer."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SdpAudio:
    address: str | None
    port: int
    payloads: tuple[int, ...]
    rtpmap: dict[int, str]


def _c_address(value: str) -> str | None:
    parts = value.split()
    return parts[2] if len(parts) >= 3 else None


def parse_sdp(text: str) -> SdpAudio:
    session_addr: str | None = None
    media_addr: str | None = None
    port: int | None = None
    payloads: tuple[int, ...] = ()
    rtpmap: dict[int, str] = {}
    section = "session"          # session | audio | other

    for line in text.replace("\r\n", "\n").split("\n"):
        if not line or len(line) < 2 or line[1] != "=":
            continue
        kind, value = line[0], line[2:]
        if kind == "m":
            if section == "audio":
                section = "other"    # first audio section already captured
                continue
            fields = value.split()
            if len(fields) >= 3 and fields[0] == "audio":
                if not fields[1].isdigit():
                    raise ValueError(f"bad m= port: {fields[1]!r}")
                pts = []
                for tok in fields[3:]:
                    if not tok.isdigit():
                        raise ValueError(f"bad m= payload type: {tok!r}")
                    pts.append(int(tok))
                port = int(fields[1])
                payloads = tuple(pts)
                section = "audio"
            else:
                section = "other" if section != "audio" else section
        elif kind == "c":
            if section == "session":
                session_addr = _c_address(value)
            elif section == "audio":
                media_addr = _c_address(value)
        elif kind == "a" and section == "audio" and value.startswith("rtpmap:"):
            rest = value[len("rtpmap:"):]
            pt_str, _, codec_clock = rest.partition(" ")
            codec, slash, _ = codec_clock.partition("/")
            if pt_str.isdigit() and slash:
                rtpmap[int(pt_str)] = codec.strip().lower()

    if port is None:
        raise ValueError("no m=audio section")
    return SdpAudio(media_addr or session_addr, port, payloads, rtpmap)


def find_payload(audio: SdpAudio, codec: str) -> int | None:
    wanted = codec.lower()
    for pt in audio.payloads:
        if pt in audio.rtpmap:
            if audio.rtpmap[pt] == wanted:
                return pt
        elif pt == 0 and wanted == "pcmu":
            return pt
    return None


def build_offer(session_id: int, local_ip: str, rtp_port: int,
                dtmf_pt: int = 101) -> str:
    lines = [
        "v=0",
        f"o=agentphone {session_id} {session_id} IN IP4 {local_ip}",
        "s=agentphone",
        f"c=IN IP4 {local_ip}",
        "t=0 0",
        f"m=audio {rtp_port} RTP/AVP 0 {dtmf_pt}",
        "a=rtpmap:0 PCMU/8000",
        f"a=rtpmap:{dtmf_pt} telephone-event/8000",
        f"a=fmtp:{dtmf_pt} 0-15",
        "a=ptime:20",
        "a=sendrecv",
    ]
    return "".join(line + "\r\n" for line in lines)
