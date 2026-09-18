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

# Recorded files commonly span a full hour. A device seen in the wild
# streamed OPPlayBack's "download" at close to the recording's own real-time
# bitrate rather than as a fast bulk copy — so a 10-minute chunk could take
# on the order of 10 minutes to arrive, not seconds. Every file is offered in
# pieces no longer than this so picking one is at least a bounded wait rather
# than downloading (and holding in memory) up to an hour of raw video; see
# dvrip.py's _download_timeout for how long a chunk this size is given.
RECORDING_CHUNK = timedelta(minutes=5)

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


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
                    f"{chunk_start.strftime(TIME_FORMAT)}|"
                    f"{chunk_end.strftime(TIME_FORMAT)}"
                ),
                media_class=MediaClass.VIDEO,
                media_content_type=MediaType.VIDEO,
                title=(
                    f"{chunk_start.strftime('%H:%M:%S')} - "
                    f"{chunk_end.strftime('%H:%M:%S')}"
                ),
                can_play=True,
                can_expand=False,
            )
            for file in files
            for chunk_start, chunk_end in _chunks_for(file)
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


def _chunks_for(file: dict[str, Any]) -> list[tuple[datetime, datetime]]:
    """Split one search_recordings() entry into playable, bounded pieces.

    Returns an empty list for an entry missing the fields needed to play it
    back at all, or whose times don't parse as ``TIME_FORMAT``.
    """
    filename = file.get("FileName")
    begin_raw = file.get("BeginTime")
    end_raw = file.get("EndTime")
    if not filename or not begin_raw or not end_raw:
        return []
    try:
        begin = datetime.strptime(begin_raw, TIME_FORMAT)  # noqa: DTZ007
        end = datetime.strptime(end_raw, TIME_FORMAT)  # noqa: DTZ007
    except ValueError:
        return []
    if end <= begin:
        return []

    chunks: list[tuple[datetime, datetime]] = []
    cursor = begin
    while cursor < end:
        chunk_end = min(cursor + RECORDING_CHUNK, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks
