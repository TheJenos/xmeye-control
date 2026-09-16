"""Camera entities for each XMEye channel."""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import quote

import voluptuous as vol

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_COMMAND,
    ATTR_DURATION,
    ATTR_PRESET,
    ATTR_STEP,
    CONF_RTSP_PORT,
    CONF_RTSP_TEMPLATE,
    CONF_SKIP_EMPTY_CHANNELS,
    CONF_STREAM,
    DEFAULT_RTSP_PORT,
    DEFAULT_RTSP_TEMPLATE,
    DEFAULT_STREAM,
    DEFAULT_USERNAME,
    PTZ_COMMANDS,
    SERVICE_PTZ,
    SERVICE_PTZ_STOP,
)
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .dvrip import DvripError
from .entity import XmeyeChannelEntity

_LOGGER = logging.getLogger(__name__)

PTZ_SCHEMA = {
    vol.Required(ATTR_COMMAND): vol.In(PTZ_COMMANDS),
    vol.Optional(ATTR_STEP, default=5): vol.All(
        vol.Coerce(int), vol.Range(min=1, max=8)
    ),
    vol.Optional(ATTR_PRESET, default=-1): vol.Coerce(int),
    vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(float), vol.Range(min=0, max=30)),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XmeyeConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one camera per channel, plus the PTZ entity services."""
    coordinator = entry.runtime_data
    skip_empty = entry.options.get(CONF_SKIP_EMPTY_CHANNELS, True)
    async_add_entities(
        XmeyeCamera(coordinator, cam["channel"])
        for cam in coordinator.data.cameras
        if not skip_empty or cam["type"] != "empty"
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_PTZ, PTZ_SCHEMA, "async_ptz")
    platform.async_register_entity_service(
        SERVICE_PTZ_STOP,
        {vol.Required(ATTR_COMMAND): vol.In(PTZ_COMMANDS)},
        "async_ptz_stop",
    )


class XmeyeCamera(XmeyeChannelEntity, Camera):
    """A single channel exposed as a camera."""

    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: XmeyeCoordinator, channel: int) -> None:
        """Initialise the camera for ``channel``."""
        XmeyeChannelEntity.__init__(self, coordinator, channel, "camera")
        Camera.__init__(self)
        self._attr_name = self.channel_title

    @property
    def is_recording(self) -> bool:
        """Whether the device is currently recording this channel."""
        return bool(self.channel_data.get("recording"))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose the raw channel details for templates and automations."""
        data = self.channel_data
        return {
            "channel": self._channel,
            "channel_type": data.get("type"),
            "bitrate": data.get("bitrate"),
            "camera_ip": data.get("ip"),
        }

    async def stream_source(self) -> str | None:
        """Build the RTSP URL for this channel from the configured template."""
        entry = self.coordinator.config_entry
        template = entry.options.get(CONF_RTSP_TEMPLATE, DEFAULT_RTSP_TEMPLATE)
        try:
            return template.format(
                host=self.coordinator.host,
                rtsp_port=entry.options.get(CONF_RTSP_PORT, DEFAULT_RTSP_PORT),
                username=quote(entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)),
                password=quote(entry.data.get(CONF_PASSWORD, "")),
                # The RTSP path numbers channels from 1, DVRIP from 0.
                channel=self._channel + 1,
                stream=entry.options.get(CONF_STREAM, DEFAULT_STREAM),
            )
        except (KeyError, IndexError):
            _LOGGER.error(
                "Invalid RTSP template %r — check the integration options",
                template,
            )
            return None

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Grab a JPEG still over DVRIP (OPSNAP)."""
        try:
            client = await self.coordinator.async_ensure_connected()
            return await client.snapshot(self._channel)
        except DvripError as err:
            _LOGGER.debug(
                "%s: snapshot on channel %s failed: %s",
                self.coordinator.host,
                self._channel,
                err,
            )
            return None

    async def async_ptz(
        self,
        command: str,
        step: int = 5,
        preset: int = -1,
        duration: float | None = None,
    ) -> None:
        """Move the camera.

        Directional and zoom commands are continuous: pass ``duration`` to have
        the move stopped automatically, or call ``xmeye.ptz_stop`` yourself.
        """
        try:
            client = await self.coordinator.async_ensure_connected()
            await client.ptz(command, self._channel, step, preset)
            if duration:
                await asyncio.sleep(duration)
                await client.ptz(command, self._channel, step, preset, stop=True)
        except DvripError as err:
            raise HomeAssistantError(
                f"PTZ command {command} failed on {self.entity_id}: {err}"
            ) from err

    async def async_ptz_stop(self, command: str) -> None:
        """Halt a continuous PTZ movement."""
        try:
            client = await self.coordinator.async_ensure_connected()
            await client.ptz(command, self._channel, stop=True)
        except DvripError as err:
            raise HomeAssistantError(
                f"Stopping PTZ {command} failed on {self.entity_id}: {err}"
            ) from err
