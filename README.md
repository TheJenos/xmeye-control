# XMEye for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/TheJenos/xmeye-control/actions/workflows/validate.yml/badge.svg)](https://github.com/TheJenos/xmeye-control/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/TheJenos/xmeye-control?display_name=tag&sort=semver)](https://github.com/TheJenos/xmeye-control/releases)

A Home Assistant integration for **Xiongmai** DVRs, NVRs and IP cameras — the
enormous family of cheap recorders sold under **XMEye**, **iCSee**, **Sofia**
and a hundred OEM badges, whose RTSP banner reads `H264DVR` and which listen on
TCP port **34567**.

These devices speak DVRIP, not ONVIF. This integration talks DVRIP directly, so
it works on hardware where the generic ONVIF integration finds nothing.

The headline feature: **your DVR becomes a Home Assistant speaker.** Point
`tts.speak` at it and the device's own speaker talks.

## What you get

| Platform | Entities |
| --- | --- |
| `camera` | One per channel — JPEG snapshots over DVRIP, live video over RTSP |
| `media_player` | The recorder's speaker, **plus one per camera** — all driven over the OPTalk backchannel |
| `binary_sensor` | Per-channel *recording* and *signal* |
| `sensor` | Per-channel bitrate; device uptime; storage total / free / used |
| `button` | Reboot |

Plus services for PTZ, renaming channels, setting the device clock and searching
recordings.

## Installation

### HACS

[![Open your Home Assistant instance and open this repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=TheJenos&repository=xmeye-control&category=integration)

Click the button above, install **XMEye (Xiongmai / Sofia DVRIP)**, then restart
Home Assistant.

Or add it by hand:

1. HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/TheJenos/xmeye-control` as an **Integration**
3. Install **XMEye (Xiongmai / Sofia DVRIP)**, then restart Home Assistant

Either way, finish with
[**Settings → Devices & Services → Add Integration → XMEye**](https://my.home-assistant.io/redirect/config_flow_start/?domain=xmeye).

### Manual

Download `xmeye.zip` from the
[latest release](https://github.com/TheJenos/xmeye-control/releases/latest) and
unzip it into your Home Assistant `config/` directory — it is laid out so the
files land in `config/custom_components/xmeye/`. Then restart Home Assistant.

## Setup

You need the device's IP, the DVRIP port (**34567**, not the web or RTSP port)
and an account on the recorder. `admin` with an empty password is the factory
default — if that still works, change it.

Home Assistant connects once to verify the credentials and reads the serial
number to identify the device.

### Options

| Option | Default | Notes |
| --- | --- | --- |
| Polling interval | 30 s | Also keeps the DVRIP session alive |
| RTSP port | 554 | |
| Stream quality | Sub stream | Main stream is sharper but much heavier |
| RTSP URL template | see below | Only touch this if live video fails |
| Talk channel | -1 | -1 = the recorder's own speaker output |
| Speakers for individual cameras | IP cameras only | Also *every channel in use*, or *none* |
| Hide unused channels | on | Skip channels with no camera attached |

The default RTSP template is Xiongmai's stock path:

```
rtsp://{host}:{rtsp_port}/user={username}&password={password}&channel={channel}&stream={stream}.sdp?
```

`{channel}` is 1-based here, matching the RTSP path (DVRIP itself counts from 0).

## Speaking through the device

Any audio Home Assistant can hand over is transcoded to 8 kHz mono G.711 A-law
and streamed to the speaker in real time.

You get two kinds of speaker entity:

- **`media_player.<device>_speaker`** — the recorder's own audio output.
- **`media_player.<device>_<camera>_speaker`** — one per camera, so you can
  speak through a *single* camera rather than the whole site.

Point TTS at whichever you want:

```yaml
action: tts.speak
target:
  entity_id: tts.google_translate_en_com
data:
  # One camera only — the person at the driveway hears this, nobody else
  media_player_entity_id: media_player.nvr_driveway_speaker
  message: Please leave the package at the door.
```

A doorbell chime, or anything else from a media source:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.nvr_driveway_speaker
data:
  media_content_id: media-source://media_source/local/doorbell.mp3
  media_content_type: music
```

`media_player.media_stop` cuts playback short.

### How audio reaches one camera

Xiongmai hardware offers two routes to a single camera, and which one works is
entirely down to firmware:

| Route | What happens |
| --- | --- |
| `direct` | Home Assistant opens its own session to the camera's IP address and uses its speaker. Reliable wherever the camera is reachable. |
| `channel` | The recorder is asked to route talk to that channel. Only some firmware supports it; the rest answer `Ret 103`. |

You don't have to know which. Both are tried in turn, and whichever worked is
remembered for next time — visible in the entity's `talk_route` attribute. If
neither works, the error names both attempts and why each failed.

For the direct route, credentials come from the camera entry in the recorder's
table, falling back to the recorder's own username and password. Most firmware
masks stored camera passwords on read, so the fallback is the normal case — it
works out of the box for kit sold as a bundle. If a camera has its own separate
password, add that camera to Home Assistant as its own XMEye device.

> **Analog channels have no speaker of their own.** On a DVR, audio out is a
> single physical jack on the recorder, which is why camera speakers default to
> IP cameras only. Set the option to *every channel in use* if you want to try
> per-channel talk on analog channels anyway.

## PTZ

`xmeye.ptz` targets a camera entity; the channel is taken from the entity.

```yaml
action: xmeye.ptz
target:
  entity_id: camera.driveway
data:
  command: DirectionLeft
  step: 4
  duration: 0.5
```

Directional and zoom commands run continuously. Passing `duration` stops the
move automatically; otherwise call `xmeye.ptz_stop` with the same command.

Commands: `DirectionUp` / `Down` / `Left` / `Right` (plus the four diagonals),
`ZoomTile` / `ZoomWide`, `FocusNear` / `FocusFar`, `IrisSmall` / `IrisLarge`,
`SetPreset` / `GotoPreset` / `ClearPreset`, `StartTour` / `StopTour`.

## Other services

```yaml
# Sync the recorder's clock to Home Assistant's
action: xmeye.set_time
data:
  device_id: !input recorder

# Rename a channel on the device itself (0-based)
action: xmeye.set_channel_title
data:
  device_id: !input recorder
  channel: 0
  title: Front door

# List what's on the disk
action: xmeye.search_recordings
data:
  device_id: !input recorder
  channel: 0
  start: "2026-09-13 00:00:00"
  end: "2026-09-13 23:59:59"
response_variable: found
```

## How it works

`dvrip.py` is a standalone asyncio implementation of the protocol, with no Home
Assistant imports — it can be lifted out and used on its own.

Every frame is a 20-byte header (`0xFF`, session id, sequence, command id,
payload length) followed by a JSON body terminated with `\n\0`. Logging in hashes
the password with Xiongmai's "Sofia" scheme: MD5, then each pair of adjacent
bytes summed modulo 62 into an alphanumeric alphabet, giving 8 characters. The
empty password is famously `tlJwpbo6`.

Audio takes a different path: claim the talk channel (`1434`), start it (`1430`),
then stream `1432` frames, each an 8-byte media header followed by 320 A-law
samples — 40 ms of audio — paced in real time so the device's jitter buffer
keeps up.

One TCP session is shared by every entity for a device, with requests serialised
behind a lock, because DVRIP matches replies to requests purely by arrival order.

## Known limits

- **No motion/alarm push.** The device can push alarm events on the same socket;
  this integration polls instead. Alarm entities are the obvious next addition.
- **Uptime units vary.** `DeviceRunTime` is minutes on the firmware this was
  built against, but Xiongmai is not consistent across OEMs.
- **Talk is one-way.** Home Assistant → device only. There is no audio return.
- **One speaker at a time per recorder.** The `channel` route shares the single
  recorder session, so two camera speakers using it will contend. Cameras on the
  `direct` route each get their own session and are independent.
- **Snapshots go over DVRIP**, not HTTP, so they share the control connection
  and are rate-limited by it.

## Development

```bash
uvx --with pytest-asyncio pytest -q     # protocol tests, no HA install needed
pytest -q                               # add homeassistant to also run entity tests
uvx ruff check custom_components tests
uvx ruff format custom_components tests
```

The protocol tests drive a fake DVRIP server, covering framing, the Sofia hash,
channel merging, snapshot reassembly, per-channel talk claims and audio
packetisation without hardware. The entity tests cover speaker route selection
and fallback; they skip themselves when Home Assistant is not installed.

### Cutting a release

Releases are automated. Bump `version` in
[`custom_components/xmeye/manifest.json`](custom_components/xmeye/manifest.json)
and merge to `main` — that is the whole process.

On every push to `main` the [release workflow](.github/workflows/release.yml)
reads that version and looks for a matching `v<version>` tag. If one already
exists nothing happens, so ordinary commits never produce a release. If it does
not, the full validation suite runs, and only once hassfest, HACS, ruff and the
tests are all green does it tag the commit, publish a release with generated
notes, and attach `xmeye.zip`.

Because the check is "does this tag exist" rather than "did this file change",
the workflow is safe to re-run and copes with force pushes and squashed merges.
It can also be triggered by hand from the Actions tab.

## Legacy Node.js library

This project began as a Node.js library and CLI for the same protocol. It now
lives in [`legacy/`](legacy/) — unmaintained, but a useful protocol reference.

## License

MIT
