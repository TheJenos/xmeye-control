"""Tests for the PTZ preset selector.

These need Home Assistant importable, so they skip when it is not installed.
The protocol tests in ``test_dvrip.py`` cover the wire format without it.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.xmeye.dvrip import DvripError
from custom_components.xmeye.select import XmeyePresetSelect
from homeassistant.exceptions import HomeAssistantError

CAMERA = {
    "channel": 0,
    "title": "Driveway",
    "type": "ip",
    "online": True,
    "ip": "192.168.1.50",
}


class FakeEntry:
    entry_id = "entry123"
    data: dict[str, Any] = {}
    options: dict[str, Any] = {}


class FakeData:
    system_info: dict[str, Any] = {}

    def __init__(self, cameras: list[dict[str, Any]]) -> None:
        self.cameras = cameras

    def camera(self, channel: int) -> dict[str, Any]:
        for cam in self.cameras:
            if cam["channel"] == channel:
                return cam
        return {}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []
        self.fail: DvripError | None = None

    async def ptz(
        self, command: str, channel: int, preset: int = -1, **kwargs: Any
    ) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((command, channel, preset))


class FakeCoordinator:
    host = "192.168.1.2"
    device_name = "NVR"

    def __init__(self, cameras: list[dict[str, Any]]) -> None:
        self.config_entry = FakeEntry()
        self.data = FakeData(cameras)
        self.client = FakeClient()

    async def async_ensure_connected(self) -> FakeClient:
        return self.client


def make_select(preset_count: int = 8) -> XmeyePresetSelect:
    """Build a preset selector wired to a fake coordinator.

    The entity is never added to hass, so ``async_write_ha_state`` (which
    requires one) is stubbed out — these tests only care about the DVRIP call
    and the entity's own ``current_option`` bookkeeping.
    """
    coordinator = FakeCoordinator([CAMERA])
    select = XmeyePresetSelect(coordinator, 0, preset_count)
    select.async_write_ha_state = lambda: None
    return select


def test_options_are_numbered_from_one() -> None:
    """The selector offers exactly ``preset_count`` numbered options."""
    select = make_select(4)
    assert select.options == ["1", "2", "3", "4"]


async def test_selecting_an_option_sends_goto_preset() -> None:
    """Picking a preset issues GotoPreset with that number on this channel."""
    select = make_select()
    await select.async_select_option("3")

    assert select.coordinator.client.calls == [("GotoPreset", 0, 3)]
    assert select.current_option == "3"


async def test_a_failed_move_raises_and_leaves_the_option_unchanged() -> None:
    """A device error surfaces to the user and does not fake success."""
    select = make_select()
    select.coordinator.client.fail = DvripError("PTZ GotoPreset failed: Ret 103")

    with pytest.raises(HomeAssistantError):
        await select.async_select_option("2")

    assert select.current_option is None
