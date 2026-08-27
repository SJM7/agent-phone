"""SIP daemon: acts as the VVX 300's registrar and holds the persistent call.

The phone registers to us over UDP. We blink its LED with unsolicited
message-summary NOTIFYs, and we place an auto-answered call to it so DTMF
(#/*) and handset audio flow to the Mac at all times.

Message construction/handling is split into pure functions so it can be
tested without sockets; UdpSipEndpoint is the thin asyncio wiring.
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable

from agent_phone.sip_message import SipMessage, parse_message, serialize, parse_params
from agent_phone import sdp

log = logging.getLogger(__name__)

MWI_BODY_YES = b"Messages-Waiting: yes\r\nVoice-Message: 1/0 (1/0)\r\n"
MWI_BODY_NO = b"Messages-Waiting: no\r\nVoice-Message: 0/0 (0/0)\r\n"


def _gen_tag() -> str:
    return f"{random.getrandbits(48):012x}"


def _gen_branch() -> str:
    return f"z9hG4bK{random.getrandbits(48):012x}"


def _gen_call_id(host: str) -> str:
    return f"{random.getrandbits(64):016x}@{host}"


@dataclass
class Registration:
    aor: str                    # address-of-record from To, e.g. sip:agentphone@10.0.0.5
    contact: str                # contact URI to reach the phone
    addr: tuple[str, int]       # observed UDP source address
    expires: int
    registered_at: float

    def fresh(self, now: float | None = None) -> bool:
        return (now or time.monotonic()) - self.registered_at < self.expires


def response_for(req: SipMessage, code: int, reason: str,
                 to_tag: str | None = None,
                 extra: list[tuple[str, str]] | None = None,
                 body: bytes = b"") -> SipMessage:
    """Build a response echoing the request's dialog headers (RFC 3261 8.2.6)."""
    headers: list[tuple[str, str]] = []
    for via in req.get_all("Via"):
        headers.append(("Via", via))
    to_value = req.get("To") or ""
    if to_tag and ";tag=" not in to_value:
        to_value = f"{to_value};tag={to_tag}"
    headers.append(("From", req.get("From") or ""))
    headers.append(("To", to_value))
    headers.append(("Call-ID", req.get("Call-ID") or ""))
    headers.append(("CSeq", req.get("CSeq") or ""))
    for name, value in extra or []:
        headers.append((name, value))
    return SipMessage(False, None, None, code, reason, "SIP/2.0", headers, body)


def registration_from(req: SipMessage, addr: tuple[str, int]) -> Registration | None:
    contact = req.get("Contact")
    if contact is None:
        return None
    base, params = parse_params(contact)
    expires = req.get("Expires")
    if params.get("expires"):
        expires = params["expires"]
    to_base, _ = parse_params(req.get("To") or "")
    return Registration(
        aor=to_base.strip("<>"),
        contact=base.strip("<>"),
        addr=addr,
        expires=int(expires) if expires and expires.isdigit() else 3600,
        registered_at=time.monotonic(),
    )


def build_request(method: str, uri: str, local_ip: str, local_port: int,
                  from_uri: str, to_uri: str, call_id: str, cseq: int,
                  from_tag: str, to_tag: str | None = None,
                  extra: list[tuple[str, str]] | None = None,
                  body: bytes = b"") -> SipMessage:
    from_value = f"<{from_uri}>;tag={from_tag}"
    to_value = f"<{to_uri}>" + (f";tag={to_tag}" if to_tag else "")
    headers = [
        ("Via", f"SIP/2.0/UDP {local_ip}:{local_port};branch={_gen_branch()}"),
        ("Max-Forwards", "70"),
        ("From", from_value),
        ("To", to_value),
        ("Call-ID", call_id),
        ("CSeq", f"{cseq} {method}"),
        ("Contact", f"<sip:agentphone@{local_ip}:{local_port}>"),
    ]
    for name, value in extra or []:
        headers.append((name, value))
    return SipMessage(True, method, uri, None, None, "SIP/2.0", headers, body)


def build_mwi_notify(reg: Registration, local_ip: str, local_port: int,
                     waiting: bool, cseq: int, call_id: str) -> SipMessage:
    return build_request(
        "NOTIFY", reg.contact, local_ip, local_port,
        from_uri=f"sip:agentphone@{local_ip}", to_uri=reg.aor,
        call_id=call_id, cseq=cseq, from_tag=_gen_tag(),
        extra=[
            ("Event", "message-summary"),
            ("Subscription-State", "active"),
            ("Content-Type", "application/simple-message-summary"),
        ],
        body=MWI_BODY_YES if waiting else MWI_BODY_NO,
    )


@dataclass
class CallState:
    call_id: str
    from_tag: str
    to_tag: str | None = None
    cseq: int = 1
    established: bool = False
    remote_rtp: tuple[str, int] | None = None
    dtmf_pt: int | None = None
    audio_pt: int | None = None
    remote_contact: str | None = None


def build_invite(reg: Registration, local_ip: str, local_port: int,
                 rtp_port: int) -> tuple[SipMessage, CallState]:
    call = CallState(call_id=_gen_call_id(local_ip), from_tag=_gen_tag())
    offer = sdp.build_offer(int(time.time()), local_ip, rtp_port).encode()
    msg = build_request(
        "INVITE", reg.contact, local_ip, local_port,
        from_uri=f"sip:agentphone@{local_ip}", to_uri=reg.aor,
        call_id=call.call_id, cseq=call.cseq, from_tag=call.from_tag,
        extra=[
            ("Content-Type", "application/sdp"),
            ("Alert-Info", "info=alert-autoanswer;delay=0"),
        ],
        body=offer,
    )
    return msg, call


def build_ack(call: CallState, uri: str, local_ip: str, local_port: int) -> SipMessage:
    return build_request(
        "ACK", uri, local_ip, local_port,
        from_uri=f"sip:agentphone@{local_ip}", to_uri=uri,
        call_id=call.call_id, cseq=call.cseq, from_tag=call.from_tag,
        to_tag=call.to_tag,
    )


def apply_invite_response(call: CallState, resp: SipMessage) -> bool:
    """Update call state from a final 2xx response; returns True when established."""
    if resp.status_code is None or not 200 <= resp.status_code < 300:
        return False
    to_value = resp.get("To") or ""
    _, to_params = parse_params(to_value)
    call.to_tag = to_params.get("tag")
    contact = resp.get("Contact")
    if contact:
        base, _ = parse_params(contact)
        call.remote_contact = base.strip("<>")
    if (resp.get("Content-Type") or "").strip().lower() == "application/sdp":
        answer = sdp.parse_sdp(resp.body.decode("latin-1", "replace"))
        if answer.address:
            call.remote_rtp = (answer.address, answer.port)
        call.dtmf_pt = sdp.find_payload(answer, "telephone-event")
        call.audio_pt = sdp.find_payload(answer, "pcmu")
    call.established = True
    return True


class UdpSipEndpoint(asyncio.DatagramProtocol):
    """Registrar + single-call UA over one UDP socket.

    Callbacks:
      on_registered(Registration)  — phone (re-)registered
      on_call_established(CallState) — persistent call is up
      on_call_down() — call ended (BYE or error); caller decides on redial
    """

    def __init__(self, local_ip: str, local_port: int = 5060,
                 on_registered: Callable[[Registration], None] | None = None,
                 on_call_established: Callable[[CallState], None] | None = None,
                 on_call_down: Callable[[], None] | None = None) -> None:
        self.local_ip = local_ip
        self.local_port = local_port
        self.on_registered = on_registered
        self.on_call_established = on_call_established
        self.on_call_down = on_call_down
        self.registration: Registration | None = None
        self.call: CallState | None = None
        self._pending_invite: SipMessage | None = None
        self._notify_cseq = 1
        self.transport: asyncio.DatagramTransport | None = None

    # -- asyncio plumbing ---------------------------------------------------
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            msg = parse_message(data)
        except ValueError as exc:
            log.debug("dropping unparseable datagram from %s: %s", addr, exc)
            return
        try:
            if msg.is_request:
                self._handle_request(msg, addr)
            else:
                self._handle_response(msg, addr)
        except Exception:
            log.exception("error handling SIP message from %s", addr)

    def _send(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        assert self.transport is not None
        self.transport.sendto(serialize(msg), addr)

    # -- inbound requests ---------------------------------------------------
    def _handle_request(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        method = msg.method or ""
        if method == "REGISTER":
            reg = registration_from(msg, addr)
            if reg is None:
                self._send(response_for(msg, 400, "Bad Request"), addr)
                return
            self.registration = reg
            extra = [("Contact", msg.get("Contact") or ""),
                     ("Expires", str(reg.expires))]
            self._send(response_for(msg, 200, "OK", extra=extra), addr)
            log.info("phone registered: %s at %s (expires %ds)",
                     reg.contact, addr, reg.expires)
            if self.on_registered:
                self.on_registered(reg)
        elif method == "OPTIONS":
            self._send(response_for(msg, 200, "OK", to_tag=_gen_tag()), addr)
        elif method == "BYE":
            self._send(response_for(msg, 200, "OK"), addr)
            if self.call and msg.get("Call-ID") == self.call.call_id:
                log.info("phone ended the call")
                self.call = None
                if self.on_call_down:
                    self.on_call_down()
        elif method == "SUBSCRIBE":
            # We don't serve subscriptions; a terminated state stops retries.
            self._send(response_for(msg, 200, "OK", to_tag=_gen_tag(),
                                    extra=[("Expires", "0")]), addr)
        elif method in ("NOTIFY", "INFO", "MESSAGE"):
            self._send(response_for(msg, 200, "OK"), addr)
        else:
            self._send(response_for(msg, 501, "Not Implemented"), addr)

    # -- outbound call ------------------------------------------------------
    def place_call(self, rtp_port: int) -> None:
        if self.registration is None:
            log.warning("cannot place call: phone not registered")
            return
        invite, call = build_invite(self.registration, self.local_ip,
                                    self.local_port, rtp_port)
        self.call = call
        self._pending_invite = invite
        self._send(invite, self.registration.addr)
        log.info("INVITE sent to %s", self.registration.contact)

    def hang_up(self) -> None:
        if self.call is None or not self.call.established or self.registration is None:
            self.call = None
            return
        call = self.call
        call.cseq += 1
        bye = build_request(
            "BYE", call.remote_contact or self.registration.contact,
            self.local_ip, self.local_port,
            from_uri=f"sip:agentphone@{self.local_ip}",
            to_uri=self.registration.aor,
            call_id=call.call_id, cseq=call.cseq,
            from_tag=call.from_tag, to_tag=call.to_tag,
        )
        self._send(bye, self.registration.addr)
        self.call = None

    def _handle_response(self, msg: SipMessage, addr: tuple[str, int]) -> None:
        call = self.call
        if call is None or msg.get("Call-ID") != call.call_id:
            return
        cseq = msg.get("CSeq") or ""
        if not cseq.endswith("INVITE"):
            return
        code = msg.status_code or 0
        if code < 200:
            return  # provisional
        if 200 <= code < 300:
            was_established = call.established
            apply_invite_response(call, msg)
            ack_uri = call.remote_contact or (
                self.registration.contact if self.registration else "")
            self._send(build_ack(call, ack_uri, self.local_ip, self.local_port), addr)
            if not was_established:
                log.info("call established; remote RTP %s, dtmf pt %s",
                         call.remote_rtp, call.dtmf_pt)
                if self.on_call_established:
                    self.on_call_established(call)
        else:
            log.warning("INVITE rejected: %d %s", code, msg.reason)
            self._send(build_ack(call, self.registration.contact
                                 if self.registration else "", self.local_ip,
                                 self.local_port), addr)
            self.call = None
            if self.on_call_down:
                self.on_call_down()

    # -- LED ----------------------------------------------------------------
    def set_mwi(self, waiting: bool) -> None:
        if self.registration is None:
            log.warning("cannot set MWI: phone not registered")
            return
        self._notify_cseq += 1
        notify = build_mwi_notify(
            self.registration, self.local_ip, self.local_port, waiting,
            cseq=self._notify_cseq, call_id=_gen_call_id(self.local_ip))
        self._send(notify, self.registration.addr)
        log.info("MWI %s", "on" if waiting else "off")
