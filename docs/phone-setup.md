# VVX 300 setup

One-time configuration of the phone, done entirely from its web UI — no
provisioning server needed. Firmware: run the latest UC Software 5.9.x
(5.9.0 was the last feature release for the VVX 300).

## 1. Web UI basics

Browse to `https://<phone-ip>`, log in as **Admin** (default password `456`).

1. **Change the admin password** (Settings → Change Password). The REST API
   refuses to work while the default password is set (error 4010).
2. Settings → Lines → Line 1:
   - Address: `agentphone`
   - SIP server address: `<your Mac's LAN IP>`, port `5060`, transport `UDPOnly`
   - Label: anything you like

## 2. Import the config fragment

The parameters that matter aren't all exposed as web UI fields. Use
Utilities → Import & Export Configuration to upload this XML (edit the IP
first — it must point at the Mac running the daemon):

```xml
<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<PHONE_CONFIG>
  <ALL
    msg.mwi.1.led="1"
    msg.mwi.1.callBackMode="disabled"
    msg.mwi.1.subscribe=""
    call.autoAnswer.SIP="1"
    call.autoAnswer.micMute="0"
    tone.dtmf.rfc2833Control="1"
    tone.dtmf.viaRtp="1"
    apps.restapi.enabled="1"
    apps.telNotification.URL="http://YOUR_MAC_IP:8489/phone/event"
    apps.telNotification.offhookEvent="1"
    apps.telNotification.onhookEvent="1"
    apps.telNotification.lineRegistrationEvent="1"
    voice.codecPref.G711Mu="1"
    voice.codecPref.G722="0"
    nat.keepalive.interval="0"
  />
</PHONE_CONFIG>
```

What each part buys us:

| Parameter | Why |
|---|---|
| `msg.mwi.1.led=1` | the red LED actually flashes on message-summary NOTIFY (default is screen-only) |
| `msg.mwi.1.callBackMode=disabled`, `subscribe=""` | no voicemail callback attempts, no SUBSCRIBE traffic |
| `call.autoAnswer.SIP=1` | phone silently answers the daemon's INVITE — this is the persistent call that carries DTMF and handset audio |
| `call.autoAnswer.micMute=0` | **critical** — by default auto-answered calls come up mic-muted, which would give the daemon silent audio |
| `tone.dtmf.rfc2833Control/viaRtp` | `#`/`*` presses arrive as RFC 2833 telephone-events in the RTP stream |
| `apps.telNotification.*` | phone POSTs off-hook/on-hook XML events to the daemon, which starts/stops speech capture |
| `voice.codecPref.*` | prefer G.711µ so the daemon never has to decode G.722 |
| `apps.restapi.enabled=1` | optional escape hatch (`https://<phone>/api/v1/...`, Basic auth `Polycom:<admin password>`) |

## 3. Why a persistent call?

On-hook keypresses generate **no network traffic at all** on a VVX — no REST
event, no notification. The only network-observable keypad is DTMF inside an
active RTP session. So the daemon calls the phone once, the phone auto-answers
(silently, mic live, we send silence back), and from then on every `#`/`*`
press reaches the daemon instantly — and lifting the handset gives it your
voice on the same stream. If the call drops, the daemon re-INVITEs a few
seconds later.

## 4. Run the daemon

```sh
uv run python -m agent_phone.daemon --backend sip --stt-command 'whisper-cli -nt -f {wav}'
```

Then wire up Claude Code hooks — see [claude-code-setup.md](claude-code-setup.md).

Quick LED test without the phone bound to anything: register the phone, then
watch the log for `phone registered`; attention marks from a Claude Code Stop
hook toggle the MWI NOTIFY that blinks the LED.
