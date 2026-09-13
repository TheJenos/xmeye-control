"""Speaker entities driven over the DVRIP OPTalk backchannel.

This is what makes `tts.speak` work against Xiongmai hardware: any media Home
Assistant can hand over is transcoded to 8 kHz mono G.711 A-law and streamed to
a device speaker in real time.

Two kinds of speaker exist. The device speaker drives the recorder's own audio
output. A camera speaker targets one channel, and reaches it whichever way that
channel actually supports — see :class:`XmeyeCameraSpeaker`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.components import media_source
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
    async_process_play_media_url,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ALAW_TRANSCODE_TIMEOUT,
    CAMERA_SPEAKERS_ALL,
    CAMERA_SPEAKERS_NONE,
    CONF_CAMERA_SPEAKERS,
    CONF_TALK_CHANNEL,
    DEFAULT_CAMERA_SPEAKERS,
    DEFAULT_USERNAME,
    ROUTE_CHANNEL,
    ROUTE_DIRECT,
)
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .dvrip import DEFAULT_PORT, DvripClient, DvripError
from .entity import XmeyeChannelEntity, XmeyeEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the device speaker plus a speaker for each camera."""
    coordinator = entry.runtime_data
    entities: list[MediaPlayerEntity] = [XmeyeDeviceSpeaker(coordinator)]

    mode = entry.options.get(CONF_CAMERA_SPEAKERS, DEFAULT_CAMERA_SPEAKERS)
    if mode != CAMERA_SPEAKERS_NONE:
        entities.extend(
            XmeyeCameraSpeaker(coordinator, cam["channel"])
            for cam in coordinator.data.cameras
            if cam["type"] == "ip"
            or (mode == CAMERA_SPEAKERS_ALL and cam["type"] != "empty")
        )

    async_add_entities(entities)


class XmeyeSpeakerBase(MediaPlayerEntity):
    """Playback machinery shared by every XMEye speaker.

    Subclasses only have to implement :meth:`_async_deliver`, which receives the
    finished A-law samples and is responsible for getting them to a speaker.
    """

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_media_content_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.BROWSE_MEDIA
    )

    coordinator: XmeyeCoordinator

    def _init_playback(self) -> None:
        """Set up the per-entity playback state."""
        self._playing: asyncio.Task[None] | None = None
        self._cancel = asyncio.Event()
        self._current_media: str | None = None

    @property
    def state(self) -> MediaPlayerState:
        """Playing while audio is streaming, otherwise idle."""
        if self._playing is not None and not self._playing.done():
            return MediaPlayerState.PLAYING
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        """The media currently being streamed, if any."""
        return self._current_media

    async def _async_deliver(self, alaw: bytes) -> None:
        """Stream ``alaw`` to whichever speaker this entity represents."""
        raise NotImplementedError

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse Home Assistant's media sources."""
        return await media_source.async_browse_media(
            self.hass,
            media_content_id,
            content_filter=lambda item: item.media_content_type.startswith("audio/"),
        )

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Transcode ``media_id`` and stream it to the speaker."""
        title = media_id
        if media_source.is_media_source_id(media_id):
            resolved = await media_source.async_resolve_media(
                self.hass, media_id, self.entity_id
            )
            media_id = resolved.url
            title = resolved.url

        media_id = async_process_play_media_url(self.hass, media_id)

        await self.async_media_stop()
        alaw = await self._async_transcode(media_id)
        if not alaw:
            raise HomeAssistantError(f"No audio could be decoded from {title}")

        self._current_media = title
        self._cancel = asyncio.Event()
        self._playing = self.hass.async_create_task(self._async_stream(alaw))
        self.async_write_ha_state()

    async def async_media_stop(self) -> None:
        """Stop any audio currently streaming."""
        if self._playing is not None and not self._playing.done():
            self._cancel.set()
            # The streaming task logs its own failures; stopping must not
            # re-raise them at whoever asked for silence.
            with contextlib.suppress(Exception):
                await self._playing
        self._playing = None
        self._current_media = None
        self.async_write_ha_state()

    async def _async_stream(self, alaw: bytes) -> None:
        """Run one delivery to completion, reporting state either way."""
        try:
            await self._async_deliver(alaw)
        except DvripError as err:
            _LOGGER.error("%s: talk playback failed: %s", self.entity_id, err)
        except Exception:
            _LOGGER.exception("%s: talk playback failed", self.entity_id)
        finally:
            self._current_media = None
            self.async_write_ha_state()

    async def _async_send_via(
        self, client: DvripClient, alaw: bytes, channel: int | None
    ) -> None:
        """Open the talk channel on ``client``, push the samples, close it."""
        await client.start_talk(channel)
        try:
            await client.send_alaw(alaw, cancel=self._cancel)
        finally:
            await client.stop_talk()

    async def _async_transcode(self, url: str) -> bytes:
        """Run ffmpeg to produce raw 8 kHz mono A-law samples from ``url``."""
        binary = get_ffmpeg_manager(self.hass).binary
        process = await asyncio.create_subprocess_exec(
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            url,
            "-ar",
            "8000",
            "-ac",
            "1",
            "-f",
            "alaw",
            "pipe:1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), ALAW_TRANSCODE_TIMEOUT
            )
        except TimeoutError as err:
            process.kill()
            await process.wait()
            raise HomeAssistantError(
                f"Timed out transcoding audio after {ALAW_TRANSCODE_TIMEOUT}s"
            ) from err

        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip() or "unknown error"
            raise HomeAssistantError(f"ffmpeg failed to decode the audio: {message}")
        return stdout

    async def async_will_remove_from_hass(self) -> None:
        """Stop playback when the entity goes away."""
        await self.async_media_stop()
        await super().async_will_remove_from_hass()


class XmeyeDeviceSpeaker(XmeyeEntity, XmeyeSpeakerBase):
    """The recorder's own audio output."""

    _attr_translation_key = "speaker"

    def __init__(self, coordinator: XmeyeCoordinator) -> None:
        """Initialise the device speaker."""
        super().__init__(coordinator, "speaker")
        self._init_playback()

    @property
    def _talk_channel(self) -> int | None:
        """The configured talk channel, or None for the device's audio-out."""
        value = self.coordinator.config_entry.options.get(CONF_TALK_CHANNEL)
        return int(value) if value is not None and int(value) >= 0 else None

    async def _async_deliver(self, alaw: bytes) -> None:
        """Send the audio over the shared recorder session."""
        client = await self.coordinator.async_ensure_connected()
        await self._async_send_via(client, alaw, self._talk_channel)


class XmeyeCameraSpeaker(XmeyeChannelEntity, XmeyeSpeakerBase):
    """The speaker of one camera, so TTS can address a single camera.

    Xiongmai hardware offers two ways to reach one camera, and which works
    depends entirely on the firmware:

    * **direct** — open a session to the camera's own IP address and talk to it
      as a standalone device. Reliable wherever the camera is reachable, which
      is the usual case for IP cameras behind an NVR on the same LAN.
    * **channel** — ask the recorder to route talk to that channel. Supported by
      a minority of firmware; the rest answer ``Ret 103``.

    Rather than make the user find out which, both are tried in turn and the one
    that worked is remembered for subsequent playbacks.
    """

    _attr_translation_key = "camera_speaker"

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise the speaker for ``channel``."""
        super().__init__(coordinator, channel, "speaker")
        self._init_playback()
        self._attr_translation_placeholders = {"channel": self.channel_title}
        self._route: str | None = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose which route audio is taking, which aids debugging a lot."""
        return {"channel": self._channel, "talk_route": self._route}

    def _candidate_routes(self) -> list[str]:
        """Routes worth trying, best-known first."""
        routes = []
        if self.channel_data.get("ip"):
            routes.append(ROUTE_DIRECT)
        routes.append(ROUTE_CHANNEL)
        if self._route in routes:
            # Whatever worked last time goes first.
            routes.remove(self._route)
            routes.insert(0, self._route)
        return routes

    async def _async_deliver(self, alaw: bytes) -> None:
        """Try each supported route until one delivers the audio."""
        failures: list[str] = []
        for route in self._candidate_routes():
            try:
                if route == ROUTE_DIRECT:
                    await self._async_deliver_direct(alaw)
                else:
                    await self._async_deliver_via_recorder(alaw)
            except DvripError as err:
                failures.append(f"{route}: {err}")
                if self._route == route:
                    self._route = None  # the remembered route stopped working
                continue
            self._route = route
            return

        raise HomeAssistantError(
            f"Could not play audio on {self.channel_title}. Tried "
            + "; ".join(failures)
        )

    async def _async_deliver_direct(self, alaw: bytes) -> None:
        """Open a session to the camera itself and use its own speaker."""
        camera = self.channel_data
        host = camera.get("ip")
        if not host:
            raise DvripError("the recorder reports no address for this camera")

        entry = self.coordinator.config_entry
        # The recorder masks stored camera passwords on read, so its own
        # credentials are the usual fallback — kit sold as a bundle shares them.
        client = DvripClient(
            host=host,
            port=camera.get("port") or DEFAULT_PORT,
            username=camera.get("username")
            or entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
            password=camera.get("password") or entry.data.get(CONF_PASSWORD, ""),
        )
        try:
            await client.connect()
            await client.login()
            await self._async_send_via(client, alaw, None)
        finally:
            await client.close()

    async def _async_deliver_via_recorder(self, alaw: bytes) -> None:
        """Ask the recorder to route talk to this channel."""
        client = await self.coordinator.async_ensure_connected()
        await self._async_send_via(client, alaw, self._channel)
