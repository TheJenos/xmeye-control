"""Tests for the shared entity base's DeviceInfo construction.

These need Home Assistant importable, so they skip when it is not installed.
No real device or socket is involved.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("homeassistant")

from custom_components.xmeye.entity import XmeyeEntity


class FakeEntry:
    entry_id = "entry123"


class FakeData:
    def __init__(self, system_info: dict[str, Any]) -> None:
        self.system_info = system_info


class FakeCoordinator:
    host = "192.168.1.2"
    device_name = "NVR"

    def __init__(self, system_info: dict[str, Any]) -> None:
        self.config_entry = FakeEntry()
        self.data = FakeData(system_info)


def test_non_string_system_info_fields_are_coerced_to_strings() -> None:
    """The device registry warns on (and will reject) non-string fields.

    Some firmware reports DeviceType etc. as a raw number rather than text.
    """
    coordinator = FakeCoordinator(
        {"DeviceType": 1234, "SoftWareVersion": 5, "HardWare": 6, "SerialNo": 789}
    )
    info = XmeyeEntity(coordinator, "test").device_info

    assert info["model"] == "1234"
    assert info["sw_version"] == "5"
    assert info["hw_version"] == "6"
    assert info["serial_number"] == "789"


def test_missing_system_info_fields_stay_none() -> None:
    """A field the device never reported must stay unset, not become "None"."""
    info = XmeyeEntity(FakeCoordinator({}), "test").device_info

    assert info["model"] is None
    assert info["sw_version"] is None
    assert info["hw_version"] is None
    assert info["serial_number"] is None


def test_already_string_fields_pass_through_unchanged() -> None:
    coordinator = FakeCoordinator({"DeviceType": "NVR8-4080", "SerialNo": "ABC123"})
    info = XmeyeEntity(coordinator, "test").device_info

    assert info["model"] == "NVR8-4080"
    assert info["serial_number"] == "ABC123"
