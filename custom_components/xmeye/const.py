"""Constants for the XMEye (Xiongmai / Sofia DVRIP) integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "xmeye"

DEFAULT_PORT: Final = 34567
DEFAULT_RTSP_PORT: Final = 554
DEFAULT_USERNAME: Final = "admin"
DEFAULT_SCAN_INTERVAL: Final = 30
DEFAULT_TIMEOUT: Final = 8

CONF_RTSP_PORT: Final = "rtsp_port"
CONF_RTSP_TEMPLATE: Final = "rtsp_template"
CONF_STREAM: Final = "stream"
CONF_TALK_CHANNEL: Final = "talk_channel"
CONF_SKIP_EMPTY_CHANNELS: Final = "skip_empty_channels"
CONF_CAMERA_SPEAKERS: Final = "camera_speakers"
CONF_PTZ_PRESET_COUNT: Final = "ptz_preset_count"

# Which channels get their own speaker entity, so TTS can target one camera.
CAMERA_SPEAKERS_IP: Final = "ip_cameras"
CAMERA_SPEAKERS_ALL: Final = "all"
CAMERA_SPEAKERS_NONE: Final = "none"
DEFAULT_CAMERA_SPEAKERS: Final = CAMERA_SPEAKERS_IP

# The device has no way to report how many presets are actually configured,
# so a preset selector's option list is just this many numbered slots. 0
# disables the selector, for recorders with no PTZ camera attached.
DEFAULT_PTZ_PRESET_COUNT: Final = 8

# How audio reaches one specific camera.
#   direct  — open a session to the camera's own IP and use its speaker
#   channel — ask the recorder to route talk to that channel (OPTalk Channel)
ROUTE_DIRECT: Final = "direct"
ROUTE_CHANNEL: Final = "channel"

# Xiongmai's stock RTSP path. Credentials live in the query string rather than
# the userinfo portion, which is what this firmware family expects.
DEFAULT_RTSP_TEMPLATE: Final = (
    "rtsp://{host}:{rtsp_port}/user={username}&password={password}"
    "&channel={channel}&stream={stream}.sdp?"
)

# Sub-stream (1) is easier on the device than the main stream (0).
DEFAULT_STREAM: Final = 1

# Upper bound on an ffmpeg transcode; a stuck remote URL must not hang playback.
ALAW_TRANSCODE_TIMEOUT: Final = 60

SERVICE_PTZ: Final = "ptz"
SERVICE_PTZ_STOP: Final = "ptz_stop"
SERVICE_SET_TIME: Final = "set_time"
SERVICE_SET_CHANNEL_TITLE: Final = "set_channel_title"
SERVICE_SEARCH_RECORDINGS: Final = "search_recordings"

ATTR_COMMAND: Final = "command"
ATTR_CHANNEL: Final = "channel"
ATTR_STEP: Final = "step"
ATTR_PRESET: Final = "preset"
ATTR_DURATION: Final = "duration"
ATTR_START: Final = "start"
ATTR_END: Final = "end"

PTZ_COMMANDS: Final = [
    "DirectionUp",
    "DirectionDown",
    "DirectionLeft",
    "DirectionRight",
    "DirectionLeftUp",
    "DirectionLeftDown",
    "DirectionRightUp",
    "DirectionRightDown",
    "ZoomTile",
    "ZoomWide",
    "FocusNear",
    "FocusFar",
    "IrisSmall",
    "IrisLarge",
    "SetPreset",
    "GotoPreset",
    "ClearPreset",
    "StartTour",
    "StopTour",
]

MANUFACTURER: Final = "Xiongmai"
