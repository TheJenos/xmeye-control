"""Tests for channel-scoped entities going unavailable when disconnected.

These need Home Assistant importable, so they skip when it is not installed.
No real device or socket is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.xmeye.binary_sensor import XmeyeOnlineSensor
from custom_components.xmeye.select import XmeyePresetSelect

ONLINE_CAMERA = {"channel": 0, "title": "Driveway", "type": "ip", "online": True}
OFFLINE_CAMERA = {"channel": 1, "title": "Garage", "type": "ip", "online": False}


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


class FakeCoordinator:
    host = "192.168.1.2"
    device_name = "NVR"

    def __init__(self, *, last_update_success: bool = True) -> None:
        self.config_entry = FakeEntry()
        self.data = FakeData([ONLINE_CAMERA, OFFLINE_CAMERA])
        self.last_update_success = last_update_success


def test_channel_entity_is_available_when_online() -> None:
    select = XmeyePresetSelect(FakeCoordinator(), channel=0, preset_count=4)
    assert select.available is True


def test_channel_entity_is_unavailable_when_offline() -> None:
    select = XmeyePresetSelect(FakeCoordinator(), channel=1, preset_count=4)
    assert select.available is False


def test_channel_entity_is_unavailable_when_the_coordinator_poll_failed() -> None:
    select = XmeyePresetSelect(
        FakeCoordinator(last_update_success=False), channel=0, preset_count=4
    )
    assert select.available is False


def test_online_sensor_stays_available_when_offline() -> None:
    """The connectivity sensor must not hide the very state it reports."""
    sensor = XmeyeOnlineSensor(FakeCoordinator(), channel=1)
    assert sensor.available is True
    assert sensor.is_on is False


def test_online_sensor_still_respects_a_failed_poll() -> None:
    sensor = XmeyeOnlineSensor(FakeCoordinator(last_update_success=False), channel=0)
    assert sensor.available is False
