"""Tests for browsing and resolving XMEye recordings as media sources.

These need Home Assistant importable, so they skip when it is not installed.
No real device or socket is involved — everything is driven through a fake
coordinator and a fake config-entries registry.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.xmeye.dvrip import DvripError
from custom_components.xmeye.media_source import XmeyeMediaSource
from homeassistant.components.media_source import MediaSourceItem, Unresolvable

CAMERAS = [
    {"channel": 0, "title": "Driveway", "type": "ip"},
    {"channel": 1, "title": "", "type": "empty"},
]

RECORDING = {
    "FileName": "/idea0/2024-01-01/001/main.h264",
    "BeginTime": "2024-01-01 10:00:00",
    "EndTime": "2024-01-01 10:10:00",
}


class FakeData:
    def __init__(self, cameras: list[dict[str, Any]]) -> None:
        self.cameras = cameras


class FakeClient:
    def __init__(self, files: list[dict[str, Any]]) -> None:
        self.files = files
        self.fail: DvripError | None = None
        self.calls: list[tuple[int, str, str]] = []

    async def search_recordings(
        self, channel: int, start: str, end: str
    ) -> list[dict[str, Any]]:
        self.calls.append((channel, start, end))
        if self.fail is not None:
            raise self.fail
        return self.files


class FakeCoordinator:
    device_name = "NVR"

    def __init__(
        self, cameras: list[dict[str, Any]], files: list[dict[str, Any]]
    ) -> None:
        self.data = FakeData(cameras)
        self.client = FakeClient(files)

    async def async_ensure_connected(self) -> FakeClient:
        return self.client


class FakeEntry:
    def __init__(self, entry_id: str, coordinator: FakeCoordinator) -> None:
        self.entry_id = entry_id
        self.runtime_data = coordinator


class FakeConfigEntries:
    def __init__(self, entries: list[FakeEntry]) -> None:
        self._entries = {entry.entry_id: entry for entry in entries}

    def async_get_entry(self, entry_id: str) -> FakeEntry | None:
        return self._entries.get(entry_id)

    def async_loaded_entries(self, domain: str) -> list[FakeEntry]:
        return list(self._entries.values())


class FakeHass:
    def __init__(self, entries: list[FakeEntry]) -> None:
        self.config_entries = FakeConfigEntries(entries)


def make_source(
    files: list[dict[str, Any]] | None = None,
) -> tuple[XmeyeMediaSource, FakeCoordinator]:
    """Build a media source wired to one fake device with one camera."""
    coordinator = FakeCoordinator(CAMERAS, files if files is not None else [RECORDING])
    hass = FakeHass([FakeEntry("entry123", coordinator)])
    return XmeyeMediaSource(hass), coordinator


def item(identifier: str | None) -> MediaSourceItem:
    """Build a MediaSourceItem carrying only what browse/resolve use."""
    return MediaSourceItem(
        hass=None, domain="xmeye", identifier=identifier, target_media_player=None
    )


async def test_root_lists_devices() -> None:
    source, _ = make_source()
    root = await source.async_browse_media(item(None))
    assert [child.identifier for child in root.children] == ["DEVICE|entry123"]


async def test_device_lists_only_non_empty_channels() -> None:
    source, _ = make_source()
    result = await source.async_browse_media(item("DEVICE|entry123"))
    assert [child.title for child in result.children] == ["Driveway"]


async def test_channel_offers_a_lookback_window_of_days() -> None:
    source, _ = make_source()
    result = await source.async_browse_media(item("CHANNEL|entry123|0"))
    assert len(result.children) == 14


async def test_day_lists_recordings_found_for_that_channel_and_range() -> None:
    source, coordinator = make_source()
    result = await source.async_browse_media(item("DAY|entry123|0|2024-01-01"))

    assert coordinator.client.calls == [
        (0, "2024-01-01 00:00:00", "2024-01-01 23:59:59")
    ]
    assert len(result.children) == 1
    file_item = result.children[0]
    assert file_item.can_play is True
    assert file_item.identifier == (
        "FILE|entry123|/idea0/2024-01-01/001/main.h264|"
        "2024-01-01 10:00:00|2024-01-01 10:10:00"
    )


async def test_day_skips_entries_missing_required_fields() -> None:
    source, _ = make_source(files=[{"FileName": "x"}])
    result = await source.async_browse_media(item("DAY|entry123|0|2024-01-01"))
    assert result.children == []


async def test_day_raises_unresolvable_on_device_error() -> None:
    source, coordinator = make_source()
    coordinator.client.fail = DvripError("Ret 103")
    with pytest.raises(Unresolvable):
        await source.async_browse_media(item("DAY|entry123|0|2024-01-01"))


async def test_resolve_media_builds_a_proxy_url_for_the_recording() -> None:
    source, _ = make_source()
    identifier = (
        "FILE|entry123|/idea0/2024-01-01/001/main.h264|"
        "2024-01-01 10:00:00|2024-01-01 10:10:00"
    )
    played = await source.async_resolve_media(item(identifier))
    assert played.mime_type == "video/mp4"
    assert played.url.startswith("/api/xmeye/recording/entry123/")


async def test_resolve_media_rejects_an_unknown_identifier() -> None:
    source, _ = make_source()
    with pytest.raises(Unresolvable):
        await source.async_resolve_media(item("NOT|A|REAL|ITEM"))
