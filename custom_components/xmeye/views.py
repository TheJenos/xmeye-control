"""HTTP proxy that turns a downloaded recording into browser-playable MP4."""

from __future__ import annotations

import asyncio
from base64 import urlsafe_b64decode, urlsafe_b64encode
from http import HTTPStatus
import logging

from aiohttp import web

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant, callback

from .coordinator import XmeyeCoordinator
from .dvrip import DvripError

_LOGGER = logging.getLogger(__name__)


@callback
def async_generate_recording_url(
    entry_id: str, filename: str, start: str, end: str
) -> str:
    """Build the proxy URL for one recorded file."""
    return XmeyeRecordingView.url.format(
        entry_id=entry_id,
        filename=urlsafe_b64encode(filename.encode()).decode(),
        start=urlsafe_b64encode(start.encode()).decode(),
        end=urlsafe_b64encode(end.encode()).decode(),
    )


class XmeyeRecordingView(HomeAssistantView):
    """Download one recording from the device and remux it to MP4.

    The device answers with its own on-disk format, not something a browser
    can play directly, so this pulls the whole file into memory and pipes it
    through ffmpeg before responding — there is no way to stream this
    incrementally without know the device's exact framing up front.
    """

    requires_auth = True
    url = "/api/xmeye/recording/{entry_id}/{start}/{end}/{filename}"
    name = "api:xmeye:recording"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the view."""
        self.hass = hass

    async def get(
        self,
        request: web.Request,
        entry_id: str,
        start: str,
        end: str,
        filename: str,
    ) -> web.Response:
        """Serve one recording as MP4."""
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None:
            return web.Response(status=HTTPStatus.NOT_FOUND, text="Unknown device")

        coordinator: XmeyeCoordinator = entry.runtime_data
        filename_decoded = urlsafe_b64decode(filename.encode()).decode()
        start_decoded = urlsafe_b64decode(start.encode()).decode()
        end_decoded = urlsafe_b64decode(end.encode()).decode()

        try:
            client = await coordinator.async_ensure_connected()
            raw = await client.download_recording(
                filename_decoded, start_decoded, end_decoded
            )
        except DvripError as err:
            _LOGGER.warning(
                "%s: could not download recording %s: %s",
                coordinator.host,
                filename_decoded,
                err,
            )
            return web.Response(status=HTTPStatus.BAD_GATEWAY, text=str(err))

        try:
            mp4 = await _remux_to_mp4(self.hass, raw)
        except RuntimeError as err:
            _LOGGER.warning(
                "%s: could not remux recording %s: %s",
                coordinator.host,
                filename_decoded,
                err,
            )
            return web.Response(status=HTTPStatus.INTERNAL_SERVER_ERROR, text=str(err))
        return web.Response(body=mp4, content_type="video/mp4")


async def _remux_to_mp4(hass: HomeAssistant, raw: bytes) -> bytes:
    """Repackage a raw H.264 recording into a browser-playable MP4.

    These recorders commonly store channels as a raw H.264 elementary stream
    (the same format their own USB backup feature exports). That has not been
    confirmed against every firmware — a device that stores something else
    will fail here rather than silently produce a broken video.
    """
    binary = get_ffmpeg_manager(hass).binary
    process = await asyncio.create_subprocess_exec(
        binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "h264",
        "-i",
        "pipe:0",
        "-c",
        "copy",
        "-movflags",
        "frag_keyframe+empty_moov",
        "-f",
        "mp4",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(raw)
    if process.returncode != 0 or not stdout:
        message = stderr.decode(errors="replace").strip() or "unknown error"
        raise RuntimeError(f"ffmpeg could not remux the recording: {message}")
    return stdout
