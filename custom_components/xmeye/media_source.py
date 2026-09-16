"""Expose XMEye recordings as browsable/playable media sources.

The device has no "list of days with recordings" query — only a time-range
file search — so the day list offered here is a fixed lookback window, not
something read from the device. Picking a day always issues a fresh search.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
    Unresolvable,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import XmeyeConfigEntry, XmeyeCoordinator
from .dvrip import DvripError
from .views import async_generate_recording_url

# The device is only asked about one day at a time; this just bounds how many
# day entries are offered to browse, going backward from today.
DAYS_BACK = 14


async def async_get_media_source(hass: HomeAssistant) -> XmeyeMediaSource:
    """Set up the XMEye media source."""
    return XmeyeMediaSource(hass)


class XmeyeMediaSource(MediaSource):
    """Browse and resolve recordings stored on an XMEye device."""

    name = "XMEye"

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the media source."""
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a recording identifier to a playable proxy URL."""
        parts = (item.identifier or "").split("|")
        if len(parts) != 5 or parts[0] != "FILE":
            raise Unresolvable(f"Unknown media item '{item.identifier}'")
        _, entry_id, filename, start, end = parts
        return PlayMedia(
            async_generate_recording_url(entry_id, filename, start, end),
            "video/mp4",
        )

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse devices, then channels, then days, then recordings."""
        if not item.identifier:
            return self._async_root()

        parts = item.identifier.split("|")
        if parts[0] == "DEVICE":
            _, entry_id = parts
            return self._async_channels(entry_id)
        if parts[0] == "CHANNEL":
            _, entry_id, channel = parts
            return self._async_days(entry_id, int(channel))
        if parts[0] == "DAY":
            _, entry_id, channel, day = parts
            return await self._async_files(entry_id, int(channel), day)

        raise Unresolvable(f"Unknown media item '{item.identifier}'")

    def _async_root(self) -> BrowseMediaSource:
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"DEVICE|{entry.entry_id}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                title=entry.runtime_data.device_name,
                can_play=False,
                can_expand=True,
            )
            for entry in self.hass.config_entries.async_loaded_entries(DOMAIN)
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=None,
            media_class=MediaClass.APP,
            media_content_type=MediaType.PLAYLIST,
            title="XMEye",
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _async_channels(self, entry_id: str) -> BrowseMediaSource:
        coordinator = self._coordinator(entry_id)
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"CHANNEL|{entry_id}|{cam['channel']}",
                media_class=MediaClass.CHANNEL,
                media_content_type=MediaType.PLAYLIST,
                title=cam.get("title") or f"Channel {cam['channel'] + 1}",
                can_play=False,
                can_expand=True,
            )
            for cam in coordinator.data.cameras
            if cam["type"] != "empty"
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"DEVICE|{entry_id}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=coordinator.device_name,
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _async_days(self, entry_id: str, channel: int) -> BrowseMediaSource:
        today = datetime.now()  # noqa: DTZ005 - device time is unknown, host-local is the best guess
        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=f"DAY|{entry_id}|{channel}|{day}",
                media_class=MediaClass.DIRECTORY,
                media_content_type=MediaType.PLAYLIST,
                title=day,
                can_play=False,
                can_expand=True,
            )
            for day in (
                (today - timedelta(days=offset)).strftime("%Y-%m-%d")
                for offset in range(DAYS_BACK)
            )
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"CHANNEL|{entry_id}|{channel}",
            media_class=MediaClass.CHANNEL,
            media_content_type=MediaType.PLAYLIST,
            title=f"Channel {channel + 1}",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _async_files(
        self, entry_id: str, channel: int, day: str
    ) -> BrowseMediaSource:
        coordinator = self._coordinator(entry_id)
        try:
            client = await coordinator.async_ensure_connected()
            files = await client.search_recordings(
                channel, start=f"{day} 00:00:00", end=f"{day} 23:59:59"
            )
        except DvripError as err:
            raise Unresolvable(f"Could not list recordings: {err}") from err

        children = [
            BrowseMediaSource(
                domain=DOMAIN,
                identifier=(
                    f"FILE|{entry_id}|{file['FileName']}|"
                    f"{file['BeginTime']}|{file['EndTime']}"
                ),
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=f"{file['BeginTime']} - {file['EndTime']}",
                can_play=True,
                can_expand=False,
            )
            for file in files
            if _has_file_fields(file)
        ]
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"DAY|{entry_id}|{channel}|{day}",
            media_class=MediaClass.DIRECTORY,
            media_content_type=MediaType.PLAYLIST,
            title=day,
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _coordinator(self, entry_id: str) -> XmeyeCoordinator:
        entry: XmeyeConfigEntry | None = self.hass.config_entries.async_get_entry(
            entry_id
        )
        if entry is None:
            raise Unresolvable(f"Unknown XMEye device: {entry_id}")
        return entry.runtime_data


def _has_file_fields(file: dict[str, Any]) -> bool:
    """Whether a search_recordings() entry has what's needed to play it back."""
    return bool(file.get("FileName") and file.get("BeginTime") and file.get("EndTime"))
